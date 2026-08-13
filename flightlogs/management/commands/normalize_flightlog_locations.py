from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from core.models import Business
from flightlogs.models import FlightLog
from flightlogs.services.locations import enrich_flightlog_location


class Command(BaseCommand):
    help = "Populate normalized FlightLog takeoff locations from addresses and optional reverse geocoding."

    def add_arguments(self, parser):
        parser.add_argument(
            "--business",
            help="Limit processing to one Business ID or slug. Omit to process all businesses.",
        )
        parser.add_argument("--force", action="store_true", help="Refresh already-populated normalized fields.")
        parser.add_argument(
            "--no-geocode",
            action="store_true",
            help="Parse stored addresses only; never make reverse-geocoding requests.",
        )
        parser.add_argument("--limit", type=int, help="Process at most this many rows.")

    def handle(self, *args, **options):
        business_value = (options.get("business") or "").strip()
        force = bool(options.get("force"))
        allow_geocode = not bool(options.get("no_geocode"))
        limit = options.get("limit")
        if limit is not None and limit <= 0:
            raise CommandError("--limit must be a positive integer.")

        queryset = FlightLog.objects.select_related("business").order_by("business_id", "pk")
        if business_value:
            business = None
            if business_value.isdigit():
                business = Business.objects.filter(pk=int(business_value)).first()
            if business is None:
                business = Business.objects.filter(slug=business_value).first()
            if business is None:
                raise CommandError("No matching business was found.")
            queryset = queryset.filter(business=business)
        if not force:
            queryset = queryset.filter(
                Q(takeoff_city="")
                | Q(takeoff_state="")
                | Q(takeoff_country="")
                | Q(takeoff_postal_code="")
            )
        if limit is not None:
            queryset = queryset[:limit]

        counts = {
            "processed": 0,
            "updated": 0,
            "parsed_from_address": 0,
            "reverse_geocoded": 0,
            "skipped": 0,
            "unable_to_normalize": 0,
            "errors": 0,
        }
        for flight_log in queryset.iterator(chunk_size=500):
            counts["processed"] += 1
            try:
                result = enrich_flightlog_location(
                    flight_log,
                    allow_geocode=allow_geocode,
                    force=force,
                )
            except Exception:
                counts["errors"] += 1
                continue
            if result.updated_fields:
                counts["updated"] += 1
                if result.source == "address":
                    counts["parsed_from_address"] += 1
                elif result.source == "geocode":
                    counts["reverse_geocoded"] += 1
            elif any(
                (
                    flight_log.takeoff_city,
                    flight_log.takeoff_state,
                    flight_log.takeoff_country,
                    flight_log.takeoff_postal_code,
                )
            ):
                counts["skipped"] += 1
            else:
                counts["unable_to_normalize"] += 1

        self.stdout.write(
            " ".join(f"{name}={value}" for name, value in counts.items())
        )
