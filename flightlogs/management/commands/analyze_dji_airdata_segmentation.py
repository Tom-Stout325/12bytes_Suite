from __future__ import annotations

import csv
import math
import statistics
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from flightlogs.models import FlightLog, FlightLogSource
from flightlogs.services.matching import _coordinates, _distance_meters


REPORT_FIELDS = (
    "filename",
    "validation_group",
    "analysis_classification",
    "aircraft_serial",
    "dji_start_time",
    "dji_duration_seconds",
    "dji_total_distance_ft",
    "dji_max_altitude_ft",
    "original_candidate_id",
    "original_candidate_start_time",
    "original_candidate_duration_seconds",
    "original_candidate_distance_ft",
    "original_candidate_max_altitude_ft",
    "original_candidate_time_difference_seconds",
    "original_candidate_location_difference_m",
    "duration_difference_seconds",
    "duration_ratio",
    "candidate_flightlog_ids",
    "candidate_start_times",
    "candidate_durations_seconds",
    "combined_candidate_duration_seconds",
    "combined_candidate_time_span_seconds",
    "candidate_distances_ft",
    "combined_candidate_distance_ft",
    "candidate_max_altitudes_ft",
    "candidate_location_differences_m",
    "original_distance_difference_pct",
    "combined_distance_difference_pct",
    "adjacent_starts_during_dji",
    "adjacent_starts_within_30s_after",
    "adjacent_starts_within_60s_after",
    "adjacent_starts_within_5m_after",
    "nearest_same_aircraft_id",
    "nearest_same_aircraft_time_difference_seconds",
    "nearest_same_aircraft_location_difference_m",
    "reason",
)


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _duration_seconds(flight):
    return flight.air_time.total_seconds() if flight.air_time is not None else None


def _percentage_difference(first, second):
    if first is None or second is None:
        return None
    denominator = max(abs(first), abs(second), 1.0)
    return abs(first - second) / denominator * 100


def _join(values):
    return ";".join("" if value is None else str(value) for value in values)


def _percentile(values, percentile):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile / 100
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


