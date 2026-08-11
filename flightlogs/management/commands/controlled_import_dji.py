from __future__ import annotations

import csv
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from core.models import Business, BusinessMembership
from flightlogs.models import FlightLog, FlightLogSource
from flightlogs.services.dji.importer import import_dji_upload
from flightlogs.services.matching import MatchType


EXPECTED_TO_MATCH_TYPE = {
    "PRIMARY_HIGH_CONFIDENCE_MATCH": MatchType.HIGH_CONFIDENCE,
    "HIGH_CONFIDENCE_LOCATION_VARIANCE": MatchType.HIGH_CONFIDENCE_LOCATION_VARIANCE,
    "REVIEW_PARTIAL_AIRDATA": MatchType.REVIEW_PARTIAL_AIRDATA,
    "AMBIGUOUS_MATCH": MatchType.AMBIGUOUS,
    "NO_MATCH": MatchType.NO_MATCH,
}

REPORT_FIELDS = (
    "filename",
    "expected_classification",
    "actual_classification",
    "flightlogsource_id",
    "linked_flightlog_id",
    "new_flightlog_created",
    "review_status",
    "raw_file_retained",
    "existing_airdata_unchanged",
    "duplicate",
    "safe_error_message",
)


def _flight_snapshot(flight):
    return {
        field.attname: field.value_from_object(flight)
        for field in flight._meta.concrete_fields
    }


