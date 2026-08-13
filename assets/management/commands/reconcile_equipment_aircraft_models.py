from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from assets.models import Asset
from core.models import Business
from drones.services import resolve_drone_model
from flightlogs.models import FlightLog


class Command(BaseCommand):
    help = "Conservatively link existing Equipment and FlightLogs to the shared drone catalog."

    def add_arguments(self, parser):
        parser.add_argument("--business", help="Business ID or slug")
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        businesses = Business.objects.all()
        selector = options.get("business")
        if selector:
            businesses = businesses.filter(pk=selector) if selector.isdigit() else businesses.filter(slug=selector)
            if not businesses.exists():
                raise CommandError("Business not found.")

        dry_run = options["dry_run"]
        counts = {"equipment": 0, "flights": 0, "ambiguous": 0, "skipped": 0}
        for business in businesses:
            for asset in Asset.objects.filter(business=business, drone_model__isnull=True).select_related("asset_type"):
                if (asset.asset_type.slug or asset.asset_type.name).casefold() not in {"drone", "aircraft", "uas", "uav"}:
                    counts["skipped"] += 1
                    continue
                model_text = asset.model or asset.name
                model = resolve_drone_model(model_text=model_text)
                if not model:
                    counts["ambiguous"] += 1
                    self.stdout.write(f"Equipment unresolved: {asset.name} ({model_text})")
                    continue
                self.stdout.write(f"Equipment: {asset.name} -> Drone Model: {model}")
                counts["equipment"] += 1
                if not dry_run:
                    asset.drone_model = model
                    asset.save(update_fields=["drone_model"])

            for flight in FlightLog.objects.filter(business=business, drone_model__isnull=True):
                model = resolve_drone_model(
                    business=business,
                    drone_serial=flight.drone_serial,
                    model_text=flight.drone_type or flight.drone_name,
                )
                if not model:
                    counts["ambiguous"] += 1
                    continue
                counts["flights"] += 1
                if not dry_run:
                    flight.drone_model = model
                    flight.save(update_fields=["drone_model"])

        if dry_run:
            transaction.set_rollback(True)
        self.stdout.write(" ".join((
            f"equipment_linked={counts['equipment']}",
            f"flight_logs_linked={counts['flights']}",
            f"ambiguous={counts['ambiguous']}",
            f"skipped={counts['skipped']}",
            f"dry_run={dry_run}",
        )))
