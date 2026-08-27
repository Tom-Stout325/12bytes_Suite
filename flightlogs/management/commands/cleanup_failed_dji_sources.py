from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from flightlogs.models import FlightLogSource


class Command(BaseCommand):
    help = "Delete only unlinked failed DJI source attempts; defaults to a dry run."

    def add_arguments(self, parser):
        parser.add_argument("--business-id", type=int, required=True)
        parser.add_argument("--sha256", required=True)
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options):
        digest = options["sha256"].lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise CommandError("--sha256 must be a 64-character hexadecimal SHA-256 digest.")
        queryset = FlightLogSource.objects.filter(
            business_id=options["business_id"],
            sha256=digest,
            source_type=FlightLogSource.SourceType.DJI_TXT,
            status=FlightLogSource.Status.FAILED,
            flight_log__isnull=True,
        )
        source = queryset.first()
        if source is None:
            self.stdout.write("No matching unlinked failed DJI source was found; nothing changed.")
            return
        if not options["confirm"]:
            self.stdout.write(
                f"Would delete failed source id={source.pk}; rerun with --confirm."
            )
            return
        stored_name = source.file.name if source.file else ""
        storage = source.file.storage if stored_name else None
        with transaction.atomic():
            deleted, _ = queryset.delete()
            if stored_name:
                transaction.on_commit(lambda: storage.delete(stored_name))
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} failed source record."))
