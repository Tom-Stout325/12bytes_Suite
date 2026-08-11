from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum

from django.db.models import QuerySet

from flightlogs.models import FlightLog


TIME_TOLERANCE = timedelta(seconds=60)
LOCATION_TOLERANCE_METERS = 100.0
DURATION_TOLERANCE_SECONDS = 30.0
DURATION_TOLERANCE_RATIO = 0.05
METRIC_TOLERANCE_RATIO = 0.10
LOCATION_VARIANCE_TOLERANCE_METERS = 500.0
LOCATION_VARIANCE_METRIC_TOLERANCE_RATIO = 0.01
PARTIAL_AIRDATA_MAX_RATIO = 0.90


class MatchType(StrEnum):
    HIGH_CONFIDENCE = "high_confidence"
    HIGH_CONFIDENCE_LOCATION_VARIANCE = "high_confidence_location_variance"
    REVIEW_PARTIAL_AIRDATA = "review_partial_airdata"
    PROBABLE = "probable"
    AMBIGUOUS = "ambiguous"
    NO_MATCH = "no_match"


@dataclass(frozen=True)
class FlightMatchResult:
    match_type: MatchType
    matched_flight: FlightLog | None = None
    confidence: str = "none"
    reasons: tuple[str, ...] = ()
    field_differences: dict[str, float | str] = field(default_factory=dict)


