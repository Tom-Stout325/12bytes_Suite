from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from assets.models import AssetType
from core.models import Business


DEFAULT_EQUIPMENT_TYPES = (
    ("drone", "Drone", 10),
    ("controller", "Controller", 20),
    ("camera", "Camera", 30),
    ("computer", "Computer", 40),
    ("storage", "Storage", 50),
    ("accessory", "Accessory", 60),
    ("other", "Other", 90),
)


class Command(BaseCommand):
    help = "Create missing default Equipment Types for one business."

    def add_arguments(self, parser):
        parser.add_argument(
            "--business",
            required=True,
            help="Business ID or slug.",
        )

    def handle(self, *args, **options):
        selector = str(options["business"]).strip()
        businesses = Business.objects.filter(pk=selector) if selector.isdigit() else Business.objects.filter(slug=selector)
        business = businesses.first()
        if business is None:
            raise CommandError("Business not found.")

        created = existing = 0
        for slug, name, sort_order in DEFAULT_EQUIPMENT_TYPES:
            match = AssetType.objects.filter(business=business).filter(
                Q(slug=slug) | Q(name__iexact=name)
            ).first()
            if match is not None:
                existing += 1
                continue
            AssetType.objects.create(
                business=business,
                slug=slug,
                name=name,
                sort_order=sort_order,
                is_active=True,
            )
            created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"business={business.pk} created={created} existing={existing}"
            )
        )
