from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from core.models import Business
from flightlogs.models import FlightLog, FlightLogSource
from flightlogs.services.dji.errors import DJIImportError
from flightlogs.services.dji.importer import _flightlog_payload, _source_metadata
from flightlogs.services.dji.subprocess_adapter import parse_dji_source
from flightlogs.services.matching import (
    MatchType,
    TIME_TOLERANCE,
    _evaluate_candidate,
    match_existing_flight,
)


REPORT_FIELDS = (
    "filename",
    "classification",
    "matched_flightlog_id",
    "candidate_flightlog_ids",
    "aircraft_serial",
    "aircraft_serial_header",
    "aircraft_identity_source",
    "battery_serial",
    "dji_takeoff_datetime",
    "airdata_takeoff_datetime",
    "takeoff_latitude",
    "takeoff_longitude",
    "duration_seconds",
    "total_distance_ft",
    "max_altitude_ft",
    "time_difference_seconds",
    "location_difference_m",
    "duration_difference_seconds",
    "battery_serial_match",
    "distance_difference_pct",
    "altitude_difference_pct",
    "parser_version",
    "log_version",
    "parser_error_code",
    "reason",
)


def _percentage_difference(existing, incoming):
    if existing is None or incoming is None:
        return ""
    denominator = max(abs(float(existing)), abs(float(incoming)))
    if denominator == 0:
        return 0.0
    return round(abs(float(existing) - float(incoming)) / denominator * 100, 3)