def _coordinates(value):
    try:
        latitude_text, longitude_text = (part.strip() for part in value.split(",", 1))
        latitude, longitude = float(latitude_text), float(longitude_text)
    except (AttributeError, TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def _distance_meters(first, second):
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    haversine = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_000 * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


def _numeric_agreement(existing, incoming, ratio=METRIC_TOLERANCE_RATIO):
    if existing is None or incoming is None:
        return None, None
    difference = abs(float(existing) - float(incoming))
    tolerance = max(abs(float(existing)), abs(float(incoming)), 1.0) * ratio
    return difference <= tolerance, difference


def _ratio(existing, incoming):
    if existing is None or incoming is None:
        return None
    incoming = float(incoming)
    if incoming <= 0:
        return None
    return float(existing) / incoming


def _evaluate_candidate(
    candidate, payload, authoritative_aircraft_serial, authoritative_battery_serial
):
    differences = {}
    reasons = []
    time_difference = abs((candidate.takeoff_datetime - payload["takeoff_datetime"]).total_seconds())
    differences["takeoff_time_seconds"] = round(time_difference, 3)
    if time_difference > TIME_TOLERANCE.total_seconds():
        return None
    reasons.append("takeoff time is within 60 seconds")

    incoming_serial = (authoritative_aircraft_serial or "").strip()
    existing_serial = (candidate.drone_serial or "").strip()
    serial_match = bool(incoming_serial and existing_serial and incoming_serial == existing_serial)
    serial_conflict = bool(incoming_serial and existing_serial and incoming_serial != existing_serial)
    if serial_conflict:
        return None
    if serial_match:
        reasons.append("full aircraft serial matches exactly")

    support_conflicts = 0
    core_support_matches = 0

    existing_coordinates = _coordinates(candidate.takeoff_latlong)
    incoming_coordinates = _coordinates(payload.get("takeoff_latlong"))
    if existing_coordinates and incoming_coordinates:
        distance = _distance_meters(existing_coordinates, incoming_coordinates)
        differences["takeoff_location_meters"] = round(distance, 3)
        if distance <= LOCATION_TOLERANCE_METERS:
            core_support_matches += 1
            reasons.append("takeoff locations are within 100 meters")
        else:
            support_conflicts += 1

    existing_seconds = None
    incoming_seconds = None
    duration_difference = None
    if candidate.air_time is not None and payload.get("air_time") is not None:
        existing_seconds = candidate.air_time.total_seconds()
        incoming_seconds = payload["air_time"].total_seconds()
        duration_difference = abs(existing_seconds - incoming_seconds)
        duration_tolerance = max(
            DURATION_TOLERANCE_SECONDS,
            max(existing_seconds, incoming_seconds) * DURATION_TOLERANCE_RATIO,
        )
        differences["air_time_seconds"] = round(duration_difference, 3)
        if duration_difference <= duration_tolerance:
            core_support_matches += 1
            reasons.append("air times agree within 30 seconds or 5 percent")
        else:
            support_conflicts += 1

    for field_name, label in (
        ("total_mileage_ft", "total distance"),
        ("max_distance_ft", "maximum distance"),
        ("max_altitude_ft", "maximum altitude"),
    ):
        agrees, difference = _numeric_agreement(
            getattr(candidate, field_name), payload.get(field_name)
        )
        if agrees is not None:
            differences[field_name] = round(difference, 3)
            if agrees:
                reasons.append(f"{label} agrees within 10 percent")

    if serial_match and core_support_matches >= 1 and support_conflicts == 0:
        return FlightMatchResult(
            MatchType.HIGH_CONFIDENCE, candidate, "high", tuple(reasons), differences
        )

    # Separate, validated path for sources whose takeoff-coordinate sampling
    # differs while their identity and normalized flight metrics agree tightly.
    location_variance_duration_agrees = (
        existing_seconds is not None
        and incoming_seconds is not None
        and abs(existing_seconds - incoming_seconds)
        <= max(existing_seconds, incoming_seconds, 1.0)
        * LOCATION_VARIANCE_METRIC_TOLERANCE_RATIO
    )
    location_variance_distance_agrees, _ = _numeric_agreement(
        candidate.total_mileage_ft,
        payload.get("total_mileage_ft"),
        LOCATION_VARIANCE_METRIC_TOLERANCE_RATIO,
    )
    location_variance_altitude_agrees, _ = _numeric_agreement(
        candidate.max_altitude_ft,
        payload.get("max_altitude_ft"),
        LOCATION_VARIANCE_METRIC_TOLERANCE_RATIO,
    )
    if (
        serial_match
        and existing_coordinates
        and incoming_coordinates
        and LOCATION_TOLERANCE_METERS < distance <= LOCATION_VARIANCE_TOLERANCE_METERS
        and location_variance_duration_agrees
        and location_variance_distance_agrees is True
        and location_variance_altitude_agrees is True
    ):
        variance_reasons = (
            "takeoff time is within 60 seconds",
            "full aircraft serial matches exactly",
            "air times agree within 1 percent",
            "total distance agrees within 1 percent",
            "maximum altitude agrees within 1 percent",
            "takeoff locations differ by more than 100 meters but no more than 500 meters",
        )
        return FlightMatchResult(
            MatchType.HIGH_CONFIDENCE_LOCATION_VARIANCE,
            candidate,
            "high",
            variance_reasons,
            differences,
        )

    duration_ratio = _ratio(existing_seconds, incoming_seconds)
    distance_ratio = _ratio(candidate.total_mileage_ft, payload.get("total_mileage_ft"))
    if (
        serial_match
        and existing_coordinates
        and incoming_coordinates
        and distance <= LOCATION_TOLERANCE_METERS
        and duration_ratio is not None
        and duration_ratio <= PARTIAL_AIRDATA_MAX_RATIO
        and distance_ratio is not None
        and distance_ratio <= PARTIAL_AIRDATA_MAX_RATIO
    ):
        differences["air_time_ratio"] = round(duration_ratio, 6)
        differences["total_mileage_ratio"] = round(distance_ratio, 6)
        partial_reasons = (
            "takeoff time is within 60 seconds",
            "full aircraft serial matches exactly",
            "takeoff locations are within 100 meters",
            "existing AirData duration is at least 10 percent shorter than DJI duration",
            "existing AirData total distance is at least 10 percent shorter than DJI total distance",
            "possible partial AirData record requires review",
        )
        return FlightMatchResult(
            MatchType.REVIEW_PARTIAL_AIRDATA,
            candidate,
            "review",
            partial_reasons,
            differences,
        )
    if not serial_match and core_support_matches == 2 and support_conflicts == 0:
        reasons.append("authoritative aircraft serial is unavailable on one source")
        return FlightMatchResult(
            MatchType.PROBABLE, candidate, "probable", tuple(reasons), differences
        )
    return None


def match_existing_flight(
    *, business, payload, authoritative_aircraft_serial, authoritative_battery_serial=""
):
    """Find a same-flight candidate strictly within one business."""
    takeoff = payload.get("takeoff_datetime")
    if takeoff is None:
        return FlightMatchResult(MatchType.NO_MATCH)

    candidates: QuerySet[FlightLog] = FlightLog.objects.filter(
        business=business,
        takeoff_datetime__gte=takeoff - TIME_TOLERANCE,
        takeoff_datetime__lte=takeoff + TIME_TOLERANCE,
    ).order_by("pk")
    matches = [
        result
        for candidate in candidates
        if (
            result := _evaluate_candidate(
                candidate,
                payload,
                authoritative_aircraft_serial,
                authoritative_battery_serial,
            )
        )
    ]
    high = [
        result
        for result in matches
        if result.match_type
        in {MatchType.HIGH_CONFIDENCE, MatchType.HIGH_CONFIDENCE_LOCATION_VARIANCE}
    ]
    partial = [
        result for result in matches if result.match_type == MatchType.REVIEW_PARTIAL_AIRDATA
    ]
    probable = [result for result in matches if result.match_type == MatchType.PROBABLE]
    # Ambiguity is never resolved by silently picking one row.
    if len(high) == 1:
        return high[0]
    if len(partial) == 1 and not high and not probable:
        return partial[0]
    if len(probable) == 1 and not high and not partial:
        return probable[0]
    if matches:
        return FlightMatchResult(
            MatchType.AMBIGUOUS,
            confidence="probable",
            reasons=("multiple plausible existing flights were found",),
        )
    return FlightMatchResult(MatchType.NO_MATCH)