class Command(BaseCommand):
    help = "Analyze DJI/AirData segmentation conflicts without database writes."

    def add_arguments(self, parser):
        parser.add_argument("validation_report")
        parser.add_argument("--output", required=True)
        parser.add_argument("--business-id", type=int)

    def handle(self, *args, **options):
        source = Path(options["validation_report"])
        output = Path(options["output"])
        if not source.is_file():
            raise CommandError(f"Validation report not found: {source}")

        business_ids = list(
            FlightLog.objects.order_by().values_list("business_id", flat=True).distinct()
        )
        business_id = options.get("business_id")
        if business_id is None:
            if len(business_ids) != 1:
                raise CommandError("Specify --business-id unless exactly one FlightLog business exists.")
            business_id = business_ids[0]
        if business_id not in business_ids:
            raise CommandError("The selected business has no FlightLog baseline.")

        before = (FlightLog.objects.count(), FlightLogSource.objects.count())
        flights = list(
            FlightLog.objects.filter(business_id=business_id)
            .only(
                "id",
                "takeoff_datetime",
                "drone_serial",
                "takeoff_latlong",
                "air_time",
                "total_mileage_ft",
                "max_altitude_ft",
                "battery_serial_printed",
                "battery_serial_internal",
            )
            .order_by("takeoff_datetime", "id")
        )
        by_serial = {}
        for flight in flights:
            by_serial.setdefault(flight.drone_serial.strip(), []).append(flight)

        with source.open(encoding="utf-8", newline="") as source_file:
            all_validation_rows = list(csv.DictReader(source_file))
            validation_rows = [
                row for row in all_validation_rows if row["classification"] == "NO_MATCH"
            ]

        flight_by_id = {flight.pk: flight for flight in flights}
        high_rows = [
            row for row in all_validation_rows if row["classification"] == "HIGH_CONFIDENCE_MATCH"
        ]
        battery_comparable = 0
        battery_matches = 0
        for row in high_rows:
            flight = flight_by_id.get(int(row["matched_flightlog_id"]))
            dji_battery = row["battery_serial"].strip()
            if not flight or not dji_battery:
                continue
            airdata_batteries = {
                value.strip()
                for value in (flight.battery_serial_printed, flight.battery_serial_internal)
                if value.strip()
            }
            if airdata_batteries:
                battery_comparable += 1
                battery_matches += dji_battery in airdata_batteries

        output.parent.mkdir(parents=True, exist_ok=True)
        counts = Counter()
        duration_differences = []
        duration_ratios = []
        distance_winners = Counter()
        rows = []
        for source_row in validation_rows:
            result = self._analyze_row(source_row, by_serial)
            rows.append(result)
            counts[result["validation_group"]] += 1
            counts[result["analysis_classification"]] += 1
            if result["validation_group"] == "DURATION_CONFLICT":
                difference = _float(result["duration_difference_seconds"])
                ratio = _float(result["duration_ratio"])
                if difference is not None:
                    duration_differences.append(difference)
                if ratio is not None:
                    duration_ratios.append(ratio)
                original = _float(result["original_distance_difference_pct"])
                combined = _float(result["combined_distance_difference_pct"])
                if original is None and combined is None:
                    distance_winners["unavailable"] += 1
                elif combined is not None and (original is None or combined + 1 < original):
                    distance_winners["combined"] += 1
                elif original is not None and (combined is None or original + 1 < combined):
                    distance_winners["original"] += 1
                else:
                    distance_winners["tie"] += 1

        with output.open("w", encoding="utf-8", newline="") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

        after = (FlightLog.objects.count(), FlightLogSource.objects.count())
        if after != before:
            raise CommandError(f"Database counts changed unexpectedly: before={before}, after={after}")

        self.stdout.write(f"Analyzed NO_MATCH rows: {len(rows)}")
        for group in (
            "DURATION_CONFLICT",
            "LOCATION_CONFLICT",
            "DURATION_AND_LOCATION_CONFLICT",
            "MISSING_CORE_EVIDENCE",
            "NO_SAME_TIME_CANDIDATE",
        ):
            self.stdout.write(f"{group}: {counts[group]}")
        for classification in (
            "MULTIPLE_AIRDATA_SEGMENTS",
            "SIMPLE_DURATION_SEMANTICS",
            "AIRDATA_PARTIAL_RECORD",
            "DIFFERENT_PHYSICAL_FLIGHT",
            "UNRESOLVED",
        ):
            self.stdout.write(f"{classification}: {counts[classification]}")
        if duration_differences:
            self.stdout.write(
                "Duration difference seconds (DJI-AirData): "
                f"min={min(duration_differences):.1f}, median={statistics.median(duration_differences):.1f}, "
                f"p90={_percentile(duration_differences, 90):.1f}, max={max(duration_differences):.1f}"
            )
        if duration_ratios:
            self.stdout.write(
                "Duration ratio (DJI/AirData): "
                f"min={min(duration_ratios):.3f}, median={statistics.median(duration_ratios):.3f}, "
                f"p90={_percentile(duration_ratios, 90):.3f}, max={max(duration_ratios):.3f}"
            )
        self.stdout.write(f"Distance comparison: {dict(distance_winners)}")
        self.stdout.write(
            f"High-match battery equality: {battery_matches}/{battery_comparable} comparable rows"
        )
        self.stdout.write(f"Database before/after: {before}/{after}")
        self.stdout.write(f"Report: {output}")

    def _analyze_row(self, row, by_serial):
        serial = row["aircraft_serial"].strip()
        start = datetime.fromisoformat(row["dji_takeoff_datetime"])
        duration = _float(row["duration_seconds"])
        end = start + timedelta(seconds=duration or 0)
        dji_distance = _float(row["total_distance_ft"])
        dji_altitude = _float(row["max_altitude_ft"])
        dji_coordinates = None
        latitude = _float(row["takeoff_latitude"])
        longitude = _float(row["takeoff_longitude"])
        if latitude is not None and longitude is not None:
            dji_coordinates = (latitude, longitude)

        serial_flights = by_serial.get(serial, [])
        same_time = [
            flight
            for flight in serial_flights
            if abs((flight.takeoff_datetime - start).total_seconds()) <= 60
        ]
        evaluated = [(flight, self._location_distance(flight, dji_coordinates)) for flight in same_time]
        original = min(
            evaluated,
            key=lambda item: (
                abs((item[0].takeoff_datetime - start).total_seconds()),
                item[0].pk,
            ),
            default=(None, None),
        )
        original_flight, original_location = original
        original_duration = _duration_seconds(original_flight) if original_flight else None
        duration_difference = (
            duration - original_duration
            if duration is not None and original_duration is not None
            else None
        )
        duration_ratio = (
            duration / original_duration
            if duration is not None and original_duration not in (None, 0)
            else None
        )
        duration_agrees = (
            duration_difference is not None
            and abs(duration_difference) <= max(30, max(duration, original_duration) * 0.05)
        )
        location_agrees = original_location is not None and original_location <= 100
        if original_flight and (original_location is None or duration_difference is None):
            validation_group = "MISSING_CORE_EVIDENCE"
        elif original_flight and not duration_agrees and location_agrees:
            validation_group = "DURATION_CONFLICT"
        elif original_flight and duration_agrees and not location_agrees:
            validation_group = "LOCATION_CONFLICT"
        elif original_flight and not duration_agrees and not location_agrees:
            validation_group = "DURATION_AND_LOCATION_CONFLICT"
        elif original_flight:
            validation_group = "MISSING_CORE_EVIDENCE"
        else:
            validation_group = "NO_SAME_TIME_CANDIDATE"

        # Broad exploratory window: inspect the same-aircraft timeline from 30 minutes
        # before DJI takeoff through five minutes after DJI's recorded end.
        broad = [
            flight
            for flight in serial_flights
            if start - timedelta(minutes=30)
            <= flight.takeoff_datetime
            <= end + timedelta(minutes=5)
        ]
        broad_with_distance = [
            (flight, self._location_distance(flight, dji_coordinates)) for flight in broad
        ]
        nearby = [
            (flight, distance)
            for flight, distance in broad_with_distance
            if distance is not None and distance <= 500
        ]
        interval_candidates = [
            (flight, distance)
            for flight, distance in nearby
            if start - timedelta(seconds=60)
            <= flight.takeoff_datetime
            <= end + timedelta(minutes=5)
        ]
        if original_flight and all(flight.pk != original_flight.pk for flight, _ in interval_candidates):
            interval_candidates.append((original_flight, original_location))
            interval_candidates.sort(key=lambda item: (item[0].takeoff_datetime, item[0].pk))

        candidate_flights = [flight for flight, _ in interval_candidates]
        candidate_distances = [distance for _, distance in interval_candidates]
        candidate_durations = [_duration_seconds(flight) for flight in candidate_flights]
        combined_duration = (
            sum(value for value in candidate_durations if value is not None)
            if any(value is not None for value in candidate_durations)
            else None
        )
        combined_distance = (
            sum(flight.total_mileage_ft for flight in candidate_flights if flight.total_mileage_ft is not None)
            if any(flight.total_mileage_ft is not None for flight in candidate_flights)
            else None
        )
        if candidate_flights:
            ends = [
                flight.takeoff_datetime + timedelta(seconds=_duration_seconds(flight) or 0)
                for flight in candidate_flights
            ]
            combined_span = (max(ends) - min(flight.takeoff_datetime for flight in candidate_flights)).total_seconds()
        else:
            combined_span = None

        original_distance = original_flight.total_mileage_ft if original_flight else None
        original_distance_pct = _percentage_difference(dji_distance, original_distance)
        combined_distance_pct = _percentage_difference(dji_distance, combined_distance)
        original_duration_pct = _percentage_difference(duration, original_duration)
        combined_duration_pct = _percentage_difference(duration, combined_duration)
        combined_span_pct = _percentage_difference(duration, combined_span)

        additional = [flight for flight in candidate_flights if not original_flight or flight.pk != original_flight.pk]
        during = sum(start < flight.takeoff_datetime <= end for flight in additional)
        after_seconds = [(flight.takeoff_datetime - end).total_seconds() for flight in additional]
        after_30 = sum(0 < seconds <= 30 for seconds in after_seconds)
        after_60 = sum(0 < seconds <= 60 for seconds in after_seconds)
        after_5m = sum(0 < seconds <= 300 for seconds in after_seconds)

        nearest = min(
            serial_flights,
            key=lambda flight: abs((flight.takeoff_datetime - start).total_seconds()),
            default=None,
        )
        nearest_time = abs((nearest.takeoff_datetime - start).total_seconds()) if nearest else None
        nearest_location = self._location_distance(nearest, dji_coordinates) if nearest else None

        classification, reason = self._classify(
            validation_group=validation_group,
            original=original_flight,
            original_location=original_location,
            original_duration_pct=original_duration_pct,
            original_distance_pct=original_distance_pct,
            additional=additional,
            combined_duration_pct=combined_duration_pct,
            combined_span_pct=combined_span_pct,
            combined_distance_pct=combined_distance_pct,
            adjacent_starts_during=during,
            nearest_time=nearest_time,
            nearest_location=nearest_location,
        )

        return {
            "filename": row["filename"],
            "validation_group": validation_group,
            "analysis_classification": classification,
            "aircraft_serial": serial,
            "dji_start_time": start.isoformat(),
            "dji_duration_seconds": duration,
            "dji_total_distance_ft": dji_distance,
            "dji_max_altitude_ft": dji_altitude,
            "original_candidate_id": original_flight.pk if original_flight else "",
            "original_candidate_start_time": original_flight.takeoff_datetime.isoformat() if original_flight else "",
            "original_candidate_duration_seconds": original_duration,
            "original_candidate_distance_ft": original_distance,
            "original_candidate_max_altitude_ft": original_flight.max_altitude_ft if original_flight else "",
            "original_candidate_time_difference_seconds": abs((original_flight.takeoff_datetime - start).total_seconds()) if original_flight else "",
            "original_candidate_location_difference_m": original_location,
            "duration_difference_seconds": duration_difference,
            "duration_ratio": duration_ratio,
            "candidate_flightlog_ids": _join(flight.pk for flight in candidate_flights),
            "candidate_start_times": _join(flight.takeoff_datetime.isoformat() for flight in candidate_flights),
            "candidate_durations_seconds": _join(candidate_durations),
            "combined_candidate_duration_seconds": combined_duration,
            "combined_candidate_time_span_seconds": combined_span,
            "candidate_distances_ft": _join(flight.total_mileage_ft for flight in candidate_flights),
            "combined_candidate_distance_ft": combined_distance,
            "candidate_max_altitudes_ft": _join(flight.max_altitude_ft for flight in candidate_flights),
            "candidate_location_differences_m": _join(round(value, 3) if value is not None else None for value in candidate_distances),
            "original_distance_difference_pct": original_distance_pct,
            "combined_distance_difference_pct": combined_distance_pct,
            "adjacent_starts_during_dji": during,
            "adjacent_starts_within_30s_after": after_30,
            "adjacent_starts_within_60s_after": after_60,
            "adjacent_starts_within_5m_after": after_5m,
            "nearest_same_aircraft_id": nearest.pk if nearest else "",
            "nearest_same_aircraft_time_difference_seconds": nearest_time,
            "nearest_same_aircraft_location_difference_m": nearest_location,
            "reason": reason,
        }

    @staticmethod
    def _location_distance(flight, dji_coordinates):
        if flight is None or dji_coordinates is None:
            return None
        coordinates = _coordinates(flight.takeoff_latlong)
        return _distance_meters(coordinates, dji_coordinates) if coordinates else None

    @staticmethod
    def _classify(
        *,
        validation_group,
        original,
        original_location,
        original_duration_pct,
        original_distance_pct,
        additional,
        combined_duration_pct,
        combined_span_pct,
        combined_distance_pct,
        adjacent_starts_during,
        nearest_time,
        nearest_location,
    ):
        if validation_group == "DURATION_CONFLICT":
            combined_duration_improves = (
                additional
                and combined_duration_pct is not None
                and original_duration_pct is not None
                and combined_duration_pct + 10 < original_duration_pct
            )
            combined_span_improves = (
                additional
                and combined_span_pct is not None
                and original_duration_pct is not None
                and combined_span_pct + 10 < original_duration_pct
            )
            combined_distance_improves = (
                additional
                and combined_distance_pct is not None
                and original_distance_pct is not None
                and combined_distance_pct + 10 < original_distance_pct
            )
            if (
                adjacent_starts_during
                and combined_distance_improves
                and (combined_duration_improves or combined_span_improves)
            ):
                return "MULTIPLE_AIRDATA_SEGMENTS", "multiple nearby AirData records jointly improve both distance and duration/time-span agreement"
            if original_distance_pct is not None and original_distance_pct <= 10:
                return "SIMPLE_DURATION_SEMANTICS", "single AirData record agrees in serial, takeoff location, time, and distance; only duration semantics materially differ"
            if original_duration_pct is not None and original_duration_pct >= 10 and original_distance_pct is not None and original_distance_pct >= 10:
                return "AIRDATA_PARTIAL_RECORD", "AirData candidate appears to cover only part of the longer DJI distance/duration record"
            return "UNRESOLVED", "duration differs without enough distance/adjacent-record evidence for a safe segmentation conclusion"

        if validation_group in {"LOCATION_CONFLICT", "DURATION_AND_LOCATION_CONFLICT"}:
            if original_location is not None and original_location > 500:
                return "DIFFERENT_PHYSICAL_FLIGHT", "same-minute record is geographically inconsistent by more than 500 meters"
            return "UNRESOLVED", "same-minute record has conflicting takeoff location; segmentation is not established"

        if validation_group == "NO_SAME_TIME_CANDIDATE":
            if nearest_time is not None and nearest_time <= 300 and nearest_location is not None and nearest_location <= 500:
                return "UNRESOLVED", "nearby same-aircraft AirData record exists outside the matcher time window"
            return "DIFFERENT_PHYSICAL_FLIGHT", "no same-time AirData record; nearest same-aircraft record does not identify this flight"

        return "UNRESOLVED", "required location or duration evidence is missing"
