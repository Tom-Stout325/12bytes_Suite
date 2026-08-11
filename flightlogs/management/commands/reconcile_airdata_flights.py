from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import timedelta, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.models import Business
from flightlogs.models import FlightLog
from flightlogs.services.airdata_reconciliation import (
    ReconciliationClassification,
    parse_airdata_datetime,
    parse_coordinates,
    parse_duration,
    parse_number,
    reconcile_row,
)
from timezonefinder import TimezoneFinder


REPORT_FIELDS = (
    "csv_row_number",
    "csv_flight_datetime_raw",
    "csv_aircraft_serial",
    "csv_battery_serial",
    "csv_takeoff_latitude",
    "csv_takeoff_longitude",
    "csv_duration",
    "match_classification",
    "matched_flightlog_id",
    "matched_flightlog_takeoff_datetime",
    "resolved_timezone",
    "resolved_utc_offset",
    "proposed_takeoff_datetime_utc",
    "timestamp_shift_seconds",
    "location_distance_m",
    "duration_difference_seconds",
    "aircraft_serial_match",
    "battery_serial_match",
    "reason",
    "review_required",
)


def _value(row, *headers):
    for header in headers:
        value = row.get(header)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _iso(value):
    return value.isoformat() if value is not None else ""


def _shift_bucket(shift_seconds):
    absolute = abs(shift_seconds)
    if absolute <= 60:
        return "already_correct"
    if absolute < 3600:
        return "under_1_hour"
    if abs(absolute - 3600) <= 300:
        return "about_1_hour"
    if abs(absolute - 7200) <= 300:
        return "about_2_hours"
    if abs(absolute - 10800) <= 300:
        return "about_3_hours"
    return "other"


