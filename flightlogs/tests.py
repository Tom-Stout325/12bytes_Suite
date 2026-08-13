from __future__ import annotations

import json
import subprocess
import tempfile
from io import StringIO
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.models import CompanyProfile
from assets.models import AircraftModel
from core.models import Business, BusinessMembership

from .models import FlightLog, FlightLogSource
from .services.dji.errors import import_error
from .services.dji.subprocess_adapter import parse_dji_source
from .services.matching import MatchType, match_existing_flight
from .services.aircraft_models import resolve_aircraft_model
from .services.weather import enrich_flightlog_weather
from .services.locations import (
    LocationComponents,
    clear_location_cache,
    enrich_flightlog_location,
    parse_takeoff_address,
)
from .views import _flightlog_payload_from_csv_row, _normalised_row


class AircraftModelResolverTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Resolver Business")
        self.other = Business.objects.create(name="Other Resolver Business")
        self.mavic_pro = AircraftModel.objects.create(
            business=self.business, manufacturer="DJI", name="Mavic 3 Pro",
            aliases=["DJI Mavic 3 Pro"], dji_model_code=84,
        )
        self.mavic_classic = AircraftModel.objects.create(
            business=self.business, manufacturer="DJI", name="Mavic 3 Classic"
        )

    def test_exact_alias_normalization_and_distinct_models(self):
        self.assertEqual(resolve_aircraft_model(business=self.business, drone_type="MAVIC 3 PRO"), self.mavic_pro)
        self.assertEqual(resolve_aircraft_model(business=self.business, drone_type="DJI Mavic 3 Pro"), self.mavic_pro)
        self.assertEqual(resolve_aircraft_model(business=self.business, drone_type="Mavic 3 Classic"), self.mavic_classic)
        self.assertIsNone(resolve_aircraft_model(business=self.business, drone_type="Mavic 4 Pro"))

    def test_dji_code_priority_and_tenant_isolation(self):
        AircraftModel.objects.create(business=self.other, name="Other Model", dji_model_code=84)
        self.assertEqual(resolve_aircraft_model(business=self.business, drone_type="unknown", dji_model_code=84), self.mavic_pro)
        self.assertIsNone(resolve_aircraft_model(business=self.other, drone_type="Mavic 3 Pro"))

    def test_fly_more_combo_is_removed_only_for_exact_known_model(self):
        self.assertEqual(
            resolve_aircraft_model(business=self.business, drone_type="Mavic 3 Pro Fly More Combo"),
            self.mavic_pro,
        )
        self.assertIsNone(resolve_aircraft_model(business=self.business, drone_type="Mystery Drone Combo"))


class TakeoffAddressParserTests(SimpleTestCase):
    def assert_location(self, raw, city, state, postal, country):
        result = parse_takeoff_address(raw)
        self.assertEqual((result.city, result.state, result.postal_code, result.country), (city, state, postal, country))

    def test_supported_us_addresses(self):
        self.assert_location("1661 Fairplex Dr, La Verne, CA 91750, USA", "La Verne", "CA", "91750", "USA")
        self.assert_location("100 Albemarle House Dr, Charlottesville, VA 22902, USA", "Charlottesville", "VA", "22902", "USA")
        self.assert_location("123 Main St, Salt Lake City, UT 84101, USA", "Salt Lake City", "UT", "84101", "USA")
        self.assert_location("123 Main St, Springfield, IL 62701-1234, USA", "Springfield", "IL", "62701-1234", "USA")
        self.assert_location("123 Main St, Las Vegas, NV 89101", "Las Vegas", "NV", "89101", "USA")

    def test_blank_and_malformed_addresses_are_safe(self):
        self.assertEqual(parse_takeoff_address(""), LocationComponents())
        self.assertEqual(parse_takeoff_address("10965 Olio Rd"), LocationComponents())
        self.assertEqual(parse_takeoff_address("not an address"), LocationComponents())

    def test_international_address_does_not_invent_us_fields(self):
        result = parse_takeoff_address("10 Downing St, London, United Kingdom")
        self.assertEqual((result.city, result.state, result.postal_code), ("", "", ""))
        self.assertEqual(result.country, "United Kingdom")


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class FlightLogListFilterTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Filter Business")
        self.other_business = Business.objects.create(name="Other Filter Business")
        self.user = get_user_model().objects.create_user("filter-owner", password="test-password")
        BusinessMembership.objects.create(
            business=self.business,
            user=self.user,
            role=BusinessMembership.Role.OWNER,
        )
        CompanyProfile.objects.create(
            business=self.business,
            created_by=self.user,
            company_name="Filter Business",
        )
        self.client.force_login(self.user)
        self.url = reverse("flightlogs:flightlog_list")

    def _flight(self, flight_date, *, address="", city="", state=""):
        return FlightLog.objects.create(
            business=self.business,
            flight_date=flight_date,
            takeoff_address=address,
            takeoff_city=city,
            takeoff_state=state,
        )

    def test_year_month_choices_are_distinct_ordered_and_tenant_scoped(self):
        self._flight(date(2023, 3, 1), address="10965 Olio Rd", city="Fishers", state="IN")
        self._flight(date(2023, 3, 2), address="1 Cooper St", city="Fishers", state="IN")
        self._flight(date(2022, 1, 1), address="10 Main St", city="Anderson", state="IN")
        self._flight(date(2024, 12, 1), address="Blank City Rd")
        FlightLog.objects.create(
            business=self.other_business,
            flight_date=date(2030, 6, 1),
            takeoff_address="Other Tenant St",
            takeoff_city="Outside City",
            takeoff_state="OH",
        )

        response = self.client.get(self.url)

        self.assertEqual(response.context["years"], [2024, 2023, 2022])
        self.assertEqual(response.context["months_present"], [1, 3, 12])
        self.assertEqual(response.context["cities"], ["Anderson", "Fishers"])
        self.assertEqual(response.context["states"], ["IN"])
        self.assertNotIn("1 Cooper St", response.context["cities"])
        self.assertNotIn("10965 Olio Rd", response.context["cities"])
        self.assertContains(response, 'name="city"')
        self.assertContains(response, 'name="state"')
        self.assertNotIn(2030, response.context["years"])
        self.assertNotIn(6, response.context["months_present"])

    def test_existing_year_and_month_filters_still_return_correct_records(self):
        expected = self._flight(date(2023, 3, 1), address="10965 Olio Rd")
        self._flight(date(2023, 4, 1), address="Second St")
        self._flight(date(2022, 3, 1), address="Fourth St")

        response = self.client.get(self.url, {"year": "2023", "month": "3"})

        self.assertEqual(response.context["total_flights"], 1)
        self.assertEqual([log.pk for log in response.context["logs"]], [expected.pk])

    def test_state_and_city_filters_use_normalized_fields(self):
        expected = self._flight(date(2023, 3, 1), address="Street A", city="Fishers", state="IN")
        self._flight(date(2023, 3, 2), address="Street B", city="Columbus", state="OH")
        self._flight(date(2023, 3, 3), address="Street C", city="Indianapolis", state="IN")

        response = self.client.get(self.url, {"state": "IN", "city": "Fishers"})

        self.assertEqual(response.context["cities"], ["Fishers", "Indianapolis"])
        self.assertEqual(response.context["total_flights"], 1)
        self.assertEqual([log.pk for log in response.context["logs"]], [expected.pk])


