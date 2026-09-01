from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_date

from core.models import Business
from flightlogs.models import FlightLogSource
from flightlogs.services.dji.importer import _flightlog_payload
from flightlogs.services.dji.subprocess_adapter import parse_dji_source


class Command(BaseCommand):
    help = "Recalculate photo/video counts from retained DJI FlightRecord source files."

    def add_arguments(self, parser):
        parser.add_argument("--business", type=int, required=True, help="Business primary key.")
        parser.add_argument("--flight-log-id", type=int)
        parser.add_argument("--start-date", type=str, help="Inclusive flight date (YYYY-MM-DD).")
        parser.add_argument("--end-date", type=str, help="Inclusive flight date (YYYY-MM-DD).")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--overwrite-existing",
            action="store_true",
            help="Replace existing counts, including confirmed zero or manually entered values.",
        )

    def handle(self, *args, **options):
        business_id = options["business"]
        if not Business.objects.filter(pk=business_id).exists():
            raise CommandError(f"Business {business_id} does not exist.")

        sources = (
            FlightLogSource.objects.filter(
                business_id=business_id,
                source_type=FlightLogSource.SourceType.DJI_TXT,
                flight_log__isnull=False,
            )
            .select_related("flight_log")
            .order_by("flight_log_id", "id")
        )
        if options["flight_log_id"]:
            sources = sources.filter(flight_log_id=options["flight_log_id"])
        for option_name, lookup in (
            ("start_date", "flight_log__flight_date__gte"),
            ("end_date", "flight_log__flight_date__lte"),
        ):
            raw_date = options[option_name]
            if raw_date:
                parsed_date = parse_date(raw_date)
                if parsed_date is None:
                    raise CommandError(f"--{option_name.replace('_', '-')} must be YYYY-MM-DD.")
                sources = sources.filter(**{lookup: parsed_date})

        examined = changed = unchanged = unavailable = failed = 0
        for source in sources.iterator():
            examined += 1
            flight = source.flight_log
            try:
                if not source.file:
                    unavailable += 1
                    self.stdout.write(
                        f"flight_log={flight.pk} source={source.pk} skipped: source file unavailable"
                    )
                    continue
                parsed = parse_dji_source(source.file)
                payload = _flightlog_payload(parsed)
                proposed_photos = payload["photos"]
                proposed_videos = payload["videos"]
                self.stdout.write(
                    f"flight_log={flight.pk} source={source.pk} "
                    f"photos={flight.photos!r}->{proposed_photos!r} "
                    f"videos={flight.videos!r}->{proposed_videos!r}"
                )
                if proposed_photos is None and proposed_videos is None:
                    unavailable += 1
                    continue

                updates = {}
                for field, proposed in (
                    ("photos", proposed_photos),
                    ("videos", proposed_videos),
                ):
                    current = getattr(flight, field)
                    if proposed is not None and (
                        current is None or options["overwrite_existing"]
                    ) and current != proposed:
                        updates[field] = proposed

                if not updates:
                    unchanged += 1
                    continue
                changed += 1
                if not options["dry_run"]:
                    with transaction.atomic():
                        locked = type(flight).objects.select_for_update().get(
                            pk=flight.pk,
                            business_id=business_id,
                        )
                        for field, value in updates.items():
                            setattr(locked, field, value)
                        locked.save(update_fields=list(updates))
            except Exception as exc:
                failed += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"flight_log={flight.pk} source={source.pk} failed: {type(exc).__name__}: {exc}"
                    )
                )

        mode = "DRY RUN" if options["dry_run"] else "UPDATED"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: examined={examined} changed={changed} unchanged={unchanged} "
                f"unavailable={unavailable} failed={failed}"
            )
        )
