from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import date, datetime, timezone
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import CompanyProfile
from core.models import Business, BusinessMembership

from .models import FlightLog, FlightLogSource
from .services.dji.errors import import_error
from .services.dji.subprocess_adapter import parse_dji_source
from .services.weather import enrich_flightlog_weather


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

    @mock.patch("flightlogs.services.dji.importer.parse_dji_source")
    def test_authenticated_upload_creates_and_links_flightlog(self, parse_mock):
        parse_mock.return_value = parser_payload()
        response = self.upload()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("flightlogs:flightlog_detail", args=[1]))
        source = FlightLogSource.objects.get()
        log = FlightLog.objects.get()
        self.assertEqual(source.business, self.business)
        self.assertEqual(source.flight_log, log)
        self.assertEqual(source.status, FlightLogSource.Status.COMPLETE)
        self.assertEqual(source.sha256, "99c3d15f9ef32b9ff16488d70635fd89b163dd352d391db47aea5c07f539e12a")
        self.assertNotIn("DJIFlightRecord.txt", source.file.name)
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
        self.upload()
        response = self.upload()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(FlightLogSource.objects.count(), 1)
        self.assertEqual(FlightLog.objects.count(), 1)
        self.assertEqual(parse_mock.call_count, 1)

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
