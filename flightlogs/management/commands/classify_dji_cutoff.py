from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError

from flightlogs.models import FlightLog, FlightLogSource
from flightlogs.services.airdata_timezones import get_timezone_finder
from flightlogs.services.matching import _coordinates, _distance_meters


CUTOFF = date(2025, 9, 6)
ANALYZED_CLASSIFICATIONS = {
    "REVIEW_PARTIAL_AIRDATA",
    "AMBIGUOUS_MATCH",
    "NO_MATCH",
}
REPORT_FIELDS = (
    "filename",
    "current_classification",
    "cutoff_classification",
    "dji_takeoff_datetime_utc",
    "local_takeoff_datetime",
    "resolved_timezone",
    "aircraft_serial",
    "takeoff_latitude",
    "takeoff_longitude",
    "duration_seconds",
    "total_distance_ft",
    "max_altitude_ft",
    "cutoff_period",
    "current_match_reason",
    "candidate_flightlog_ids",
    "candidate_provenance",
    "evidence_summary",
    "review_required",
    "controlled_source_id",
)


def _local_time(row):
    takeoff = datetime.fromisoformat(row["dji_takeoff_datetime"])
    coordinates = _coordinates(
        f"{row['takeoff_latitude']}, {row['takeoff_longitude']}"
    )
    if coordinates is None:
        return None, "UNRESOLVED_MISSING_COORDINATES"
    timezone_name = get_timezone_finder().certain_timezone_at(
        lat=coordinates[0], lng=coordinates[1]
    )
    if not timezone_name:
        return None, "UNRESOLVED_TIMEZONE"
    return takeoff.astimezone(ZoneInfo(timezone_name)), timezone_name


