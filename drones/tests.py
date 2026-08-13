from __future__ import annotations

import json
import tempfile
from datetime import date
from decimal import Decimal
from io import StringIO

from django.apps import apps
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from assets.models import Asset, AssetType
from core.models import Business
from flightlogs.models import FlightLog

from .models import (
    BatteryFamily,
    DroneModel,
    DroneModelAlias,
    DroneModelIdentifier,
    DroneSafetyProfile,
    normalize_catalog_name,
)
from .services import resolve_drone_model


class DroneCatalogModelTests(TestCase):
    def setUp(self):
        self.family = BatteryFamily.objects.create(manufacturer="DJI", name="Mavic 4 Pro")
        self.standard = DroneModel.objects.create(
            manufacturer="DJI", name="Mavic 4 Pro", battery_family=self.family,
        )
        self.creator = DroneModel.objects.create(
            manufacturer="DJI", name="Mavic 4 Pro Creator", battery_family=self.family,
        )

    def test_variants_are_distinct_and_share_family(self):
        self.assertNotEqual(self.standard.normalized_name, self.creator.normalized_name)
        self.assertEqual(self.creator.normalized_name, "mavic-4-pro-creator")
        self.assertEqual(self.standard.battery_family, self.creator.battery_family)
        self.assertEqual(normalize_catalog_name("Creator"), "creator")

    def test_global_model_uniqueness_is_case_insensitive(self):
        with self.assertRaises(IntegrityError):
            DroneModel.objects.create(manufacturer="dji", name="MAVIC 4 PRO")

    def test_alias_and_identifier_are_unambiguous(self):
        DroneModelAlias.objects.create(drone_model=self.standard, alias="M4P", source="fixture")
        DroneModelIdentifier.objects.create(drone_model=self.standard, provider="Test Provider", identifier="confirmed-code")
        self.assertEqual(resolve_drone_model(model_text="M4P"), self.standard)
        self.assertEqual(resolve_drone_model(provider="test provider", identifier="confirmed-code"), self.standard)
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                DroneModelAlias.objects.create(drone_model=self.creator, alias="m4p")
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                DroneModelIdentifier.objects.create(drone_model=self.creator, provider="TEST PROVIDER", identifier="confirmed-code")

    def test_no_fuzzy_matching_and_generic_name_never_selects_creator(self):
        self.assertEqual(resolve_drone_model(model_text="Mavic 4 Pro"), self.standard)
        self.assertIsNone(resolve_drone_model(model_text="Mavic 4"))
        self.assertIsNone(resolve_drone_model(model_text="Mavic 4 Pro Creat"))

    def test_serial_evidence_is_business_scoped_and_precedes_generic_text(self):
        business = Business.objects.create(name="Owner")
        other = Business.objects.create(name="Other")
        drone_type = AssetType.objects.create(business=business, name="Drone", slug="drone")
        other_type = AssetType.objects.create(business=other, name="Drone", slug="drone")
        Asset.objects.create(
            business=business, name="Creator", asset_type=drone_type,
            purchase_date=date(2026, 1, 1), purchase_price=Decimal("1"),
            serial_number="CREATOR-SERIAL", drone_model=self.creator,
        )
        Asset.objects.create(
            business=other, name="Standard", asset_type=other_type,
            purchase_date=date(2026, 1, 1), purchase_price=Decimal("1"),
            serial_number="OTHER-SERIAL", drone_model=self.standard,
        )
        self.assertEqual(resolve_drone_model(
            business=business, drone_serial="CREATOR-SERIAL", model_text="Mavic 4 Pro"
        ), self.creator)
        self.assertEqual(resolve_drone_model(
            business=business, drone_serial="OTHER-SERIAL", model_text="Mavic 4 Pro"
        ), self.standard)

    def test_asset_and_flightlog_keep_raw_identity_with_catalog_relationship(self):
        business = Business.objects.create(name="Logs")
        drone_type = AssetType.objects.create(business=business, name="Drone", slug="drone")
        asset = Asset.objects.create(
            business=business, name="Primary", asset_type=drone_type,
            purchase_date=date(2026, 1, 1), purchase_price=Decimal("1"),
            manufacturer="DJI", model="Entered model", serial_number="RAW-AIRCRAFT",
            drone_model=self.creator,
        )
        flight = FlightLog.objects.create(
            business=business, flight_date=date(2026, 1, 2),
            drone_name="Pilot label", drone_type="Raw source type",
            drone_serial="RAW-AIRCRAFT", drone_reg_number="FA123",
            drone_model=self.creator,
        )
        self.assertEqual(asset.model, "Entered model")
        self.assertEqual(
            (flight.drone_name, flight.drone_type, flight.drone_serial, flight.drone_reg_number),
            ("Pilot label", "Raw source type", "RAW-AIRCRAFT", "FA123"),
        )
        self.assertEqual(flight.drone_model, self.creator)

    def test_no_individual_battery_model_exists(self):
        with self.assertRaises(LookupError):
            apps.get_model("drones", "Battery")


class SeedDroneCatalogTests(TestCase):
    def source_file(self):
        records = [{
            "model": "drones.dronesafetyprofile",
            "pk": 98765,
            "fields": {
                "brand": "DJI", "model_name": "Mavic 4 Pro",
                "full_display_name": "DJI Mavic 4 Pro",
                "aka_names": "Mavic 4 Pro\nM4P", "year_released": 2025,
                "safety_features": "Reviewed safety narrative.",
                "active": True, "is_enterprise": False,
            },
        }]
        handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(records, handle)
        handle.close()
        return handle.name

    def test_seed_ignores_pks_preserves_prose_and_is_idempotent(self):
        source = self.source_file()
        first = StringIO()
        call_command("seed_drone_catalog", source=source, stdout=first)
        standard = DroneModel.objects.get(normalized_name="mavic-4-pro")
        creator = DroneModel.objects.get(normalized_name="mavic-4-pro-creator")
        self.assertNotEqual(standard.pk, 98765)
        self.assertEqual(standard.safety_profile.safety_features, "Reviewed safety narrative.")
        self.assertFalse(hasattr(creator, "safety_profile"))
        self.assertEqual(standard.battery_family, creator.battery_family)
        self.assertEqual(DroneModel.objects.count(), 2)
        self.assertEqual(DroneSafetyProfile.objects.count(), 1)
        self.assertEqual(DroneModelAlias.objects.count(), 3)

        second = StringIO()
        call_command("seed_drone_catalog", source=source, stdout=second)
        self.assertEqual(DroneModel.objects.count(), 2)
        self.assertEqual(DroneSafetyProfile.objects.count(), 1)
        self.assertEqual(DroneModelAlias.objects.count(), 3)
        self.assertIn("models_created=0", second.getvalue())
        self.assertIn("aliases_created=0", second.getvalue())

    def test_safety_profile_contains_no_invented_structured_specs(self):
        field_names = {field.name for field in DroneSafetyProfile._meta.fields}
        self.assertEqual(field_names, {"id", "drone_model", "safety_features", "created_at", "updated_at"})