class Command(BaseCommand):
    help = "Parse DJI logs and validate AirData matching without database writes."

    def add_arguments(self, parser):
        parser.add_argument("directory")
        parser.add_argument("--output", required=True)
        parser.add_argument("--business-id", type=int)

    def handle(self, *args, **options):
        directory = Path(options["directory"])
        output = Path(options["output"])
        if not directory.is_dir():
            raise CommandError(f"DJI directory not found: {directory}")
        files = sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".txt")
        if not files:
            raise CommandError("No DJI .txt files were found.")

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
        business = Business.objects.get(pk=business_id)

        before = (FlightLog.objects.count(), FlightLogSource.objects.count())
        counts = Counter()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8", newline="") as report_file:
            writer = csv.DictWriter(report_file, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            for index, path in enumerate(files, start=1):
                row = {field: "" for field in REPORT_FIELDS}
                row["filename"] = path.name
                try:
                    source_file = File(path.open("rb"), name=path.name)
                    parsed = parse_dji_source(source_file)
                    payload = _flightlog_payload(parsed)
                    metadata = _source_metadata(parsed)
                    counts["parsed"] += 1

                    full_aircraft_serial = metadata["aircraft_serial"]
                    header_aircraft_serial = metadata["aircraft_serial_header"]
                    full_battery_serial = metadata["battery_serial"]
                    row.update(
                        {
                            "aircraft_serial": full_aircraft_serial,
                            "aircraft_serial_header": header_aircraft_serial,
                            "aircraft_identity_source": (
                                "component" if full_aircraft_serial else "header" if header_aircraft_serial else "missing"
                            ),
                            "battery_serial": full_battery_serial or metadata["battery_serial_header"],
                            "dji_takeoff_datetime": payload["takeoff_datetime"].isoformat(),
                            "takeoff_latitude": parsed.get("takeoff_latitude") if parsed.get("takeoff_latitude") is not None else "",
                            "takeoff_longitude": parsed.get("takeoff_longitude") if parsed.get("takeoff_longitude") is not None else "",
                            "duration_seconds": payload["air_time"].total_seconds() if payload.get("air_time") else "",
                            "total_distance_ft": payload.get("total_mileage_ft") if payload.get("total_mileage_ft") is not None else "",
                            "max_altitude_ft": payload.get("max_altitude_ft") if payload.get("max_altitude_ft") is not None else "",
                            "parser_version": metadata["parser_version"],
                            "log_version": metadata["log_version"],
                        }
                    )

                    primary = match_existing_flight(
                        business=business,
                        payload=payload,
                        authoritative_aircraft_serial=full_aircraft_serial,
                        authoritative_battery_serial=full_battery_serial,
                    )
                    takeoff = payload["takeoff_datetime"]
                    candidate_results = []
                    candidates = FlightLog.objects.filter(
                        business_id=business_id,
                        takeoff_datetime__gte=takeoff - TIME_TOLERANCE,
                        takeoff_datetime__lte=takeoff + TIME_TOLERANCE,
                    ).order_by("pk")
                    for candidate in candidates:
                        result = _evaluate_candidate(
                            candidate, payload, full_aircraft_serial, full_battery_serial
                        )
                        if result:
                            candidate_results.append(result)

                    classifications = {
                        MatchType.HIGH_CONFIDENCE: "PRIMARY_HIGH_CONFIDENCE_MATCH",
                        MatchType.HIGH_CONFIDENCE_LOCATION_VARIANCE: "HIGH_CONFIDENCE_LOCATION_VARIANCE",
                        MatchType.REVIEW_PARTIAL_AIRDATA: "REVIEW_PARTIAL_AIRDATA",
                        MatchType.PROBABLE: "PROBABLE_MATCH",
                        MatchType.AMBIGUOUS: "AMBIGUOUS_MATCH",
                        MatchType.NO_MATCH: "NO_MATCH",
                    }
                    classification = classifications[primary.match_type]
                    selected = primary if primary.matched_flight is not None else None

                    row["classification"] = classification
                    counts[classification] += 1
                    row["candidate_flightlog_ids"] = ";".join(
                        str(result.matched_flight.pk) for result in candidate_results
                    )
                    if selected:
                        flight = selected.matched_flight
                        differences = selected.field_differences
                        existing_batteries = {
                            value.strip()
                            for value in (flight.battery_serial_internal, flight.battery_serial_printed)
                            if value.strip()
                        }
                        row.update(
                            {
                                "matched_flightlog_id": flight.pk,
                                "airdata_takeoff_datetime": flight.takeoff_datetime.isoformat(),
                                "time_difference_seconds": differences.get("takeoff_time_seconds", ""),
                                "location_difference_m": differences.get("takeoff_location_meters", ""),
                                "duration_difference_seconds": differences.get("air_time_seconds", ""),
                                "battery_serial_match": bool(full_battery_serial and full_battery_serial in existing_batteries),
                                "distance_difference_pct": _percentage_difference(
                                    flight.total_mileage_ft, payload.get("total_mileage_ft")
                                ),
                                "altitude_difference_pct": _percentage_difference(
                                    flight.max_altitude_ft, payload.get("max_altitude_ft")
                                ),
                                "reason": "; ".join(selected.reasons),
                            }
                        )
                    elif classification == "AMBIGUOUS_MATCH":
                        row["reason"] = "multiple plausible existing flights were found"
                    else:
                        row["reason"] = "; ".join(primary.reasons) or "no existing flight met the current matching thresholds"
                except DJIImportError as exc:
                    row["classification"] = "PARSER_FAILURE"
                    row["parser_error_code"] = exc.code
                    row["reason"] = "parser failure"
                    counts["PARSER_FAILURE"] += 1
                except Exception:
                    row["classification"] = "PARSER_FAILURE"
                    row["parser_error_code"] = "DJI_PARSER_WORKER_FAILURE"
                    row["reason"] = "parser failure"
                    counts["PARSER_FAILURE"] += 1
                writer.writerow(row)
                report_file.flush()
                if index % 25 == 0 or index == len(files):
                    self.stdout.write(f"Processed {index}/{len(files)}")

        after = (FlightLog.objects.count(), FlightLogSource.objects.count())
        if after != before:
            raise CommandError(f"Database counts changed unexpectedly: before={before}, after={after}")
        self.stdout.write(f"Total: {len(files)}")
        self.stdout.write(f"Parsed: {counts['parsed']}")
        self.stdout.write(f"Parser failures: {counts['PARSER_FAILURE']}")
        for classification in (
            "PRIMARY_HIGH_CONFIDENCE_MATCH",
            "HIGH_CONFIDENCE_LOCATION_VARIANCE",
            "REVIEW_PARTIAL_AIRDATA",
            "PROBABLE_MATCH",
            "AMBIGUOUS_MATCH",
            "NO_MATCH",
        ):
            self.stdout.write(f"{classification}: {counts[classification]}")
        total_high = (
            counts["PRIMARY_HIGH_CONFIDENCE_MATCH"]
            + counts["HIGH_CONFIDENCE_LOCATION_VARIANCE"]
        )
        self.stdout.write(f"TOTAL_HIGH_CONFIDENCE_MATCH: {total_high}")
        self.stdout.write(f"Database before/after: {before}/{after}")
        self.stdout.write(f"Report: {output}")
