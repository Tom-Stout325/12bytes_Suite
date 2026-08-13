from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import CompanyProfile
from assets.models import Asset, AssetType
from assets.forms import AssetForm
from core.models import Business, BusinessMembership
from flightlogs.models import FlightLog
from drones.models import BatteryFamily, DroneModel


class EquipmentTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Equipment Business")
        self.other_business = Business.objects.create(name="Other Equipment Business")
        self.user = get_user_model().objects.create_user("equipment-owner", password="test-password")
        BusinessMembership.objects.create(business=self.business, user=self.user, role=BusinessMembership.Role.OWNER)
        CompanyProfile.objects.create(business=self.business, created_by=self.user, company_name="Equipment Business")
        self.drone_type = AssetType.objects.create(business=self.business, name="Drone", slug="drone")
        self.controller_type = AssetType.objects.create(business=self.business, name="Controller", slug="controller")
        self.family = BatteryFamily.objects.create(manufacturer="DJI", name="Mavic 4 Pro")
        self.model = DroneModel.objects.create(
            manufacturer="DJI", name="Mavic 4 Pro", battery_family=self.family,
        )
        self.client.force_login(self.user)

    def equipment(self, name, *, active=True, model=None, asset_type=None, business=None):
        return Asset.objects.create(
            business=business or self.business,
            name=name,
            asset_type=asset_type or self.drone_type,
            is_active=active,
            purchase_date=date(2026, 1, 1),
            purchase_price=Decimal("1000.00"),
            drone_model=model,
        )

    def test_list_requires_authentication_and_is_tenant_scoped(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("assets:asset_list")).status_code, 302)
        self.client.force_login(self.user)
        self.equipment("Mine")
        other_type = AssetType.objects.create(business=self.other_business, name="Drone", slug="drone")
        self.equipment("Not Mine", business=self.other_business, asset_type=other_type)
        response = self.client.get(reverse("assets:asset_list"))
        self.assertContains(response, "Mine")
        self.assertNotContains(response, "Not Mine")

    def test_active_default_show_all_and_toggle_preserve_type(self):
        self.equipment("Active Drone", active=True)
        inactive = self.equipment("Inactive Drone", active=False)
        default = self.client.get(reverse("assets:asset_list"), {"type": self.drone_type.pk})
        self.assertContains(default, "Active Drone")
        self.assertNotContains(default, "Inactive Drone")
        self.assertContains(default, "Show All Equipment")
        self.assertContains(default, f"show=all&amp;type={self.drone_type.pk}")
        all_response = self.client.get(reverse("assets:asset_list"), {"show": "all", "type": self.drone_type.pk})
        self.assertContains(all_response, "Inactive Drone")
        self.assertContains(all_response, "Show Active Equipment")
        self.assertEqual(self.client.get(reverse("assets:asset_detail", args=[inactive.pk])).status_code, 200)

    def test_identity_fields_and_shared_aircraft_model(self):
        first = self.equipment("Primary", model=self.model)
        second = self.equipment("Backup", model=self.model)
        first.manufacturer, first.model, first.serial_number = "DJI", "Mavic 4 Pro", "AIRCRAFT-1"
        first.save()
        first.refresh_from_db()
        self.assertEqual(first.drone_model, second.drone_model)
        self.assertEqual((first.manufacturer, first.model, first.serial_number), ("DJI", "Mavic 4 Pro", "AIRCRAFT-1"))

    def test_creator_variant_is_distinct_but_can_share_battery_family(self):
        creator = DroneModel.objects.create(
            manufacturer="DJI",
            name="Mavic 4 Pro Creator",
            battery_family=self.family,
        )
        self.assertNotEqual(self.model.normalized_name, creator.normalized_name)
        self.assertEqual(creator.normalized_name, "mavic-4-pro-creator")
        self.assertEqual(self.model.battery_family, creator.battery_family)

    def test_model_level_flight_and_battery_summary(self):
        first = self.equipment("Primary", model=self.model)
        second = self.equipment("Backup", model=self.model)
        FlightLog.objects.create(business=self.business, drone_model=self.model, flight_date=date(2026, 8, 1), air_time=timedelta(hours=1), battery_serial_internal="INT-1", battery_serial_printed="PRINT-IGNORED", battery_cycle_count=41, battery_name="Pack A")
        FlightLog.objects.create(business=self.business, drone_model=self.model, flight_date=date(2026, 8, 10), air_time=timedelta(minutes=30), battery_serial_internal="INT-1", battery_cycle_count=42)
        FlightLog.objects.create(business=self.business, drone_model=self.model, flight_date=date(2026, 8, 8), air_time=None, battery_serial_printed="PRINT-2")
        FlightLog.objects.create(business=self.business, drone_model=self.model, flight_date=date(2026, 8, 9), battery_name="No serial")
        other_model = DroneModel.objects.create(manufacturer="DJI", name="Mavic 3 Pro")
        FlightLog.objects.create(business=self.business, drone_model=other_model, flight_date=date(2026, 8, 11), air_time=timedelta(hours=10), battery_serial_internal="OTHER")
        FlightLog.objects.create(business=self.other_business, drone_model=self.model, flight_date=date(2026, 8, 12), battery_serial_internal="FOREIGN")

        for equipment in (first, second):
            response = self.client.get(reverse("assets:asset_detail", args=[equipment.pk]))
            self.assertEqual(response.context["flight_summary"]["flights"], 4)
            self.assertEqual(response.context["flight_summary"]["hours"], 1.5)
            self.assertEqual(response.context["flight_summary"]["last_flight"], date(2026, 8, 10))
            batteries = {item["identifier"]: item for item in response.context["battery_summary"]}
            self.assertEqual(set(batteries), {"INT-1", "PRINT-2"})
            self.assertEqual(batteries["INT-1"]["flights"], 2)
            self.assertEqual(batteries["INT-1"]["cycle_count"], 42)
            self.assertEqual(batteries["INT-1"]["hours"], 1.5)
            self.assertIsNone(batteries["PRINT-2"]["cycle_count"])
            self.assertNotContains(response, "FOREIGN")

    def test_non_aircraft_equipment_has_no_flight_summary(self):
        controller = self.equipment("Controller", asset_type=self.controller_type)
        response = self.client.get(reverse("assets:asset_detail", args=[controller.pk]))
        self.assertNotIn("flight_summary", response.context)

    def test_variant_flights_are_separate_but_family_batteries_are_shared(self):
        creator = DroneModel.objects.create(
            manufacturer="DJI", name="Mavic 4 Pro Creator",
            battery_family=self.family,
        )
        standard_equipment = self.equipment("Standard", model=self.model)
        creator_equipment = self.equipment("Creator", model=creator)
        FlightLog.objects.create(
            business=self.business, drone_model=self.model, flight_date=date(2026, 8, 1),
            air_time=timedelta(hours=1), battery_serial_internal="SHARED", battery_cycle_count=41,
        )
        FlightLog.objects.create(
            business=self.business, drone_model=creator, flight_date=date(2026, 8, 2),
            air_time=timedelta(hours=2), battery_serial_internal="SHARED", battery_cycle_count=42,
        )
        other_family = BatteryFamily.objects.create(manufacturer="DJI", name="Mavic 3")
        other_model = DroneModel.objects.create(manufacturer="DJI", name="Mavic 3 Pro", battery_family=other_family)
        FlightLog.objects.create(
            business=self.business, drone_model=other_model, flight_date=date(2026, 8, 3),
            air_time=timedelta(hours=10), battery_serial_internal="OTHER-FAMILY",
        )
        for equipment, expected_hours in ((standard_equipment, 1.0), (creator_equipment, 2.0)):
            response = self.client.get(reverse("assets:asset_detail", args=[equipment.pk]))
            self.assertEqual(response.context["flight_summary"]["flights"], 1)
            self.assertEqual(response.context["flight_summary"]["hours"], expected_hours)
            self.assertEqual(len(response.context["battery_summary"]), 1)
            battery = response.context["battery_summary"][0]
            self.assertEqual(battery["identifier"], "SHARED")
            self.assertEqual(battery["flights"], 2)
            self.assertEqual(battery["hours"], 3.0)
            self.assertEqual(battery["cycle_count"], 42)

    def test_equipment_terminology_and_urls_remain(self):
        response = self.client.get(reverse("assets:asset_list"))
        self.assertContains(response, "Equipment")
        self.assertNotContains(response, ">Assets<")
        self.assertEqual(reverse("assets:asset_list"), "/assets/")

    def test_normal_equipment_form_hides_internal_canonical_relationship(self):
        form = AssetForm(business=self.business)
        self.assertNotIn("aircraft_model", form.fields)
        self.assertNotIn("battery_family", form.fields)


class EquipmentTypeSeedTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Seed Business")
        self.other_business = Business.objects.create(name="Other Seed Business")

    def run_seed(self):
        output = StringIO()
        call_command("seed_equipment_types", business=str(self.business.pk), stdout=output)
        return output.getvalue()

    def test_seeded_types_appear_in_business_scoped_equipment_form(self):
        AssetType.objects.create(
            business=self.other_business,
            name="Other Tenant Only",
            slug="other-tenant-only",
        )
        output = self.run_seed()
        choices = AssetForm(business=self.business).fields["asset_type"].queryset
        self.assertEqual(
            list(choices.values_list("name", flat=True)),
            ["Drone", "Controller", "Camera", "Computer", "Storage", "Accessory", "Other"],
        )
        self.assertTrue(choices.filter(name="Drone").exists())
        self.assertFalse(choices.filter(name="Other Tenant Only").exists())
        self.assertIn("created=7 existing=0", output)

    def test_seed_is_idempotent_preserves_custom_types_and_omits_battery(self):
        custom = AssetType.objects.create(
            business=self.business,
            name="Thermal Sensor",
            slug="thermal-sensor",
            sort_order=5,
            is_active=False,
        )
        self.run_seed()
        second_output = self.run_seed()
        custom.refresh_from_db()
        self.assertFalse(custom.is_active)
        self.assertEqual(custom.name, "Thermal Sensor")
        self.assertEqual(AssetType.objects.filter(business=self.business).count(), 8)
        self.assertFalse(AssetType.objects.filter(business=self.business, name__iexact="Battery").exists())
        self.assertIn("created=0 existing=7", second_output)

    def test_existing_name_is_not_duplicated_or_renamed(self):
        existing = AssetType.objects.create(
            business=self.business,
            name="Drone",
            slug="custom-drone-slug",
            sort_order=77,
            is_active=False,
        )
        self.run_seed()
        existing.refresh_from_db()
        self.assertEqual((existing.slug, existing.sort_order, existing.is_active), ("custom-drone-slug", 77, False))
        self.assertEqual(AssetType.objects.filter(business=self.business, name__iexact="Drone").count(), 1)