class Command(BaseCommand):
    help = "DEV ONLY: import an explicit small DJI control set through the real importer."

    def add_arguments(self, parser):
        parser.add_argument("directory")
        parser.add_argument("filenames", nargs="+")
        parser.add_argument("--validation-report", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--business-id", type=int)
        parser.add_argument("--user-id", type=int)
        parser.add_argument("--duplicate-file", required=True)
        parser.add_argument("--confirm-controlled-write", action="store_true")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("This controlled persistence command is disabled unless DEBUG=True.")
        if not options["confirm_controlled_write"]:
            raise CommandError("Pass --confirm-controlled-write to acknowledge local database writes.")
        if settings.DJI_DELETE_SUCCESSFUL_SOURCE_FILES:
            raise CommandError("DJI_DELETE_SUCCESSFUL_SOURCE_FILES must be False.")

        directory = Path(options["directory"]).resolve()
        report_path = Path(options["validation_report"])
        output_path = Path(options["output"])
        filenames = options["filenames"]
        if not directory.is_dir() or not report_path.is_file():
            raise CommandError("DJI directory or validation report was not found.")
        if not 1 <= len(filenames) <= 12 or len(set(filenames)) != len(filenames):
            raise CommandError("Select between 1 and 12 unique filenames.")

        selected_paths = {}
        for filename in filenames:
            path = (directory / filename).resolve()
            if path.parent != directory or path.suffix.lower() != ".txt" or not path.is_file():
                raise CommandError(f"Invalid controlled source filename: {filename}")
            selected_paths[filename] = path

        with report_path.open(encoding="utf-8", newline="") as report_file:
            validation = {row["filename"]: row for row in csv.DictReader(report_file)}
        missing = [filename for filename in filenames if filename not in validation]
        if missing:
            raise CommandError(f"Selected files are missing from validation report: {missing}")
        for filename in filenames:
            expected = validation[filename]["classification"]
            if expected not in EXPECTED_TO_MATCH_TYPE:
                raise CommandError(f"Unsupported expected classification for {filename}: {expected}")

        duplicate_filename = options["duplicate_file"]
        if duplicate_filename not in selected_paths:
            raise CommandError("--duplicate-file must be one of the selected filenames.")
        if validation[duplicate_filename]["classification"] not in {
            "PRIMARY_HIGH_CONFIDENCE_MATCH",
            "HIGH_CONFIDENCE_LOCATION_VARIANCE",
        }:
            raise CommandError("--duplicate-file must be a high-confidence selection.")

        business_ids = list(
            FlightLog.objects.order_by().values_list("business_id", flat=True).distinct()
        )
        business_id = options.get("business_id")
        if business_id is None:
            if len(business_ids) != 1:
                raise CommandError("Specify --business-id unless exactly one FlightLog business exists.")
            business_id = business_ids[0]
        business = Business.objects.filter(pk=business_id).first()
        if business is None or business_id not in business_ids:
            raise CommandError("The selected business has no FlightLog baseline.")

        memberships = BusinessMembership.objects.filter(business=business).select_related("user")
        user_id = options.get("user_id")
        membership = (
            memberships.filter(user_id=user_id).first()
            if user_id is not None
            else memberships.order_by("pk").first()
        )
        if membership is None:
            raise CommandError("A user membership in the selected business is required.")
        user = membership.user

        baseline = (FlightLog.objects.count(), FlightLogSource.objects.count())
        initial_flight_ids = set(FlightLog.objects.values_list("pk", flat=True))
        rows = []
        linked_existing = 0
        created_new = 0
        retained_review = 0
        ambiguous = 0

        self.stdout.write(f"Baseline FlightLog/FlightLogSource: {baseline}")
        for filename in filenames:
            expected = validation[filename]["classification"]
            expected_flight_id = validation[filename]["matched_flightlog_id"]
            existing = None
            before_snapshot = None
            if expected in {
                "PRIMARY_HIGH_CONFIDENCE_MATCH",
                "HIGH_CONFIDENCE_LOCATION_VARIANCE",
            }:
                existing = FlightLog.objects.filter(
                    business=business,
                    pk=expected_flight_id,
                ).first()
                if existing is None:
                    raise CommandError(f"Expected business-owned FlightLog is missing for {filename}.")
                before_snapshot = _flight_snapshot(existing)

            before_logs = FlightLog.objects.count()
            with selected_paths[filename].open("rb") as handle:
                result = import_dji_upload(
                    business=business,
                    user=user,
                    uploaded=File(handle, name=filename),
                )
            source = FlightLogSource.objects.get(pk=result.source.pk)
            after_logs = FlightLog.objects.count()
            actual = result.match_type.value if result.match_type is not None else ""
            expected_match_type = EXPECTED_TO_MATCH_TYPE[expected]
            if result.duplicate or result.match_type != expected_match_type:
                raise CommandError(
                    f"Unexpected importer result for {filename}: expected={expected_match_type.value}, "
                    f"actual={actual or 'duplicate'}"
                )

            new_flight = bool(
                source.flight_log_id
                and source.flight_log_id not in initial_flight_ids
            )
            raw_retained = bool(
                source.file.name and source.file.storage.exists(source.file.name)
            )
            airdata_unchanged = ""
            if existing is not None:
                existing.refresh_from_db()
                airdata_unchanged = _flight_snapshot(existing) == before_snapshot
                if not airdata_unchanged or source.flight_log_id != existing.pk:
                    raise CommandError(f"Existing AirData protection check failed for {filename}.")
                linked_existing += 1
            if expected == "NO_MATCH":
                if after_logs != before_logs + 1 or not new_flight:
                    raise CommandError(f"NO_MATCH did not create exactly one linked FlightLog for {filename}.")
                created_new += 1
            elif after_logs != before_logs:
                raise CommandError(f"Unexpected FlightLog creation for {filename}.")
            if expected in {"REVIEW_PARTIAL_AIRDATA", "AMBIGUOUS_MATCH"}:
                if source.status != FlightLogSource.Status.REVIEW or source.flight_log_id is not None:
                    raise CommandError(f"Review source persistence check failed for {filename}.")
                retained_review += 1
                ambiguous += expected == "AMBIGUOUS_MATCH"
            if not raw_retained:
                raise CommandError(f"Raw source was not retained for {filename}.")

            row = {
                "filename": filename,
                "expected_classification": expected,
                "actual_classification": actual,
                "flightlogsource_id": source.pk,
                "linked_flightlog_id": source.flight_log_id or "",
                "new_flightlog_created": new_flight,
                "review_status": source.status,
                "raw_file_retained": raw_retained,
                "existing_airdata_unchanged": airdata_unchanged,
                "duplicate": False,
                "safe_error_message": source.safe_error_code or source.safe_error_detail,
            }
            rows.append(row)
            self.stdout.write(
                f"{filename}: expected={expected}, actual={actual}, source={source.pk}, "
                f"flight={source.flight_log_id}, new={new_flight}, status={source.status}, retained={raw_retained}"
            )

        before_duplicate = (FlightLog.objects.count(), FlightLogSource.objects.count())
        duplicate_path = selected_paths[duplicate_filename]
        with duplicate_path.open("rb") as handle:
            duplicate_result = import_dji_upload(
                business=business,
                user=user,
                uploaded=File(handle, name=duplicate_filename),
            )
        duplicate_source = FlightLogSource.objects.get(pk=duplicate_result.source.pk)
        after_duplicate = (FlightLog.objects.count(), FlightLogSource.objects.count())
        if not duplicate_result.duplicate or before_duplicate != after_duplicate:
            raise CommandError("Exact SHA-256 duplicate persistence check failed.")
        rows.append(
            {
                "filename": duplicate_filename,
                "expected_classification": "EXACT_SHA256_DUPLICATE",
                "actual_classification": "EXACT_SHA256_DUPLICATE",
                "flightlogsource_id": duplicate_source.pk,
                "linked_flightlog_id": duplicate_source.flight_log_id or "",
                "new_flightlog_created": False,
                "review_status": duplicate_source.status,
                "raw_file_retained": bool(
                    duplicate_source.file.name
                    and duplicate_source.file.storage.exists(duplicate_source.file.name)
                ),
                "existing_airdata_unchanged": True,
                "duplicate": True,
                "safe_error_message": duplicate_source.safe_error_code or duplicate_source.safe_error_detail,
            }
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

        final = (FlightLog.objects.count(), FlightLogSource.objects.count())
        self.stdout.write(f"Final FlightLog/FlightLogSource: {final}")
        self.stdout.write(f"Linked existing: {linked_existing}")
        self.stdout.write(f"Created new: {created_new}")
        self.stdout.write(f"Retained for review: {retained_review}")
        self.stdout.write(f"Ambiguous: {ambiguous}")
        self.stdout.write(
            f"Duplicate reused source={duplicate_source.pk}, flight={duplicate_source.flight_log_id}, "
            f"counts unchanged={before_duplicate == after_duplicate}"
        )
        self.stdout.write(f"Report: {output_path}")