class Command(BaseCommand):
    help = "Classify unmatched DJI validation rows around the AirData cutoff without writes."

    def add_arguments(self, parser):
        parser.add_argument("validation_report")
        parser.add_argument("--segmentation-report", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--business-id", type=int)

    def handle(self, *args, **options):
        validation_path = Path(options["validation_report"])
        segmentation_path = Path(options["segmentation_report"])
        output_path = Path(options["output"])
        if not validation_path.is_file() or not segmentation_path.is_file():
            raise CommandError("Validation or segmentation report was not found.")

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
        with validation_path.open(encoding="utf-8", newline="") as handle:
            all_validation = list(csv.DictReader(handle))
        with segmentation_path.open(encoding="utf-8", newline="") as handle:
            segmentation = {row["filename"]: row for row in csv.DictReader(handle)}

        controlled_sources = {
            source.original_filename: source
            for source in FlightLogSource.objects.filter(business_id=business_id)
        }
        rows = []
        counts = {
            "no_match_pre": 0,
            "no_match_post": 0,
            "expected_new_total": 0,
            "expected_new_remaining": 0,
            "pre_review": 0,
        }
        for validation in all_validation:
            current = validation["classification"]
            if current not in ANALYZED_CLASSIFICATIONS:
                continue
            local_takeoff, timezone_name = _local_time(validation)
            utc_takeoff = datetime.fromisoformat(validation["dji_takeoff_datetime"])
            cutoff_date = (local_takeoff or utc_takeoff).date()
            cutoff_period = "ON_OR_BEFORE_2025_09_06" if cutoff_date <= CUTOFF else "AFTER_2025_09_06"
            segment = segmentation.get(validation["filename"], {})
            source = controlled_sources.get(validation["filename"])
            candidate_ids = validation["candidate_flightlog_ids"]
            if not candidate_ids and segment.get("original_candidate_id"):
                candidate_ids = segment["original_candidate_id"]
            candidate_provenance = self._candidate_provenance(
                candidate_ids, business_id
            )

            if current == "REVIEW_PARTIAL_AIRDATA":
                classification = (
                    "PRE_CUTOFF_POSSIBLE_MATCH"
                    if cutoff_period.startswith("ON_OR_BEFORE")
                    else "POST_CUTOFF_PARTIAL_EXISTING_BASELINE_REVIEW"
                )
                evidence = (
                    "exact serial, close time/location, and materially shorter existing duration/distance; "
                    "same-flight evidence is strong but row-level AirData source provenance is unavailable"
                )
                review_required = True
            elif current == "AMBIGUOUS_MATCH":
                classification = (
                    "PRE_CUTOFF_REVIEW"
                    if cutoff_period.startswith("ON_OR_BEFORE")
                    else "POST_CUTOFF_AMBIGUOUS_EXISTING_BASELINE_REVIEW"
                )
                evidence = "multiple existing baseline candidates remain plausible"
                review_required = True
            elif cutoff_period.startswith("ON_OR_BEFORE"):
                counts["no_match_pre"] += 1
                classification, evidence = self._classify_pre_cutoff(
                    validation, business_id
                )
                review_required = True
                counts["pre_review"] += 1
            else:
                counts["no_match_post"] += 1
                analysis_class = segment.get("analysis_classification", "")
                original_candidate = segment.get("original_candidate_id", "")
                if source and source.flight_log_id:
                    classification = "POST_CUTOFF_NEW_FLIGHT_ALREADY_IMPORTED_CONTROL"
                    evidence = "no same-flight baseline candidate; controlled import created and linked a new FlightLog"
                    review_required = False
                    counts["expected_new_total"] += 1
                elif analysis_class == "SIMPLE_DURATION_SEMANTICS":
                    classification = "POST_CUTOFF_POSSIBLE_EXISTING_MATCH"
                    evidence = "exact serial/time/location/distance agreement indicates an existing same physical flight despite duration disagreement"
                    review_required = True
                elif original_candidate:
                    classification = "POST_CUTOFF_EXISTING_CANDIDATE_REVIEW"
                    evidence = (
                        "an exact-serial same-time baseline candidate exists, but current normalized evidence conflicts or is incomplete"
                    )
                    review_required = True
                else:
                    classification = "POST_CUTOFF_NEW_FLIGHT"
                    evidence = "no broad same-aircraft same-time baseline candidate was identified"
                    review_required = False
                    counts["expected_new_total"] += 1
                    counts["expected_new_remaining"] += 1

            rows.append(
                {
                    "filename": validation["filename"],
                    "current_classification": current,
                    "cutoff_classification": classification,
                    "dji_takeoff_datetime_utc": validation["dji_takeoff_datetime"],
                    "local_takeoff_datetime": local_takeoff.isoformat() if local_takeoff else "",
                    "resolved_timezone": timezone_name,
                    "aircraft_serial": validation["aircraft_serial"],
                    "takeoff_latitude": validation["takeoff_latitude"],
                    "takeoff_longitude": validation["takeoff_longitude"],
                    "duration_seconds": validation["duration_seconds"],
                    "total_distance_ft": validation["total_distance_ft"],
                    "max_altitude_ft": validation["max_altitude_ft"],
                    "cutoff_period": cutoff_period,
                    "current_match_reason": validation["reason"],
                    "candidate_flightlog_ids": candidate_ids,
                    "candidate_provenance": candidate_provenance,
                    "evidence_summary": evidence,
                    "review_required": review_required,
                    "controlled_source_id": source.pk if source else "",
                }
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

        after = (FlightLog.objects.count(), FlightLogSource.objects.count())
        if after != before:
            raise CommandError(f"Database counts changed unexpectedly: before={before}, after={after}")

        validation_counts = {}
        for row in all_validation:
            validation_counts[row["classification"]] = validation_counts.get(row["classification"], 0) + 1
        self.stdout.write(
            "Current high confidence: "
            f"{validation_counts.get('PRIMARY_HIGH_CONFIDENCE_MATCH', 0) + validation_counts.get('HIGH_CONFIDENCE_LOCATION_VARIANCE', 0)}"
        )
        self.stdout.write(f"Partial reviews: {validation_counts.get('REVIEW_PARTIAL_AIRDATA', 0)}")
        self.stdout.write(f"Ambiguous: {validation_counts.get('AMBIGUOUS_MATCH', 0)}")
        self.stdout.write(f"No match: {validation_counts.get('NO_MATCH', 0)}")
        self.stdout.write(f"No match on/before cutoff: {counts['no_match_pre']}")
        self.stdout.write(f"No match after cutoff: {counts['no_match_post']}")
        self.stdout.write(f"Expected new post-cutoff total: {counts['expected_new_total']}")
        self.stdout.write(f"Expected new post-cutoff remaining: {counts['expected_new_remaining']}")
        self.stdout.write(f"Pre-cutoff requiring review: {counts['pre_review']}")
        self.stdout.write(f"Database before/after: {before}/{after}")
        self.stdout.write(f"Report: {output_path}")

    @staticmethod
    def _candidate_provenance(candidate_ids, business_id):
        ids = [int(value) for value in candidate_ids.split(";") if value]
        if not ids:
            return "no candidate"
        candidates = list(
            FlightLog.objects.filter(business_id=business_id, pk__in=ids).prefetch_related("sources")
        )
        if len(candidates) != len(ids):
            return "candidate missing or outside selected business"
        if any(candidate.sources.exists() for candidate in candidates):
            return "candidate has DJI source provenance"
        return "pre-control source-less baseline row; originating CSV is not recorded per row"

    @staticmethod
    def _classify_pre_cutoff(validation, business_id):
        takeoff = datetime.fromisoformat(validation["dji_takeoff_datetime"])
        serial = validation["aircraft_serial"].strip()
        incoming_coordinates = _coordinates(
            f"{validation['takeoff_latitude']}, {validation['takeoff_longitude']}"
        )
        candidates = FlightLog.objects.filter(
            business_id=business_id,
            drone_serial=serial,
            takeoff_datetime__gte=takeoff - timedelta(hours=6),
            takeoff_datetime__lte=takeoff + timedelta(hours=6),
        )
        evidence = []
        for candidate in candidates:
            time_difference = abs((candidate.takeoff_datetime - takeoff).total_seconds())
            candidate_coordinates = _coordinates(candidate.takeoff_latlong)
            location_difference = (
                _distance_meters(candidate_coordinates, incoming_coordinates)
                if candidate_coordinates and incoming_coordinates
                else None
            )
            evidence.append((time_difference, location_difference, candidate.pk))
        if any(time <= 300 and location is not None and location <= 1000 for time, location, _ in evidence):
            return "PRE_CUTOFF_POSSIBLE_MATCH", "broad same-aircraft time/location search found a plausible existing candidate"
        if not evidence:
            return "PRE_CUTOFF_LIKELY_MISSING_FROM_CSV", "no same-aircraft FlightLog exists within six hours"
        return "PRE_CUTOFF_REVIEW", "same-aircraft records exist broadly nearby but evidence is insufficient"