class Command(BaseCommand):
    help = "Produce a read-only AirData-to-FlightLog reconciliation CSV."

    def add_arguments(self, parser):
        parser.add_argument("csv_path")
        parser.add_argument("--dry-run", action="store_true", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--business-id", type=int)

    def _business(self, business_id):
        if business_id:
            try:
                return Business.objects.get(pk=business_id)
            except Business.DoesNotExist as exc:
                raise CommandError("The requested business does not exist.") from exc
        business_ids = list(
            FlightLog.objects.order_by().values_list("business_id", flat=True).distinct()[:2]
        )
        if len(business_ids) != 1:
            raise CommandError(
                "Specify --business-id when the database does not contain exactly one FlightLog-owning business."
            )
        return Business.objects.get(pk=business_ids[0])

    def handle(self, *args, **options):
        if not options["dry_run"]:
            raise CommandError("This milestone supports --dry-run only.")
        input_path = Path(options["csv_path"])
        output_path = Path(options["output"])
        if not input_path.is_file():
            raise CommandError(f"AirData CSV not found: {input_path}")
        if input_path.resolve() == output_path.resolve():
            raise CommandError("Input and output paths must be different.")

        business = self._business(options.get("business_id"))
        flights_by_date = defaultdict(list)
        queryset = FlightLog.objects.filter(business=business).only(
            "id", "flight_date", "takeoff_datetime", "takeoff_latlong", "air_time",
            "landing_time",
            "drone_serial", "battery_serial_internal", "battery_serial_printed",
            "total_mileage_ft", "max_altitude_ft", "max_distance_ft",
        )
        for flight in queryset.iterator(chunk_size=1000):
            flights_by_date[flight.flight_date].append(flight)

        timezone_finder = TimezoneFinder(in_memory=True)
        classification_counts = Counter()
        shift_counts = Counter()
        resolved_timezones = set()
        missing_coordinates = dst_ambiguous = dst_nonexistent = 0
        rows_total = 0

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with input_path.open("r", encoding="utf-8-sig", newline="") as source_file, output_path.open(
            "w", encoding="utf-8", newline=""
        ) as report_file:
            reader = csv.DictReader(source_file)
            if not reader.fieldnames or "Flight Date/Time" not in reader.fieldnames:
                raise CommandError("CSV does not contain the AirData Flight Date/Time header.")
            writer = csv.DictWriter(report_file, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            for row_number, row in enumerate(reader, start=2):
                rows_total += 1
                coordinates = parse_coordinates(_value(row, "Takeoff Lat/Long"))
                if coordinates is None:
                    missing_coordinates += 1
                duration_raw = _value(row, "Air Time", "Air Seconds")
                duration = parse_duration(duration_raw)
                datetime_raw = _value(row, "Flight Date/Time")
                aircraft_serial = _value(row, "Drone Serial Number")
                battery_serial = _value(row, "Bat Internal Serial", "Bat Printed Serial")
                row_data = {
                    "datetime_raw": datetime_raw,
                    "coordinates": coordinates,
                    "duration": duration,
                    "aircraft_serial": aircraft_serial,
                    "battery_serial": battery_serial,
                    "landing_local": (
                        parse_airdata_datetime(_value(row, "Landing Time")).time()
                        if parse_airdata_datetime(_value(row, "Landing Time"))
                        else None
                    ),
                    "total_distance_ft": parse_number(_value(row, "Total Mileage (Feet)")),
                    "max_altitude_ft": parse_number(_value(row, "Max Altitude (Feet)")),
                    "max_distance_ft": parse_number(_value(row, "Max Distance (Feet)")),
                }

                # AirData's local calendar date is stable even where the historical UTC instant is not.
                parsed_for_date = parse_airdata_datetime(datetime_raw)
                possible_flights = []
                if parsed_for_date is not None:
                    local_date = parsed_for_date.date()
                    for day_delta in (-1, 0, 1):
                        possible_flights.extend(
                            flights_by_date.get(local_date + timedelta(days=day_delta), [])
                        )
                result = reconcile_row(
                    row_data=row_data,
                    existing_flights=possible_flights,
                    timezone_finder=timezone_finder,
                )
                classification_counts[result.classification] += 1
                if result.timestamp.timezone_name:
                    resolved_timezones.add(result.timestamp.timezone_name)
                dst_ambiguous += int(result.timestamp.dst_ambiguous)
                dst_nonexistent += int(result.timestamp.dst_nonexistent)

                evidence = result.evidence
                matched = result.matched_flight
                shift_seconds = ""
                if matched and matched.takeoff_datetime and result.timestamp.proposed_utc:
                    shift_seconds = (
                        result.timestamp.proposed_utc
                        - matched.takeoff_datetime.astimezone(timezone.utc)
                    ).total_seconds()
                    shift_counts[_shift_bucket(shift_seconds)] += 1
                writer.writerow(
                    {
                        "csv_row_number": row_number,
                        "csv_flight_datetime_raw": datetime_raw,
                        "csv_aircraft_serial": aircraft_serial,
                        "csv_battery_serial": battery_serial,
                        "csv_takeoff_latitude": coordinates[0] if coordinates else "",
                        "csv_takeoff_longitude": coordinates[1] if coordinates else "",
                        "csv_duration": duration_raw,
                        "match_classification": result.classification,
                        "matched_flightlog_id": (
                            matched.pk
                            if matched
                            else ";".join(str(candidate_id) for candidate_id in result.candidate_ids)
                        ),
                        "matched_flightlog_takeoff_datetime": _iso(matched.takeoff_datetime) if matched else "",
                        "resolved_timezone": result.timestamp.timezone_name,
                        "resolved_utc_offset": result.timestamp.utc_offset,
                        "proposed_takeoff_datetime_utc": _iso(result.timestamp.proposed_utc),
                        "timestamp_shift_seconds": shift_seconds,
                        "location_distance_m": round(evidence.location_distance_m, 3) if evidence and evidence.location_distance_m is not None else "",
                        "duration_difference_seconds": round(evidence.duration_difference_seconds, 3) if evidence and evidence.duration_difference_seconds is not None else "",
                        "aircraft_serial_match": evidence.aircraft_serial_match if evidence else "",
                        "battery_serial_match": evidence.battery_serial_match if evidence else "",
                        "reason": result.reason,
                        "review_required": result.review_required,
                    }
                )

        self.stdout.write(f"CSV rows total: {rows_total}")
        self.stdout.write(f"Existing exact matches: {classification_counts[ReconciliationClassification.EXACT_EXISTING]}")
        self.stdout.write(f"Ambiguous matches: {classification_counts[ReconciliationClassification.AMBIGUOUS_EXISTING]}")
        self.stdout.write(f"New CSV flights: {classification_counts[ReconciliationClassification.NEW_CSV_FLIGHT]}")
        self.stdout.write(f"Unresolved rows: {classification_counts[ReconciliationClassification.UNRESOLVED]}")
        self.stdout.write(f"Already-correct timestamps: {shift_counts['already_correct']}")
        self.stdout.write(f"Would shift < 1 hour: {shift_counts['under_1_hour']}")
        self.stdout.write(f"Would shift ~1 hour: {shift_counts['about_1_hour']}")
        self.stdout.write(f"Would shift ~2 hours: {shift_counts['about_2_hours']}")
        self.stdout.write(f"Would shift ~3 hours: {shift_counts['about_3_hours']}")
        self.stdout.write(f"Would shift other amount: {shift_counts['other']}")
        self.stdout.write(f"Distinct resolved timezones: {', '.join(sorted(resolved_timezones))}")
        self.stdout.write(f"Rows missing coordinates: {missing_coordinates}")
        self.stdout.write(f"DST ambiguous rows: {dst_ambiguous}")
        self.stdout.write(f"DST nonexistent rows: {dst_nonexistent}")
        self.stdout.write(f"Report: {output_path}")