class FlightLogLocationServiceTests(TestCase):
    def setUp(self):
        clear_location_cache()
        self.business = Business.objects.create(name="Location Service")

    def test_address_enrichment_preserves_raw_address(self):
        raw = "1661 Fairplex Dr, La Verne, CA 91750, USA"
        flight = FlightLog.objects.create(business=self.business, flight_date=date(2026, 1, 1), takeoff_address=raw)
        result = enrich_flightlog_location(flight, allow_geocode=False)
        flight.refresh_from_db()
        self.assertEqual(result.source, "address")
        self.assertEqual(flight.takeoff_address, raw)
        self.assertEqual((flight.takeoff_city, flight.takeoff_state, flight.takeoff_postal_code, flight.takeoff_country), ("La Verne", "CA", "91750", "USA"))

    @mock.patch("flightlogs.services.locations.reverse_geocode_coordinates")
    def test_reverse_geocode_and_coordinate_cache(self, reverse_mock):
        reverse_mock.return_value = LocationComponents("Fishers", "IN", "USA", "46037")
        first = FlightLog.objects.create(business=self.business, flight_date=date(2026, 1, 1), takeoff_latlong="39.956754, -85.968544")
        second = FlightLog.objects.create(business=self.business, flight_date=date(2026, 1, 2), takeoff_latlong="39.9567539, -85.9685441")
        enrich_flightlog_location(first)
        enrich_flightlog_location(second)
        second.refresh_from_db()
        reverse_mock.assert_called_once()
        self.assertEqual((second.takeoff_city, second.takeoff_state), ("Fishers", "IN"))

    @mock.patch("flightlogs.services.locations.reverse_geocode_coordinates")
    def test_populated_or_invalid_coordinate_does_not_geocode(self, reverse_mock):
        populated = FlightLog.objects.create(business=self.business, flight_date=date(2026, 1, 1), takeoff_latlong="39.9, -85.9", takeoff_city="Fishers", takeoff_state="IN")
        invalid = FlightLog.objects.create(business=self.business, flight_date=date(2026, 1, 2), takeoff_latlong="invalid")
        enrich_flightlog_location(populated)
        enrich_flightlog_location(invalid)
        reverse_mock.assert_not_called()

    @mock.patch("flightlogs.services.locations._fetch_json", side_effect=TimeoutError)
    def test_geocoder_timeout_is_nonfatal(self, _fetch_mock):
        flight = FlightLog.objects.create(business=self.business, flight_date=date(2026, 1, 1), takeoff_latlong="39.9, -85.9")
        result = enrich_flightlog_location(flight)
        self.assertEqual(result.updated_fields, ())
        self.assertTrue(FlightLog.objects.filter(pk=flight.pk).exists())


class NormalizeFlightLogLocationsCommandTests(TestCase):
    def setUp(self):
        clear_location_cache()
        self.business = Business.objects.create(name="Command Business")

    def run_command(self, *args, **kwargs):
        output = StringIO()
        call_command("normalize_flightlog_locations", *args, stdout=output, **kwargs)
        return output.getvalue()

    def test_no_geocode_backfills_address_and_is_idempotent(self):
        flight = FlightLog.objects.create(business=self.business, flight_date=date(2026, 1, 1), takeoff_address="123 Main St, St Louis, MO 63101, USA")
        first_output = self.run_command("--no-geocode", business=str(self.business.pk))
        second_output = self.run_command("--no-geocode", business=str(self.business.pk))
        flight.refresh_from_db()
        self.assertEqual((flight.takeoff_city, flight.takeoff_state), ("St Louis", "MO"))
        self.assertIn("updated=1", first_output)
        self.assertIn("processed=0", second_output)

    def test_default_does_not_overwrite_and_force_refreshes(self):
        flight = FlightLog.objects.create(business=self.business, flight_date=date(2026, 1, 1), takeoff_address="123 Main St, Las Vegas, NV 89101", takeoff_city="Existing", takeoff_state="NV", takeoff_country="USA", takeoff_postal_code="89101")
        self.run_command("--no-geocode", business=str(self.business.pk))
        flight.refresh_from_db()
        self.assertEqual(flight.takeoff_city, "Existing")
        self.run_command("--no-geocode", "--force", business=str(self.business.pk))
        flight.refresh_from_db()
        self.assertEqual(flight.takeoff_city, "Las Vegas")

    @mock.patch("flightlogs.services.locations.reverse_geocode_coordinates")
    def test_reverse_geocode_fallback_is_mocked(self, reverse_mock):
        reverse_mock.return_value = LocationComponents("Fishers", "IN", "USA", "46037")
        flight = FlightLog.objects.create(business=self.business, flight_date=date(2026, 1, 1), takeoff_latlong="39.9, -85.9")
        output = self.run_command(business=str(self.business.pk), limit=1)
        flight.refresh_from_db()
        self.assertEqual(flight.takeoff_city, "Fishers")
        self.assertIn("reverse_geocoded=1", output)


