from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from zoneinfo import ZoneInfo

from django.conf import settings

from flightlogs.models import FlightLog
from flightlogs.services.airdata_timezones import (
    TimestampResolution,
    parse_airdata_datetime,
    parse_coordinates,
    resolve_airdata_timestamp,
)


TIME_TOLERANCE_SECONDS = 60.0
LOCATION_TOLERANCE_METERS = 100.0
DURATION_TOLERANCE_SECONDS = 30.0
DURATION_TOLERANCE_RATIO = 0.05


class ReconciliationClassification(StrEnum):
    EXACT_EXISTING = "EXACT_EXISTING"
    AMBIGUOUS_EXISTING = "AMBIGUOUS_EXISTING"
    NEW_CSV_FLIGHT = "NEW_CSV_FLIGHT"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class CandidateEvidence:
    flight: FlightLog
    location_distance_m: float | None
    duration_difference_seconds: float | None
    aircraft_serial_match: bool
    battery_serial_match: bool
    wall_time_difference_seconds: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReconciliationResult:
    classification: ReconciliationClassification
    timestamp: TimestampResolution
    matched_flight: FlightLog | None = None
    evidence: CandidateEvidence | None = None
    reason: str = ""
    review_required: bool = False
    candidate_ids: tuple[int, ...] = field(default_factory=tuple)


def parse_duration(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if ":" not in text:
            return timedelta(seconds=float(text))
        parts = text.split(":")
        if len(parts) == 3:
            return timedelta(hours=int(parts[0]), minutes=int(parts[1]), seconds=float(parts[2]))
        if len(parts) == 2:
            return timedelta(minutes=int(parts[0]), seconds=float(parts[1]))
    except (TypeError, ValueError):
        return None
    return None


def parse_number(value):
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def _distance_meters(first, second):
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    delta_latitude, delta_longitude = lat2 - lat1, lon2 - lon1
    haversine = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_longitude / 2) ** 2
    )
    return 6_371_000 * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


def _serial(value):
    return str(value or "").strip()


def _candidate_evidence(flight, row_data, timestamp):
    csv_serial = _serial(row_data.get("aircraft_serial"))
    existing_serial = _serial(flight.drone_serial)
    if csv_serial and existing_serial and csv_serial != existing_serial:
        return None
    aircraft_match = bool(csv_serial and existing_serial and csv_serial == existing_serial)

    legacy_zone = ZoneInfo(settings.TIME_ZONE)
    if flight.takeoff_datetime is None:
        if flight.flight_date != timestamp.local_wall_time.date():
            return None
        wall_difference = 0.0
    elif timestamp.parsed.tzinfo is None:
        existing_wall = flight.takeoff_datetime.astimezone(legacy_zone).replace(tzinfo=None)
        incoming_wall = timestamp.local_wall_time
        legacy_difference = abs((existing_wall - incoming_wall).total_seconds())
        normalized_difference = abs(
            (flight.takeoff_datetime.astimezone(timezone.utc) - timestamp.proposed_utc).total_seconds()
        )
        wall_difference = min(legacy_difference, normalized_difference)
    else:
        existing_wall = flight.takeoff_datetime.astimezone(timezone.utc).replace(tzinfo=None)
        incoming_wall = timestamp.proposed_utc.replace(tzinfo=None)
        wall_difference = abs((existing_wall - incoming_wall).total_seconds())
    if wall_difference > TIME_TOLERANCE_SECONDS:
        return None

    csv_coordinates = row_data.get("coordinates")
    existing_coordinates = parse_coordinates(flight.takeoff_latlong)
    location_distance = None
    location_match = False
    if csv_coordinates and existing_coordinates:
        location_distance = _distance_meters(csv_coordinates, existing_coordinates)
        if location_distance > LOCATION_TOLERANCE_METERS:
            return None
        location_match = True

    csv_duration = row_data.get("duration")
    duration_difference = None
    duration_match = False
    if csv_duration is not None and flight.air_time is not None:
        csv_seconds = csv_duration.total_seconds()
        existing_seconds = flight.air_time.total_seconds()
        duration_difference = abs(csv_seconds - existing_seconds)
        tolerance = max(
            DURATION_TOLERANCE_SECONDS,
            max(csv_seconds, existing_seconds) * DURATION_TOLERANCE_RATIO,
        )
        if duration_difference > tolerance:
            return None
        duration_match = True

    csv_battery = _serial(row_data.get("battery_serial"))
    existing_batteries = {
        _serial(flight.battery_serial_internal),
        _serial(flight.battery_serial_printed),
    } - {""}
    battery_match = bool(csv_battery and csv_battery in existing_batteries)

    if flight.takeoff_datetime is None:
        # Without an existing timestamp, accept only near-exact physical source
        # values; legacy landing_time values are not reliable enough to infer takeoff.
        if not (
            aircraft_match
            and location_distance is not None
            and location_distance <= 5
            and duration_difference is not None
            and duration_difference <= 5
        ):
            return None

    # With a full aircraft identity, one core physical measurement is required.
    # Without it, both core measurements plus battery identity are required.
    if aircraft_match and not (location_match or duration_match):
        return None
    if not aircraft_match and not (location_match and duration_match and battery_match):
        return None

    reasons = [
        "local wall-clock time agrees within 60 seconds"
        if flight.takeoff_datetime is not None
        else "local date and near-exact physical source values agree"
    ]
    if aircraft_match:
        reasons.append("full aircraft serial matches")
    if location_match:
        reasons.append("takeoff location agrees within 100 meters")
    if duration_match:
        reasons.append("duration agrees within 30 seconds or 5 percent")
    if battery_match:
        reasons.append("battery serial matches")
    return CandidateEvidence(
        flight, location_distance, duration_difference, aircraft_match,
        battery_match, wall_difference, tuple(reasons),
    )


def reconcile_row(*, row_data, existing_flights, timezone_finder):
    timestamp = resolve_airdata_timestamp(
        row_data.get("datetime_raw"), row_data.get("coordinates"), timezone_finder
    )
    if timestamp.proposed_utc is None:
        return ReconciliationResult(
            ReconciliationClassification.UNRESOLVED,
            timestamp,
            reason=timestamp.reason,
            review_required=True,
        )

    candidates = []
    for flight in existing_flights:
        evidence = _candidate_evidence(flight, row_data, timestamp)
        if evidence:
            candidates.append(evidence)
    if len(candidates) == 1:
        evidence = candidates[0]
        return ReconciliationResult(
            ReconciliationClassification.EXACT_EXISTING,
            timestamp,
            evidence.flight,
            evidence,
            "; ".join(evidence.reasons),
        )
    if len(candidates) > 1:
        return ReconciliationResult(
            ReconciliationClassification.AMBIGUOUS_EXISTING,
            timestamp,
            reason="multiple plausible existing FlightLog rows",
            review_required=True,
            candidate_ids=tuple(candidate.flight.pk for candidate in candidates),
        )

    sufficient_identity = bool(
        _serial(row_data.get("aircraft_serial"))
        and row_data.get("coordinates")
        and row_data.get("duration") is not None
    )
    if sufficient_identity:
        return ReconciliationResult(
            ReconciliationClassification.NEW_CSV_FLIGHT,
            timestamp,
            reason="no business-scoped existing flight matched full identity evidence",
        )
    return ReconciliationResult(
        ReconciliationClassification.UNRESOLVED,
        timestamp,
        reason="insufficient identity evidence to classify as a new flight",
        review_required=True,
    )
