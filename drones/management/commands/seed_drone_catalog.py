from __future__ import annotations

import json
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from drones.models import (
    BatteryFamily,
    DroneModel,
    DroneModelAlias,
    DroneSafetyProfile,
    normalize_catalog_name,
)


CURATED_VARIANTS = (
    {
        "manufacturer": "DJI",
        "name": "Mavic 4 Pro Creator",
        "battery_family": "Mavic 4 Pro",
        "aliases": ("DJI Mavic 4 Pro Creator",),
    },
)
CONFIRMED_BATTERY_FAMILIES = {
    ("DJI", "Mavic 4 Pro"): "Mavic 4 Pro",
    ("DJI", "Mavic 4 Pro Creator"): "Mavic 4 Pro",
}


def split_aliases(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,;\n]+", value or "") if part.strip()]


class Command(BaseCommand):
    help = "Idempotently seed the centrally curated drone product catalog."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            type=Path,
            default=Path(settings.BASE_DIR) / "drones" / "fixtures" / "drone_safety_profiles_updated.json",
            help="Source JSON fixture-shaped catalog file.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        source = Path(options["source"])
        try:
            records = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Unable to read drone catalog source: {exc}") from exc

        counts = {key: 0 for key in (
            "models_created", "models_updated", "profiles_created", "profiles_updated",
            "aliases_created", "aliases_existing",
        )}
        for record in records:
            fields = record.get("fields", {})
            manufacturer = " ".join(str(fields.get("brand", "")).strip().split())
            name = " ".join(str(fields.get("model_name", "")).strip().split())
            if not manufacturer or not name:
                raise CommandError("Each source record requires brand and model_name.")
            model, created = DroneModel.objects.get_or_create(
                normalized_manufacturer=normalize_catalog_name(manufacturer),
                normalized_name=normalize_catalog_name(name),
                defaults={"manufacturer": manufacturer, "name": name},
            )
            if created:
                counts["models_created"] += 1
            changed = []
            for field, value in (
                ("name", name),
                ("year_released", fields.get("year_released")),
                ("is_enterprise", bool(fields.get("is_enterprise", False))),
                ("active", bool(fields.get("active", True))),
            ):
                if getattr(model, field) != value:
                    setattr(model, field, value)
                    changed.append(field)
            if changed:
                model.save(update_fields=[*changed, "normalized_name", "full_display_name", "updated_at"])
                if not created:
                    counts["models_updated"] += 1

            safety_features = fields.get("safety_features", "")
            profile = DroneSafetyProfile.objects.filter(drone_model=model).first()
            if profile is None:
                DroneSafetyProfile.objects.create(
                    drone_model=model,
                    safety_features=safety_features,
                )
                counts["profiles_created"] += 1
            elif profile.safety_features != safety_features:
                profile.safety_features = safety_features
                profile.save(update_fields=["safety_features", "updated_at"])
                counts["profiles_updated"] += 1
            self._aliases(model, split_aliases(fields.get("aka_names", "")), "fixture", counts)

        self._apply_curated_variants(counts)
        self.stdout.write(" ".join(f"{key}={value}" for key, value in counts.items()))

    def _apply_curated_variants(self, counts):
        for item in CURATED_VARIANTS:
            family, _ = BatteryFamily.objects.get_or_create(
                normalized_manufacturer=normalize_catalog_name(item["manufacturer"]),
                normalized_name=normalize_catalog_name(item["battery_family"]),
                defaults={"manufacturer": item["manufacturer"], "name": item["battery_family"]},
            )
            model, created = DroneModel.objects.get_or_create(
                normalized_manufacturer=normalize_catalog_name(item["manufacturer"]),
                normalized_name=normalize_catalog_name(item["name"]),
                defaults={"manufacturer": item["manufacturer"], "name": item["name"], "battery_family": family},
            )
            if created:
                counts["models_created"] += 1
            if model.battery_family_id != family.pk:
                model.battery_family = family
                model.save(update_fields=["battery_family", "updated_at"])
                if not created:
                    counts["models_updated"] += 1
            self._aliases(model, item["aliases"], "curated", counts)

        for (manufacturer, name), family_name in CONFIRMED_BATTERY_FAMILIES.items():
            model = DroneModel.objects.filter(
                normalized_manufacturer=normalize_catalog_name(manufacturer),
                normalized_name=normalize_catalog_name(name),
            ).first()
            if not model:
                continue
            family = BatteryFamily.objects.get(
                normalized_manufacturer=normalize_catalog_name(manufacturer),
                normalized_name=normalize_catalog_name(family_name),
            )
            if model.battery_family_id != family.pk:
                model.battery_family = family
                model.save(update_fields=["battery_family", "updated_at"])
                counts["models_updated"] += 1

    @staticmethod
    def _aliases(model, aliases, source, counts):
        for alias in aliases:
            normalized = normalize_catalog_name(alias)
            existing = DroneModelAlias.objects.filter(normalized_alias=normalized).first()
            if existing and existing.drone_model_id != model.pk:
                raise CommandError(
                    f"Alias {alias!r} already belongs to {existing.drone_model}."
                )
            _, created = DroneModelAlias.objects.get_or_create(
                normalized_alias=normalized,
                defaults={"drone_model": model, "alias": alias, "source": source},
            )
            counts["aliases_created" if created else "aliases_existing"] += 1