def parser_payload(**overrides):
    payload = {
        "success": True,
        "parser_version": "0.5.7",
        "log_version": 14,
        "encrypted": True,
        "aircraft_model": "Mavic 3 Pro",
        "aircraft_model_code": 84,
        "aircraft_name": "Survey Aircraft",
        "aircraft_serial": "COMPONENT-AIRCRAFT-SERIAL",
        "aircraft_serial_header": "HEADER-AIRCRAFT",
        "battery_serial": "COMPONENT-BATTERY-SERIAL",
        "battery_serial_header": "HEADER-BATTERY",
        "start_time": "2025-07-18T23:34:20.011+00:00",
        "duration_seconds": 1809.8,
        "airborne_duration_seconds": 1750.5,
        "takeoff_latitude": 47.32105249,
        "takeoff_longitude": -122.14325966,
        "maximum_altitude_relative_m": 119.7,
        "maximum_distance_from_home_m": 875.2,
        "total_distance_m": 2696.763,
        "maximum_satellites": 32,
        "minimum_satellites_airborne": 18,
        "minimum_airborne_satellites": 18,
        "minimum_gps_signal_level_airborne": 4,
        "maximum_gps_signal_level": 5,
        "takeoff_battery_percent": 96,
        "landing_battery_percent": 24,
        "takeoff_battery_voltage_v": 17.42,
        "landing_battery_voltage_v": 14.81,
        "takeoff_battery_capacity_mah": 4830,
        "landing_battery_capacity_mah": 1195,
        "maximum_battery_temperature_c": 51.5,
        "minimum_cell_voltage_v": 3.61,
        "maximum_cell_voltage_v": 4.36,
        "battery_cycle_count": 14,
        "battery_life_value": 98,
        "battery_life_raw": 98,
        "maximum_horizontal_speed_m_s": 19.25,
        "maximum_vertical_speed_m_s": 6.0,
        "maximum_vertical_speed_mps": 6.0,
        "signal_loss_events_over_one_second": 2,
        "photo_count": 37,
        "flight_modes": ["GPSAtti", "GPSSport"],
        "rc_serial": "RC-COMPONENT-SERIAL",
        "camera_serial": "CAMERA-COMPONENT-SERIAL",
        "warnings": [],
        "serious_warnings": [],
        "tips": [],
        "messages": [],
    }
    payload.update(overrides)
    return payload


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class FlightLogDJIUploadTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_directory = tempfile.TemporaryDirectory()
        cls.settings_override = override_settings(MEDIA_ROOT=cls.media_directory.name)
        cls.settings_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls.settings_override.disable()
        cls.media_directory.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.location_patcher = mock.patch(
            "flightlogs.services.dji.importer.enrich_flightlog_location",
        )
        self.location_mock = self.location_patcher.start()
        self.addCleanup(self.location_patcher.stop)
        self.weather_patcher = mock.patch(
            "flightlogs.services.dji.importer.enrich_flightlog_weather",
            return_value=False,
        )
        self.weather_mock = self.weather_patcher.start()
        self.addCleanup(self.weather_patcher.stop)
        self.business = Business.objects.create(name="Alpha Drone")
        self.user = get_user_model().objects.create_user("alpha", password="test-password")
        BusinessMembership.objects.create(
            business=self.business,
            user=self.user,
            role=BusinessMembership.Role.OWNER,
        )
        CompanyProfile.objects.create(
            business=self.business,
            created_by=self.user,
            company_name="Alpha Drone",
        )
        self.client.force_login(self.user)

    def upload(self, content=b"dji-flight-record", name="DJIFlightRecord.txt"):
        return self.client.post(
            reverse("flightlogs:flightlog_dji_upload"),
            {"dji_file": SimpleUploadedFile(name, content, content_type="text/plain")},
        )

    def upload_many(self, files):
        return self.client.post(
            reverse("flightlogs:flightlog_dji_upload"),
            {"dji_file": files},
        )

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_authenticated_upload_creates_and_links_flightlog(self, parse_mock):
        parse_mock.return_value = parser_payload()
        response = self.upload()
        self.assertEqual(response.status_code, 302)
        source = FlightLogSource.objects.get()
        log = FlightLog.objects.get()
        self.assertEqual(
            response.url,
            reverse("flightlogs:flightlog_detail", args=[log.pk]),
        )
        self.assertEqual(source.business, self.business)
        self.assertEqual(source.flight_log, log)
        self.assertEqual(source.status, FlightLogSource.Status.COMPLETE)
        self.assertEqual(source.sha256, "99c3d15f9ef32b9ff16488d70635fd89b163dd352d391db47aea5c07f539e12a")
        self.assertNotIn("DJIFlightRecord.txt", source.file.name)
        self.assertTrue(source.file.storage.exists(source.file.name))
        self.assertEqual(log.drone_serial, "COMPONENT-AIRCRAFT-SERIAL")
        self.assertEqual(log.drone_name, "Survey Aircraft")
        self.assertEqual(log.battery_serial_internal, "COMPONENT-BATTERY-SERIAL")
        self.assertEqual(log.battery_name, "")
        self.assertEqual(source.battery_serial, "COMPONENT-BATTERY-SERIAL")
        self.assertEqual(source.battery_serial_header, "HEADER-BATTERY")
        self.assertEqual(log.takeoff_battery_pct, 96)
        self.assertEqual(log.landing_battery_pct, 24)
        self.assertEqual(log.takeoff_mah, 4830)
        self.assertEqual(log.landing_mah, 1195)
        self.assertEqual(log.takeoff_volts, 17.42)
        self.assertEqual(log.landing_volts, 14.81)
        self.assertEqual(log.air_time.total_seconds(), 1750.5)
        self.assertAlmostEqual(log.max_battery_temp_f, 124.7)
        self.assertAlmostEqual(log.max_speed_mph, 43.061023622, places=6)
        self.assertEqual(log.signal_losses, 2)
        self.assertEqual(log.photos, 37)
        self.assertEqual(log.maximum_satellites, 32)
        self.assertEqual(log.minimum_airborne_satellites, 18)
        self.assertEqual(log.minimum_airborne_gps_level, 4)
        self.assertEqual(log.battery_cycle_count, 14)
        self.assertEqual(log.minimum_cell_voltage_v, 3.61)
        self.assertEqual(log.maximum_cell_voltage_v, 4.36)
        self.assertEqual(log.battery_life_raw, 98)
        self.assertEqual(log.maximum_vertical_speed_mps, 6.0)
        self.assertEqual(log.flight_modes, "GPSAtti, GPSSport")
        self.assertEqual(log.rc_serial, "RC-COMPONENT-SERIAL")
        self.assertEqual(log.camera_serial, "CAMERA-COMPONENT-SERIAL")
        self.assertIsNone(log.avg_wind)
        self.assertIsNone(log.max_gust)
        self.assertAlmostEqual(log.max_altitude_ft, 392.716535433, places=6)
        self.assertAlmostEqual(log.total_mileage_ft, 8847.6476378, places=5)
        self.weather_mock.assert_called_once_with(log)
        self.location_mock.assert_called_once_with(log)

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_dji_model_code_resolves_canonical_aircraft_model(self, parse_mock):
        model = AircraftModel.objects.create(
            business=self.business, manufacturer="DJI", name="Mavic 3 Pro",
            aliases=["DJI Mavic 3 Pro"], dji_model_code=84,
        )
        parse_mock.return_value = parser_payload()
        self.upload(content=b"canonical-model")
        self.assertEqual(FlightLog.objects.get().aircraft_model, model)

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_location_enrichment_failure_does_not_fail_dji_import(self, parse_mock):
        parse_mock.return_value = parser_payload()
        self.location_mock.side_effect = TimeoutError("geocoder unavailable")

        response = self.upload(content=b"location-timeout")

        self.assertEqual(response.status_code, 302)
        source = FlightLogSource.objects.get()
        self.assertEqual(source.status, FlightLogSource.Status.COMPLETE)
        self.assertIsNotNone(source.flight_log_id)

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_battery_header_serial_is_fallback_when_component_serial_absent(self, parse_mock):
        parse_mock.return_value = parser_payload(
            battery_serial=None,
            battery_serial_header="HEADER-ONLY-BATTERY",
        )
        self.upload(content=b"header-only-battery")

        source = FlightLogSource.objects.get()
        self.assertEqual(source.battery_serial, "")
        self.assertEqual(source.battery_serial_header, "HEADER-ONLY-BATTERY")
        self.assertEqual(source.flight_log.battery_serial_internal, "HEADER-ONLY-BATTERY")
        self.assertEqual(source.flight_log.battery_name, "")

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_battery_serial_remains_blank_when_both_sources_absent(self, parse_mock):
        parse_mock.return_value = parser_payload(
            battery_serial=None,
            battery_serial_header=None,
        )
        self.upload(content=b"no-battery-serial")

        source = FlightLogSource.objects.get()
        self.assertEqual(source.battery_serial, "")
        self.assertEqual(source.battery_serial_header, "")
        self.assertEqual(source.flight_log.battery_serial_internal, "")
        self.assertEqual(source.flight_log.battery_name, "")

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_dji_messages_are_deduplicated_ordered_and_bounded(self, parse_mock):
        parse_mock.return_value = parser_payload(
            warnings=["First warning", " First   warning ", "Second warning"],
            serious_warnings=["Serious warning", "Serious warning"],
            tips=["Useful tip", "x" * 500],
            messages=["compatibility value should not win"],
        )

        self.upload(content=b"bounded-operational-messages")

        log = FlightLog.objects.get()
        self.assertEqual(log.dji_warnings, "First warning\nSecond warning")
        self.assertEqual(log.dji_serious_warnings, "Serious warning")
        tip_lines = log.dji_tips.splitlines()
        self.assertEqual(tip_lines[0], "Useful tip")
        self.assertEqual(len(tip_lines[1]), 300)
        self.assertNotIn("compatibility value", log.dji_tips)
        self.assertEqual(log.notes, "")

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_unavailable_component_identities_remain_blank(self, parse_mock):
        # Rust reports null when multiple distinct type-1 or type-3 component
        # serials make a single identity ambiguous.
        parse_mock.return_value = parser_payload(rc_serial=None, camera_serial=None)

        self.upload(content=b"ambiguous-components")

        log = FlightLog.objects.get()
        self.assertEqual(log.rc_serial, "")
        self.assertEqual(log.camera_serial, "")

    def test_active_business_is_required(self):
        user = get_user_model().objects.create_user("no-business", password="test-password")
        self.client.force_login(user)
        response = self.upload(content=b"different")
        self.assertRedirects(response, reverse("accounts:onboarding"))
        self.assertFalse(FlightLogSource.objects.filter(created_by=user).exists())

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_exact_hash_duplicate_in_same_business_returns_existing(self, parse_mock):
        parse_mock.return_value = parser_payload()
        with self.captureOnCommitCallbacks(execute=True):
            self.upload()
        response = self.upload()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(FlightLogSource.objects.count(), 1)
        self.assertEqual(FlightLog.objects.count(), 1)
        self.assertEqual(parse_mock.call_count, 1)
        self.assertTrue(FlightLogSource.objects.get().file.name)

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_same_hash_is_independent_across_businesses(self, parse_mock):
        parse_mock.return_value = parser_payload()
        self.upload()

        second_business = Business.objects.create(name="Bravo Drone")
        second_user = get_user_model().objects.create_user("bravo", password="test-password")
        BusinessMembership.objects.create(business=second_business, user=second_user)
        CompanyProfile.objects.create(business=second_business, company_name="Bravo Drone")
        self.client.force_login(second_user)
        response = self.upload()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(FlightLogSource.objects.count(), 2)
        self.assertEqual(FlightLog.objects.count(), 2)
        self.assertEqual(parse_mock.call_count, 2)
        self.assertEqual(
            FlightLogSource.objects.values("sha256").distinct().count(),
            1,
        )

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_parser_panic_keeps_failed_source_without_flightlog(self, parse_mock):
        parse_mock.side_effect = import_error("DJI_PARSER_PANIC")
        response = self.upload()
        self.assertRedirects(response, reverse("flightlogs:flightlog_dji_upload"))
        source = FlightLogSource.objects.get()
        self.assertEqual(source.status, FlightLogSource.Status.FAILED)
        self.assertEqual(source.safe_error_code, "DJI_PARSER_PANIC")
        self.assertIn("parser update", source.safe_error_detail)
        self.assertIsNone(source.flight_log)
        self.assertFalse(FlightLog.objects.exists())

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_unknown_model_code_does_not_invent_model_and_component_serial_wins(self, parse_mock):
        parse_mock.return_value = parser_payload(
            aircraft_model=None,
            aircraft_model_code=137,
            aircraft_serial="FULL-COMPONENT-SERIAL",
            aircraft_serial_header="TRUNCATED-HEADER",
        )
        self.upload(content=b"unknown-model")
        source = FlightLogSource.objects.get()
        self.assertEqual(source.aircraft_model_code, 137)
        self.assertEqual(source.aircraft_serial_header, "TRUNCATED-HEADER")
        self.assertEqual(source.flight_log.drone_type, "")
        self.assertEqual(source.flight_log.drone_serial, "FULL-COMPONENT-SERIAL")

    def test_csv_import_regression(self):
        csv_data = b"flight_date,flight_title,drone_serial\n2025-01-02,CSV Flight,CSV-SERIAL\n"
        response = self.client.post(
            reverse("flightlogs:flightlog_upload"),
            {"csv_file": SimpleUploadedFile("flights.csv", csv_data, content_type="text/csv")},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("flightlogs:flightlog_list"))
        log = FlightLog.objects.get()
        self.assertEqual(log.flight_title, "CSV Flight")
        self.assertEqual(log.drone_serial, "CSV-SERIAL")
        self.assertFalse(FlightLogSource.objects.exists())
        self.weather_mock.assert_not_called()

    def test_airdata_takeoff_address_maps_and_normalizes(self):
        row = _normalised_row(
            {
                "flight_date": "2025-01-02",
                "Takeoff Address": "100 Albemarle House Dr, Charlottesville, VA 22902, USA",
                "Takeoff Lat/Long": "38.123, -77.456",
            }
        )

        payload = _flightlog_payload_from_csv_row(row)

        self.assertEqual(payload["takeoff_address"], "100 Albemarle House Dr, Charlottesville, VA 22902, USA")
        self.assertEqual(payload["takeoff_latlong"], "38.123, -77.456")
        self.assertEqual(payload["takeoff_city"], "Charlottesville")
        self.assertEqual(payload["takeoff_state"], "VA")
        self.assertEqual(payload["takeoff_postal_code"], "22902")
        self.assertEqual(payload["takeoff_country"], "USA")

    def test_native_csv_location_fields_override_address_parser(self):
        row = _normalised_row({
            "flight_date": "2025-01-02",
            "Takeoff Address": "100 Main St, Parsed City, VA 22902, USA",
            "Takeoff City": "Explicit City",
            "Takeoff State": "EX",
            "Takeoff Postal Code": "00000",
            "Takeoff Country": "Explicit Country",
        })
        payload = _flightlog_payload_from_csv_row(row)
        self.assertEqual((payload["takeoff_city"], payload["takeoff_state"], payload["takeoff_postal_code"], payload["takeoff_country"]), ("Explicit City", "EX", "00000", "Explicit Country"))

    def test_native_csv_restores_newer_battery_fields(self):
        payload = _flightlog_payload_from_csv_row(_normalised_row({
            "flight_date": "2025-01-02",
            "battery_cycle_count": "42",
            "battery_life_raw": "97",
            "minimum_cell_voltage_v": "3.55",
            "maximum_cell_voltage_v": "4.31",
        }))
        self.assertEqual(payload["battery_cycle_count"], 42)
        self.assertEqual(payload["battery_life_raw"], 97)
        self.assertEqual(payload["minimum_cell_voltage_v"], 3.55)
        self.assertEqual(payload["maximum_cell_voltage_v"], 4.31)

    def test_csv_import_resolves_model_and_unknown_remains_null(self):
        model = AircraftModel.objects.create(
            business=self.business, manufacturer="DJI", name="Mavic 4 Pro",
            aliases=["DJI Mavic 4 Pro"],
        )
        csv_data = b"flight_date,Drone Type,Drone Name\n2025-01-02,Mavic 4 Pro,Primary\n2025-01-03,Unknown Model,Unknown\n"
        self.client.post(
            reverse("flightlogs:flightlog_upload"),
            {"csv_file": SimpleUploadedFile("models.csv", csv_data, content_type="text/csv")},
        )
        self.assertEqual(FlightLog.objects.get(flight_date=date(2025, 1, 2)).aircraft_model, model)
        self.assertIsNone(FlightLog.objects.get(flight_date=date(2025, 1, 3)).aircraft_model)

    def test_export_and_reimport_preserve_new_battery_fields(self):
        FlightLog.objects.create(
            business=self.business, flight_date=date(2025, 1, 2),
            battery_serial_internal="BAT-1", battery_cycle_count=42,
            battery_life_raw=97, minimum_cell_voltage_v=3.55,
            maximum_cell_voltage_v=4.31,
        )
        response = self.client.get(reverse("flightlogs:export_flightlogs_csv"))
        text = response.content.decode()
        for header in ("battery_cycle_count", "battery_life_raw", "minimum_cell_voltage_v", "maximum_cell_voltage_v"):
            self.assertIn(header, text.splitlines()[0])

        FlightLog.objects.all().delete()
        self.client.post(
            reverse("flightlogs:flightlog_upload"),
            {"csv_file": SimpleUploadedFile("suite-export.csv", response.content, content_type="text/csv")},
        )
        restored = FlightLog.objects.get()
        self.assertEqual((restored.battery_cycle_count, restored.battery_life_raw), (42, 97))
        self.assertEqual((restored.minimum_cell_voltage_v, restored.maximum_cell_voltage_v), (3.55, 4.31))

    def test_csv_reimport_remains_idempotent_with_derived_location(self):
        csv_data = (
            b"flight_date,Takeoff Address,flight_title\n"
            b'2025-01-02,"100 Main St, St Louis, MO 63101, USA",Same Flight\n'
        )
        for _ in range(2):
            self.client.post(
                reverse("flightlogs:flightlog_upload"),
                {"csv_file": SimpleUploadedFile("flights.csv", csv_data, content_type="text/csv")},
            )
        self.assertEqual(FlightLog.objects.count(), 1)
        self.assertEqual(FlightLog.objects.get().takeoff_city, "St Louis")

    def test_csv_timezone_resolution_failure_does_not_store_row(self):
        csv_data = (
            b"Flight Date/Time,Drone Serial Number\n"
            b'"Jul 1st, 2026 02:30PM",CSV-SERIAL\n'
        )
        response = self.client.post(
            reverse("flightlogs:flightlog_upload"),
            {"csv_file": SimpleUploadedFile("flights.csv", csv_data, content_type="text/csv")},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(FlightLog.objects.exists())

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_weather_failure_does_not_fail_dji_import(self, parse_mock):
        parse_mock.return_value = parser_payload()
        self.weather_mock.side_effect = RuntimeError("provider unavailable")

        response = self.upload(content=b"weather-failure")

        self.assertEqual(response.status_code, 302)
        source = FlightLogSource.objects.get()
        self.assertEqual(
            response.url,
            reverse("flightlogs:flightlog_detail", args=[source.flight_log_id]),
        )
        self.assertEqual(source.status, FlightLogSource.Status.COMPLETE)
        self.assertIsNotNone(source.flight_log)

    def test_list_and_detail_business_isolation_remain_enforced(self):
        own = FlightLog.objects.create(
            business=self.business,
            flight_date=date(2025, 1, 1),
            flight_title="Own Flight",
        )
        other_business = Business.objects.create(name="Other")
        other = FlightLog.objects.create(
            business=other_business,
            flight_date=date(2025, 1, 2),
            flight_title="Other Flight",
        )
        list_response = self.client.get(reverse("flightlogs:flightlog_list"))
        listed_ids = {log.pk for log in list_response.context["logs"]}
        self.assertIn(own.pk, listed_ids)
        self.assertNotIn(other.pk, listed_ids)
        detail_response = self.client.get(reverse("flightlogs:flightlog_detail", args=[other.pk]))
        self.assertEqual(detail_response.status_code, 404)

    def _airdata_flight(self, **overrides):
        values = {
            "business": self.business,
            "flight_date": date(2025, 7, 18),
            "takeoff_datetime": datetime(2025, 7, 18, 23, 34, tzinfo=timezone.utc),
            "drone_serial": "COMPONENT-AIRCRAFT-SERIAL",
            "battery_serial_internal": "AIRDATA-BATTERY",
            "takeoff_latlong": "47.32105, -122.14326",
            "air_time": timedelta(seconds=1740),
            "total_mileage_ft": 8800,
            "max_distance_ft": 2870,
            "max_altitude_ft": 390,
            "flight_title": "AirData value",
        }
        values.update(overrides)
        return FlightLog.objects.create(**values)

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_high_confidence_match_links_without_creating_or_overwriting(self, parse_mock):
        existing = self._airdata_flight()
        parse_mock.return_value = parser_payload()

        response = self.upload(content=b"cross-source-high")

        source = FlightLogSource.objects.get()
        existing.refresh_from_db()
        self.assertRedirects(response, reverse("flightlogs:flightlog_detail", args=[existing.pk]))
        self.assertEqual(FlightLog.objects.count(), 1)
        self.assertEqual(source.flight_log, existing)
        self.assertEqual(source.status, FlightLogSource.Status.COMPLETE)
        self.assertEqual(existing.flight_title, "AirData value")
        self.assertEqual(existing.battery_serial_internal, "AIRDATA-BATTERY")
        self.assertTrue(source.file.name)
        self.assertTrue(source.file.storage.exists(source.file.name))
        self.assertTrue(source.sha256)

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_missing_full_serial_strong_evidence_requires_review(self, parse_mock):
        self._airdata_flight(drone_serial="")
        parse_mock.return_value = parser_payload(
            aircraft_serial=None,
            aircraft_serial_header="TRUNCATED-HEADER",
        )

        response = self.upload(content=b"cross-source-probable", name="probable.txt")

        source = FlightLogSource.objects.get()
        self.assertRedirects(
            response,
            f"{reverse('flightlogs:flightlog_dji_upload')}?review=1",
        )
        self.assertEqual(FlightLog.objects.count(), 1)
        self.assertIsNone(source.flight_log)
        self.assertEqual(source.status, FlightLogSource.Status.REVIEW)
        self.assertTrue(source.file.name)
        self.assertTrue(source.file.storage.exists(source.file.name))
        followup = self.client.get(response.url)
        self.assertContains(followup, "Possible existing flight found — review required.")

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_different_aircraft_at_same_time_is_not_matched(self, parse_mock):
        existing = self._airdata_flight(drone_serial="OTHER-AIRCRAFT")
        parse_mock.return_value = parser_payload()
        self.upload(content=b"different-aircraft")
        source = FlightLogSource.objects.get()
        self.assertEqual(FlightLog.objects.count(), 2)
        self.assertNotEqual(source.flight_log, existing)

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_same_aircraft_outside_time_tolerance_is_not_matched(self, parse_mock):
        existing = self._airdata_flight(
            takeoff_datetime=datetime(2025, 7, 18, 23, 32, 59, tzinfo=timezone.utc)
        )
        parse_mock.return_value = parser_payload()
        self.upload(content=b"outside-time-window")
        source = FlightLogSource.objects.get()
        self.assertEqual(FlightLog.objects.count(), 2)
        self.assertNotEqual(source.flight_log, existing)

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_failed_source_file_is_retained(self, parse_mock):
        parse_mock.side_effect = import_error("DJI_PARSE_ERROR")
        self.upload(content=b"retained-failure")
        source = FlightLogSource.objects.get()
        self.assertEqual(source.status, FlightLogSource.Status.FAILED)
        self.assertTrue(source.file.name)
        self.assertTrue(source.file.storage.exists(source.file.name))

    def test_matching_time_boundary_and_business_isolation(self):
        existing = self._airdata_flight(
            takeoff_datetime=datetime(2025, 7, 18, 23, 33, 20, 11000, tzinfo=timezone.utc)
        )
        payload = {
            "takeoff_datetime": datetime(2025, 7, 18, 23, 34, 20, 11000, tzinfo=timezone.utc),
            "takeoff_latlong": "47.32105249, -122.14325966",
            "air_time": timedelta(seconds=1750.5),
            "total_mileage_ft": 8847.6,
            "max_distance_ft": 2871.4,
            "max_altitude_ft": 392.7,
        }
        result = match_existing_flight(
            business=self.business,
            payload=payload,
            authoritative_aircraft_serial="COMPONENT-AIRCRAFT-SERIAL",
        )
        self.assertEqual(result.match_type, MatchType.HIGH_CONFIDENCE)
        self.assertEqual(result.matched_flight, existing)
        self.assertIn("takeoff_location_meters", result.field_differences)
        self.assertIn("air_time_seconds", result.field_differences)

        other_business = Business.objects.create(name="Isolated")
        isolated = match_existing_flight(
            business=other_business,
            payload=payload,
            authoritative_aircraft_serial="COMPONENT-AIRCRAFT-SERIAL",
        )
        self.assertEqual(isolated.match_type, MatchType.NO_MATCH)

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_partial_airdata_match_is_retained_for_review_without_linking(self, parse_mock):
        self._airdata_flight(
            air_time=timedelta(seconds=700),
            total_mileage_ft=3000,
        )
        parse_mock.return_value = parser_payload()

        response = self.upload(content=b"partial-airdata-review")

        source = FlightLogSource.objects.get()
        self.assertRedirects(
            response,
            f"{reverse('flightlogs:flightlog_dji_upload')}?review=1",
        )
        self.assertEqual(FlightLog.objects.count(), 1)
        self.assertIsNone(source.flight_log)
        self.assertEqual(source.status, FlightLogSource.Status.REVIEW)
        self.assertTrue(source.file.name)
        self.assertTrue(source.file.storage.exists(source.file.name))

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_location_variance_high_confidence_match_links_existing_flight(self, parse_mock):
        existing = self._airdata_flight(takeoff_latlong="47.31855, -122.14326")
        parse_mock.return_value = parser_payload()

        response = self.upload(content=b"location-variance-high")

        source = FlightLogSource.objects.get()
        self.assertRedirects(response, reverse("flightlogs:flightlog_detail", args=[existing.pk]))
        self.assertEqual(FlightLog.objects.count(), 1)
        self.assertEqual(source.flight_log, existing)
        self.assertEqual(source.status, FlightLogSource.Status.COMPLETE)

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_multiple_file_successful_upload(self, parse_mock):
        parse_mock.side_effect = [
            parser_payload(start_time="2026-08-01T12:00:00+00:00"),
            parser_payload(start_time="2026-08-02T12:00:00+00:00"),
        ]

        response = self.upload_many(
            [
                SimpleUploadedFile("first.txt", b"first", content_type="text/plain"),
                SimpleUploadedFile("second.txt", b"second", content_type="text/plain"),
            ]
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2 files processed sequentially")
        self.assertContains(response, "Imported New Flight", count=4)
        self.assertEqual(FlightLog.objects.count(), 2)
        self.assertEqual(FlightLogSource.objects.count(), 2)

    def test_default_bulk_file_limit_is_ten(self):
        self.assertEqual(settings.DJI_BULK_MAX_FILES, 10)
        response = self.client.get(reverse("flightlogs:flightlog_dji_upload"))
        self.assertContains(response, "You can upload up to 10 DJI flight logs at a time.")
        self.assertContains(response, "Choose up to 10 files.")

    def test_dji_upload_has_no_custom_processing_ui(self):
        response = self.client.get(reverse("flightlogs:flightlog_dji_upload"))
        self.assertNotContains(response, 'id="dji-processing-state"')
        self.assertNotContains(response, "flightplan-processing-overlay")
        self.assertNotContains(response, "spinner-border")
        self.assertNotContains(response, 'aria-busy="true"')
        self.assertNotContains(response, "dji-propeller.png")
        self.assertNotContains(response, "Importing DJI Flight Log")
        self.assertNotContains(response, "if (submitting)")
        self.assertContains(response, 'id="dji-selected-count"')
        self.assertContains(response, "1 file selected.")

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_ten_file_submission_is_accepted(self, parse_mock):
        parse_mock.side_effect = [
            parser_payload(start_time=f"2026-08-{index + 1:02d}T12:00:00+00:00")
            for index in range(10)
        ]
        response = self.upload_many(
            [
                SimpleUploadedFile(f"flight-{index}.txt", f"source-{index}".encode())
                for index in range(10)
            ]
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "10 files processed sequentially")
        self.assertEqual(parse_mock.call_count, 10)
        self.assertEqual(FlightLog.objects.count(), 10)
        self.assertEqual(FlightLogSource.objects.count(), 10)

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_eleven_file_submission_is_rejected(self, parse_mock):
        response = self.upload_many(
            [
                SimpleUploadedFile(f"flight-{index}.txt", f"source-{index}".encode())
                for index in range(11)
            ]
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select no more than 10 DJI flight records")
        parse_mock.assert_not_called()

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_bulk_results_include_desktop_and_mobile_layouts(self, parse_mock):
        existing = self._airdata_flight()
        parse_mock.side_effect = [
            parser_payload(),
            import_error("DJI_PARSE_ERROR"),
        ]
        response = self.upload_many(
            [
                SimpleUploadedFile("FlightRecord_linked.txt", b"linked"),
                SimpleUploadedFile("FlightRecord_failed.txt", b"failed"),
            ]
        )
        detail_url = reverse("flightlogs:flightlog_detail", args=[existing.pk])
        self.assertContains(response, 'data-testid="dji-results-desktop"')
        self.assertContains(response, 'data-testid="dji-results-mobile"')
        self.assertContains(response, detail_url, count=2)
        self.assertContains(response, "FlightLog %s" % existing.pk, count=2)
        self.assertContains(response, "Linked Existing Flight", count=2)
        self.assertContains(response, "Failed", count=3)

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_mixed_linked_new_review_failure_batch(self, parse_mock):
        linked = self._airdata_flight()
        self._airdata_flight(
            takeoff_datetime=datetime(2025, 7, 19, 23, 34, tzinfo=timezone.utc),
            flight_date=date(2025, 7, 19),
            air_time=timedelta(seconds=500),
            total_mileage_ft=2000,
        )
        review_payload = parser_payload(
            start_time="2025-07-19T23:34:20.011+00:00",
        )
        parse_mock.side_effect = [
            parser_payload(),
            parser_payload(start_time="2026-08-02T12:00:00+00:00"),
            review_payload,
            import_error("DJI_PARSE_ERROR"),
        ]

        response = self.upload_many(
            [
                SimpleUploadedFile("linked.txt", b"linked", content_type="text/plain"),
                SimpleUploadedFile("new.txt", b"new", content_type="text/plain"),
                SimpleUploadedFile("review.txt", b"review", content_type="text/plain"),
                SimpleUploadedFile("failed.txt", b"failed", content_type="text/plain"),
            ]
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Linked Existing Flight")
        self.assertContains(response, "Imported New Flight")
        self.assertContains(response, "Review Required — Partial Existing Record")
        self.assertContains(response, "Failed")
        sources = {source.original_filename: source for source in FlightLogSource.objects.all()}
        self.assertEqual(sources["linked.txt"].flight_log, linked)
        self.assertEqual(sources["review.txt"].status, FlightLogSource.Status.REVIEW)
        self.assertIsNone(sources["review.txt"].flight_log)
        self.assertEqual(sources["failed.txt"].status, FlightLogSource.Status.FAILED)
        self.assertEqual(FlightLog.objects.count(), 3)

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_exact_duplicate_within_batch(self, parse_mock):
        parse_mock.return_value = parser_payload(start_time="2026-08-01T12:00:00+00:00")
        response = self.upload_many(
            [
                SimpleUploadedFile("first.txt", b"same-source", content_type="text/plain"),
                SimpleUploadedFile("renamed.txt", b"same-source", content_type="text/plain"),
            ]
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Duplicate Source")
        self.assertEqual(parse_mock.call_count, 1)
        self.assertEqual(FlightLog.objects.count(), 1)
        self.assertEqual(FlightLogSource.objects.count(), 1)

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_duplicate_against_previous_upload_in_batch(self, parse_mock):
        parse_mock.side_effect = [
            parser_payload(start_time="2026-08-01T12:00:00+00:00"),
            parser_payload(start_time="2026-08-02T12:00:00+00:00"),
        ]
        self.upload(content=b"previous", name="previous.txt")
        response = self.upload_many(
            [
                SimpleUploadedFile("duplicate.txt", b"previous", content_type="text/plain"),
                SimpleUploadedFile("new.txt", b"new-source", content_type="text/plain"),
            ]
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Duplicate Source")
        self.assertContains(response, "Imported New Flight")
        self.assertEqual(parse_mock.call_count, 2)
        self.assertEqual(FlightLog.objects.count(), 2)
        self.assertEqual(FlightLogSource.objects.count(), 2)

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_parser_failure_does_not_stop_later_file(self, parse_mock):
        parse_mock.side_effect = [
            import_error("DJI_PARSE_ERROR"),
            parser_payload(start_time="2026-08-02T12:00:00+00:00"),
        ]
        response = self.upload_many(
            [
                SimpleUploadedFile("failed.txt", b"failed", content_type="text/plain"),
                SimpleUploadedFile("later.txt", b"later", content_type="text/plain"),
            ]
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Failed")
        self.assertContains(response, "Imported New Flight")
        self.assertEqual(FlightLog.objects.count(), 1)
        self.assertEqual(FlightLogSource.objects.count(), 2)

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_review_does_not_stop_later_file(self, parse_mock):
        self._airdata_flight(air_time=timedelta(seconds=500), total_mileage_ft=2000)
        parse_mock.side_effect = [
            parser_payload(),
            parser_payload(start_time="2026-08-02T12:00:00+00:00"),
        ]
        response = self.upload_many(
            [
                SimpleUploadedFile("review.txt", b"review", content_type="text/plain"),
                SimpleUploadedFile("later.txt", b"later", content_type="text/plain"),
            ]
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Review Required — Partial Existing Record")
        self.assertContains(response, "Imported New Flight")
        self.assertEqual(FlightLog.objects.count(), 2)

    @override_settings(DJI_BULK_MAX_FILES=2)
    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_bulk_file_count_limit(self, parse_mock):
        response = self.upload_many(
            [
                SimpleUploadedFile(f"{index}.txt", b"x", content_type="text/plain")
                for index in range(3)
            ]
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select no more than 2 DJI flight records")
        parse_mock.assert_not_called()

    @override_settings(DJI_UPLOAD_MAX_BYTES=10, DJI_BULK_MAX_TOTAL_BYTES=5)
    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_bulk_total_size_limit(self, parse_mock):
        response = self.upload_many(
            [
                SimpleUploadedFile("first.txt", b"123", content_type="text/plain"),
                SimpleUploadedFile("second.txt", b"456", content_type="text/plain"),
            ]
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "must total 0 MB or less")
        parse_mock.assert_not_called()

    @override_settings(DJI_UPLOAD_MAX_BYTES=3, DJI_BULK_MAX_TOTAL_BYTES=10)
    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_bulk_per_file_size_limit(self, parse_mock):
        response = self.upload_many(
            [SimpleUploadedFile("large.txt", b"1234", content_type="text/plain")]
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "must be 0 MB or smaller")
        parse_mock.assert_not_called()

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_bulk_rejects_non_txt_and_empty_files(self, parse_mock):
        non_txt = self.upload_many(
            [SimpleUploadedFile("flight.pdf", b"content", content_type="application/pdf")]
        )
        self.assertContains(non_txt, "must be a .txt file")
        empty = self.upload_many(
            [SimpleUploadedFile("empty.txt", b"", content_type="text/plain")]
        )
        self.assertContains(empty, "is empty")
        parse_mock.assert_not_called()

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_bulk_duplicate_is_business_isolated(self, parse_mock):
        parse_mock.return_value = parser_payload(start_time="2026-08-01T12:00:00+00:00")
        self.upload(content=b"shared-source", name="first.txt")
        second_business = Business.objects.create(name="Bulk Isolated")
        second_user = get_user_model().objects.create_user("bulk-other", password="test-password")
        BusinessMembership.objects.create(business=second_business, user=second_user)
        CompanyProfile.objects.create(business=second_business, company_name="Bulk Isolated")
        self.client.force_login(second_user)
        response = self.upload_many(
            [
                SimpleUploadedFile("same.txt", b"shared-source", content_type="text/plain"),
                SimpleUploadedFile("other.txt", b"other-source", content_type="text/plain"),
            ]
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Duplicate Source")
        self.assertEqual(FlightLogSource.objects.filter(business=second_business).count(), 2)


class FlightMatchingRulesTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Matching Business")
        self.takeoff = datetime(2026, 3, 21, 15, 44, tzinfo=timezone.utc)

    def flight(self, **overrides):
        values = {
            "business": self.business,
            "flight_date": self.takeoff.date(),
            "takeoff_datetime": self.takeoff,
            "drone_serial": "FULL-AIRCRAFT-SERIAL",
            "takeoff_latlong": "40.000000, -86.000000",
            "air_time": timedelta(seconds=1000),
            "total_mileage_ft": 10000,
            "max_altitude_ft": 400,
        }
        values.update(overrides)
        return FlightLog.objects.create(**values)

    def payload(self, **overrides):
        values = {
            "takeoff_datetime": self.takeoff + timedelta(seconds=20),
            "takeoff_latlong": "40.000050, -86.000000",
            "air_time": timedelta(seconds=1005),
            "total_mileage_ft": 10050,
            "max_distance_ft": 2500,
            "max_altitude_ft": 402,
        }
        values.update(overrides)
        return values

    def match(self, payload=None, *, serial="FULL-AIRCRAFT-SERIAL", battery=""):
        return match_existing_flight(
            business=self.business,
            payload=payload or self.payload(),
            authoritative_aircraft_serial=serial,
            authoritative_battery_serial=battery,
        )

    def test_existing_primary_high_confidence_rule_is_unchanged(self):
        existing = self.flight()

        result = self.match()

        self.assertEqual(result.match_type, MatchType.HIGH_CONFIDENCE)
        self.assertEqual(result.matched_flight, existing)

    def test_location_variance_path_matches_tight_metrics_between_150_and_400_meters(self):
        existing = self.flight()
        payload = self.payload(
            takeoff_latlong="40.002500, -86.000000",
            air_time=timedelta(seconds=1009),
            total_mileage_ft=10090,
            max_altitude_ft=403.9,
        )

        result = self.match(payload)

        self.assertEqual(result.match_type, MatchType.HIGH_CONFIDENCE_LOCATION_VARIANCE)
        self.assertEqual(result.matched_flight, existing)
        self.assertGreater(result.field_differences["takeoff_location_meters"], 150)
        self.assertLess(result.field_differences["takeoff_location_meters"], 400)

    def test_location_variance_over_500_meters_is_not_high_confidence(self):
        self.flight()
        result = self.match(self.payload(takeoff_latlong="40.005000, -86.000000"))
        self.assertEqual(result.match_type, MatchType.NO_MATCH)

    def test_duration_conflict_prevents_location_variance_auto_link(self):
        self.flight()
        result = self.match(
            self.payload(
                takeoff_latlong="40.002500, -86.000000",
                air_time=timedelta(seconds=1020),
            )
        )
        self.assertEqual(result.match_type, MatchType.NO_MATCH)

    def test_distance_conflict_prevents_location_variance_auto_link(self):
        self.flight()
        result = self.match(
            self.payload(
                takeoff_latlong="40.002500, -86.000000",
                total_mileage_ft=10200,
            )
        )
        self.assertEqual(result.match_type, MatchType.NO_MATCH)

    def test_altitude_conflict_prevents_location_variance_auto_link(self):
        self.flight()
        result = self.match(
            self.payload(
                takeoff_latlong="40.002500, -86.000000",
                max_altitude_ft=410,
            )
        )
        self.assertEqual(result.match_type, MatchType.NO_MATCH)

    def test_conflicting_aircraft_serial_prevents_match(self):
        self.flight()
        self.assertEqual(self.match(serial="OTHER-SERIAL").match_type, MatchType.NO_MATCH)

    def test_multiple_location_variance_candidates_are_ambiguous(self):
        self.flight()
        self.flight(flight_title="Second candidate")
        result = self.match(self.payload(takeoff_latlong="40.002500, -86.000000"))
        self.assertEqual(result.match_type, MatchType.AMBIGUOUS)
        self.assertIsNone(result.matched_flight)

    def test_partial_airdata_pattern_requires_review_and_never_auto_links(self):
        existing = self.flight(
            air_time=timedelta(seconds=400),
            total_mileage_ft=3500,
        )

        result = self.match()

        self.assertEqual(result.match_type, MatchType.REVIEW_PARTIAL_AIRDATA)
        self.assertEqual(result.matched_flight, existing)
        self.assertEqual(result.confidence, "review")

    def test_adjacent_airdata_flights_are_not_summed(self):
        existing = self.flight(
            air_time=timedelta(seconds=400),
            total_mileage_ft=3500,
        )
        self.flight(
            takeoff_datetime=self.takeoff + timedelta(minutes=2),
            air_time=timedelta(seconds=600),
            total_mileage_ft=6500,
        )

        result = self.match()

        self.assertEqual(result.match_type, MatchType.REVIEW_PARTIAL_AIRDATA)
        self.assertEqual(result.matched_flight, existing)

    def test_battery_identity_does_not_affect_classification(self):
        self.flight(battery_serial_internal="MATCHING-BATTERY")
        matching = self.match(battery="MATCHING-BATTERY")
        conflicting = self.match(battery="OTHER-BATTERY")
        missing = self.match(battery="")
        self.assertEqual(
            {matching.match_type, conflicting.match_type, missing.match_type},
            {MatchType.HIGH_CONFIDENCE},
        )
        self.assertEqual(matching.reasons, conflicting.reasons)

    def test_location_variance_path_is_business_isolated(self):
        self.flight()
        other = Business.objects.create(name="Other Matching Business")
        result = match_existing_flight(
            business=other,
            payload=self.payload(takeoff_latlong="40.002500, -86.000000"),
            authoritative_aircraft_serial="FULL-AIRCRAFT-SERIAL",
        )
        self.assertEqual(result.match_type, MatchType.NO_MATCH)


@override_settings(DJI_PARSER_PATH="/test/suite-dji-parser")
class DJIParserAdapterTests(TestCase):
    def source(self):
        return SimpleUploadedFile("DJIFlightRecord.txt", b"record", content_type="text/plain")

    @staticmethod
    def completed_with(*, stdout=b"", stderr=b"", returncode=0):
        def run(args, **kwargs):
            kwargs["stdout"].write(stdout)
            kwargs["stderr"].write(stderr)
            return subprocess.CompletedProcess(args, returncode)

        return run

    @mock.patch("flightlogs.services.dji.subprocess_adapter.subprocess.run")
    def test_missing_api_key_is_sanitized(self, run_mock):
        run_mock.side_effect = self.completed_with(
            stderr=b"safe [diagnostic_code=DJI_API_KEY_MISSING]",
            returncode=1,
        )
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(Exception, "not configured") as caught:
                parse_dji_source(self.source())
        self.assertEqual(caught.exception.code, "DJI_API_KEY_MISSING")
        self.assertNotIn("DJI_API_KEY", run_mock.call_args.args[0])

    @mock.patch("flightlogs.services.dji.subprocess_adapter.subprocess.run")
    def test_malformed_parser_json(self, run_mock):
        run_mock.side_effect = self.completed_with(stdout=b"not-json")
        with self.assertRaises(Exception) as caught:
            parse_dji_source(self.source())
        self.assertEqual(caught.exception.code, "DJI_PARSER_OUTPUT_INVALID")

    @mock.patch("flightlogs.services.dji.subprocess_adapter.subprocess.run")
    def test_parser_timeout(self, run_mock):
        run_mock.side_effect = subprocess.TimeoutExpired(["parser"], 60)
        with self.assertRaises(Exception) as caught:
            parse_dji_source(self.source())
        self.assertEqual(caught.exception.code, "DJI_PARSER_TIMEOUT")

    @mock.patch("flightlogs.services.dji.subprocess_adapter.subprocess.run")
    def test_parser_executable_missing(self, run_mock):
        run_mock.side_effect = FileNotFoundError
        with self.assertRaises(Exception) as caught:
            parse_dji_source(self.source())
        self.assertEqual(caught.exception.code, "DJI_PARSER_MISSING")

    @mock.patch("flightlogs.services.dji.subprocess_adapter.subprocess.run")
    def test_valid_json_contract(self, run_mock):
        run_mock.side_effect = self.completed_with(stdout=json.dumps(parser_payload()).encode())
        result = parse_dji_source(self.source())
        self.assertEqual(result["log_version"], 14)
        command = run_mock.call_args.args[0]
        self.assertEqual(command[0], "/test/suite-dji-parser")
        self.assertEqual(len(command), 2)
        self.assertFalse(run_mock.call_args.kwargs["shell"])


class HistoricalWeatherServiceTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Weather Test")

    def flight_log(self, **overrides):
        values = {
            "business": self.business,
            "flight_date": date(2025, 7, 18),
            "takeoff_datetime": datetime(2025, 7, 18, 23, 34, tzinfo=timezone.utc),
            "takeoff_latlong": "47.32105249, -122.14325966",
        }
        values.update(overrides)
        return FlightLog.objects.create(**values)

    @staticmethod
    def response(**hourly_overrides):
        hourly = {
            "time": ["2025-07-18T23:00", "2025-07-19T00:00"],
            "temperature_2m": [10.0, 20.0],
            "relative_humidity_2m": [70, 80],
            "dew_point_2m": [5.0, 10.0],
            "precipitation": [0.0, 0.2],
            "rain": [0.0, 0.2],
            "weather_code": [1, 63],
            "pressure_msl": [1000.0, 1010.0],
            "cloud_cover": [25, 75],
            "wind_speed_10m": [16.09344, 32.18688],
            "wind_direction_10m": [270, 180],
            "wind_gusts_10m": [24.14016, 40.2336],
        }
        hourly.update(hourly_overrides)
        return {"hourly": hourly}

    @mock.patch("flightlogs.services.weather._fetch_json")
    def test_success_uses_nearest_hour_and_converts_units(self, fetch_mock):
        fetch_mock.return_value = self.response()
        flight_log = self.flight_log()

        self.assertTrue(enrich_flightlog_weather(flight_log))

        flight_log.refresh_from_db()
        # 23:34 UTC is closer to 00:00 than 23:00.
        self.assertEqual(flight_log.ground_temp_f, 68.0)
        self.assertEqual(flight_log.dew_point_f, 50.0)
        self.assertEqual(flight_log.humidity_pct, 80)
        self.assertAlmostEqual(flight_log.pressure_inhg, 29.825283, places=6)
        self.assertAlmostEqual(flight_log.wind_speed, 20.0, places=6)
        self.assertEqual(flight_log.wind_direction, "180")
        self.assertEqual(flight_log.cloud_cover, "75%")
        self.assertEqual(flight_log.ground_weather_summary, "Moderate rain")
        self.assertIsNone(flight_log.avg_wind)
        self.assertIsNone(flight_log.max_gust)
        self.assertIsNone(flight_log.visibility_miles)
        self.assertEqual(flight_log.rain_rate, "")

        called_url = fetch_mock.call_args.args[0]
        self.assertIn("archive-api.open-meteo.com/v1/archive", called_url)
        self.assertIn("timezone=UTC", called_url)
        self.assertEqual(fetch_mock.call_args.kwargs["timeout"], 5)

    @mock.patch("flightlogs.services.weather._fetch_json")
    def test_missing_individual_values_remain_blank(self, fetch_mock):
        fetch_mock.return_value = self.response(
            temperature_2m=[None, None],
            relative_humidity_2m=[None, None],
            wind_speed_10m=[None, None],
        )
        flight_log = self.flight_log()

        self.assertTrue(enrich_flightlog_weather(flight_log))

        flight_log.refresh_from_db()
        self.assertIsNone(flight_log.ground_temp_f)
        self.assertIsNone(flight_log.humidity_pct)
        self.assertIsNone(flight_log.wind_speed)
        self.assertEqual(flight_log.ground_weather_summary, "Moderate rain")

    @mock.patch("flightlogs.services.weather._fetch_json")
    def test_malformed_response_is_safe(self, fetch_mock):
        fetch_mock.return_value = {"hourly": "not-an-object"}
        flight_log = self.flight_log()

        self.assertFalse(enrich_flightlog_weather(flight_log))

        flight_log.refresh_from_db()
        self.assertIsNone(flight_log.ground_temp_f)

    @mock.patch("flightlogs.services.weather._fetch_json")
    def test_api_failure_is_safe(self, fetch_mock):
        fetch_mock.side_effect = TimeoutError
        flight_log = self.flight_log()

        self.assertFalse(enrich_flightlog_weather(flight_log))
        self.assertIsNone(FlightLog.objects.get(pk=flight_log.pk).ground_temp_f)

    @mock.patch("flightlogs.services.weather._fetch_json")
    def test_missing_coordinates_skips_api_call(self, fetch_mock):
        flight_log = self.flight_log(takeoff_latlong="")

        self.assertFalse(enrich_flightlog_weather(flight_log))
        fetch_mock.assert_not_called()

    @mock.patch("flightlogs.services.weather._fetch_json")
    def test_unknown_weather_code_does_not_invent_summary(self, fetch_mock):
        fetch_mock.return_value = self.response(weather_code=[123, 123])
        flight_log = self.flight_log()

        self.assertTrue(enrich_flightlog_weather(flight_log))

        flight_log.refresh_from_db()
        self.assertEqual(flight_log.ground_weather_summary, "")
