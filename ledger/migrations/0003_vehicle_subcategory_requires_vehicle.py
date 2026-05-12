from __future__ import annotations

from django.db import migrations
from django.db.models import Q


VEHICLE_SUBCATEGORY_SLUGS = [
    "vehicle-equipment-purchases",
    "vehicle-gas",
    "vehicle-loan-interest",
    "vehicle-loan-payments",
    "vehicle-maintenance",
    "vehicle-other-expenses",
    "vehicle-repairs",
]

VEHICLE_SUBCATEGORY_NAMES = [
    "Vehicle: Equipment Purchases",
    "Vehicle: Gas",
    "Vehicle: Loan Interest",
    "Vehicle: Loan Payments",
    "Vehicle: Maintenance",
    "Vehicle: Other Expenses",
    "Vehicle: Repairs",
]


def require_vehicle_not_asset(apps, schema_editor):
    SubCategory = apps.get_model("ledger", "SubCategory")
    SubCategory.objects.filter(
        Q(slug__in=VEHICLE_SUBCATEGORY_SLUGS) | Q(name__in=VEHICLE_SUBCATEGORY_NAMES)
    ).update(requires_asset=False, requires_vehicle=True)


def restore_previous_asset_rules(apps, schema_editor):
    SubCategory = apps.get_model("ledger", "SubCategory")
    SubCategory.objects.filter(
        slug__in=[
            "vehicle-loan-interest",
            "vehicle-loan-payments",
            "vehicle-maintenance",
            "vehicle-other-expenses",
            "vehicle-repairs",
        ]
    ).update(requires_asset=True, requires_vehicle=False)
    SubCategory.objects.filter(slug="vehicle-equipment-purchases").update(
        requires_asset=False, requires_vehicle=True
    )
    SubCategory.objects.filter(slug="vehicle-gas").update(
        requires_asset=False, requires_vehicle=False
    )


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(require_vehicle_not_asset, restore_previous_asset_rules),
    ]
