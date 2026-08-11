from __future__ import annotations

import csv
import tempfile
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.test import TestCase
from timezonefinder import TimezoneFinder

from core.models import Business
from flightlogs.models import FlightLog, FlightLogSource
from flightlogs.services.airdata_reconciliation import (
    ReconciliationClassification,
    reconcile_row,
    resolve_airdata_timestamp,
)
from flightlogs.views import _flightlog_payload_from_csv_row, _normalised_row


class AirDataTimezoneResolutionTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.finder = TimezoneFinder(in_memory=True)

    def assert_resolution(self, raw, coordinates, timezone_name, expected_utc):
        result = resolve_airdata_timestamp(raw, coordinates, self.finder)
        self.assertEqual(result.timezone_name, timezone_name)
        self.assertEqual(result.proposed_utc, expected_utc)
        self.assertFalse(result.reason)

    def test_eastern_naive_timestamp(self):
        self.assert_resolution(
            "Jul 1st, 2026 02:30PM", (40.7128, -74.0060), "America/New_York",
            datetime(2026, 7, 1, 18, 30, tzinfo=timezone.utc),
        )

    def test_central_naive_timestamp(self):
        self.assert_resolution(
            "Jul 1st, 2026 02:30PM", (41.8781, -87.6298), "America/Chicago",
            datetime(2026, 7, 1, 19, 30, tzinfo=timezone.utc),
        )

    def test_mountain_naive_timestamp(self):
        self.assert_resolution(
            "Jul 1st, 2026 02:30PM", (39.7392, -104.9903), "America/Denver",
            datetime(2026, 7, 1, 20, 30, tzinfo=timezone.utc),
        )

    def test_pacific_naive_timestamp(self):
        self.assert_resolution(
            "Jul 1st, 2026 02:30PM", (34.0522, -118.2437), "America/Los_Angeles",
            datetime(2026, 7, 1, 21, 30, tzinfo=timezone.utc),
        )

    def test_arizona_naive_timestamp_has_no_dst_shift(self):
        self.assert_resolution(
            "Jul 1st, 2026 02:30PM", (33.4484, -112.0740), "America/Phoenix",
            datetime(2026, 7, 1, 21, 30, tzinfo=timezone.utc),
        )

    def test_explicit_offset_is_preserved_without_coordinates(self):
        result = resolve_airdata_timestamp("2026-07-01T14:30:00-07:00", None, self.finder)
        self.assertEqual(result.proposed_utc, datetime(2026, 7, 1, 21, 30, tzinfo=timezone.utc))
        self.assertEqual(result.utc_offset, "-07:00")

    def test_dst_fall_back_is_ambiguous(self):
        result = resolve_airdata_timestamp(
            "Nov 1st, 2026 01:30AM", (40.7128, -74.0060), self.finder
        )
        self.assertTrue(result.dst_ambiguous)
        self.assertIsNone(result.proposed_utc)

    def test_dst_spring_forward_is_nonexistent(self):
        result = resolve_airdata_timestamp(
            "Mar 8th, 2026 02:30AM", (40.7128, -74.0060), self.finder
        )
        self.assertTrue(result.dst_nonexistent)
        self.assertIsNone(result.proposed_utc)

    def test_missing_coordinates_requires_review(self):
        result = resolve_airdata_timestamp("Jul 1st, 2026 02:30PM", None, self.finder)
        self.assertIsNone(result.proposed_utc)
        self.assertIn("coordinates", result.reason)


class AirDataImporterTimezoneTests(TestCase):
    def row(self, datetime_value="Jul 1st, 2026 02:30PM", coordinates="41.8781,-87.6298", **values):
        row = {
            "Flight Date/Time": datetime_value,
            "Takeoff Lat/Long": coordinates,
            "Air Time": "00:10:00",
            "Drone Serial Number": "AIRCRAFT-SERIAL",
            "Bat Printed Serial": "PRINTED-BATTERY",
            "Bat Internal Serial": "INTERNAL-BATTERY",
            "Max Altitude (Feet)": "250.5",
            "Max Distance (Feet)": "1200",
            "Total Mileage (Feet)": "4500",
            "Max Speed (mph)": "32.4",
            "Signal Score": "98",
            "Ground Weather Summary": "Clear",
            "Photos": "7",
        }
        row.update(values)
        return _normalised_row(row)

    def assert_import_utc(self, coordinates, expected_utc):
        payload = _flightlog_payload_from_csv_row(self.row(coordinates=coordinates))
        self.assertEqual(payload["takeoff_datetime"], expected_utc)
        self.assertEqual(payload["flight_date"], date(2026, 7, 1))

    def test_importer_resolves_eastern_central_mountain_pacific_and_arizona(self):
        cases = (
            ("40.7128,-74.0060", datetime(2026, 7, 1, 18, 30, tzinfo=timezone.utc)),
            ("41.8781,-87.6298", datetime(2026, 7, 1, 19, 30, tzinfo=timezone.utc)),
            ("39.7392,-104.9903", datetime(2026, 7, 1, 20, 30, tzinfo=timezone.utc)),
            ("34.0522,-118.2437", datetime(2026, 7, 1, 21, 30, tzinfo=timezone.utc)),
            ("33.4484,-112.0740", datetime(2026, 7, 1, 21, 30, tzinfo=timezone.utc)),
        )
        for coordinates, expected in cases:
            with self.subTest(coordinates=coordinates):
                self.assert_import_utc(coordinates, expected)

    def test_importer_preserves_explicit_offset(self):
        payload = _flightlog_payload_from_csv_row(
            self.row(datetime_value="2026-07-01T14:30:00-07:00", coordinates="")
        )
        self.assertEqual(
            payload["takeoff_datetime"],
            datetime(2026, 7, 1, 21, 30, tzinfo=timezone.utc),
        )

    def test_importer_rejects_missing_and_invalid_coordinates(self):
        for coordinates in ("", "not-coordinates", "200,-500"):
            with self.subTest(coordinates=coordinates):
                with self.assertRaisesRegex(ValueError, "coordinates"):
                    _flightlog_payload_from_csv_row(self.row(coordinates=coordinates))

    def test_importer_rejects_dst_ambiguous_and_nonexistent_times(self):
        cases = (
            ("Nov 1st, 2026 01:30AM", "ambiguous"),
            ("Mar 8th, 2026 02:30AM", "nonexistent"),
        )
        for timestamp, reason in cases:
            with self.subTest(timestamp=timestamp):
                with self.assertRaisesRegex(ValueError, reason):
                    _flightlog_payload_from_csv_row(
                        self.row(datetime_value=timestamp, coordinates="40.7128,-74.0060")
                    )

    def test_representative_field_mappings_are_unchanged(self):
        payload = _flightlog_payload_from_csv_row(self.row())
        self.assertEqual(payload["drone_serial"], "AIRCRAFT-SERIAL")
        self.assertEqual(payload["battery_serial_printed"], "PRINTED-BATTERY")
        self.assertEqual(payload["battery_serial_internal"], "INTERNAL-BATTERY")
        self.assertEqual(payload["air_time"], timedelta(minutes=10))
        self.assertEqual(payload["max_altitude_ft"], 250.5)
        self.assertEqual(payload["max_distance_ft"], 1200)
        self.assertEqual(payload["total_mileage_ft"], 4500)
        self.assertEqual(payload["max_speed_mph"], 32.4)
        self.assertEqual(payload["signal_score"], 98)
        self.assertEqual(payload["ground_weather_summary"], "Clear")
        self.assertEqual(payload["photos"], 7)


class AirDataReconciliationMatchingTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.finder = TimezoneFinder(in_memory=True)

    def setUp(self):
        self.business = Business.objects.create(name="Reconciliation Business")
        self.other_business = Business.objects.create(name="Other Business")
        self.coordinates = (41.8781, -87.6298)
        self.row = {
            "datetime_raw": "Jul 1st, 2026 02:30PM",
            "coordinates": self.coordinates,
            "duration": timedelta(seconds=600),
            "aircraft_serial": "FULL-AIRCRAFT-SERIAL",
            "battery_serial": "FULL-BATTERY-SERIAL",
        }

    def flight(self, **overrides):
        values = {
            "business": self.business,
            "flight_date": date(2026, 7, 1),
            # Historical importer interpreted the Chicago wall clock as Eastern.
            "takeoff_datetime": datetime(2026, 7, 1, 14, 30, tzinfo=ZoneInfo("America/New_York")),
            "takeoff_latlong": "41.87810, -87.62980",
            "air_time": timedelta(seconds=600),
            "drone_serial": "FULL-AIRCRAFT-SERIAL",
            "battery_serial_internal": "FULL-BATTERY-SERIAL",
        }
        values.update(overrides)
        return FlightLog.objects.create(**values)

    def reconcile(self, flights):
        return reconcile_row(
            row_data=self.row, existing_flights=flights, timezone_finder=self.finder
        )

    def test_exact_serial_location_duration_match(self):
        flight = self.flight()
        result = self.reconcile([flight])
        self.assertEqual(result.classification, ReconciliationClassification.EXACT_EXISTING)
        self.assertTrue(result.evidence.aircraft_serial_match)
        self.assertLess(result.evidence.location_distance_m, 1)
        self.assertEqual(result.evidence.duration_difference_seconds, 0)

    def test_shifted_historical_timestamp_matches_same_physical_flight(self):
        flight = self.flight()
        result = self.reconcile([flight])
        self.assertEqual(result.classification, ReconciliationClassification.EXACT_EXISTING)
        self.assertEqual(
            result.timestamp.proposed_utc - flight.takeoff_datetime.astimezone(timezone.utc),
            timedelta(hours=1),
        )

    def test_missing_existing_timestamp_requires_near_exact_physical_values(self):
        flight = self.flight(takeoff_datetime=None)
        result = self.reconcile([flight])
        self.assertEqual(result.classification, ReconciliationClassification.EXACT_EXISTING)
        self.assertIsNone(result.matched_flight.takeoff_datetime)

    def test_conflicting_aircraft_serial_is_new(self):
        result = self.reconcile([self.flight(drone_serial="OTHER-AIRCRAFT")])
        self.assertEqual(result.classification, ReconciliationClassification.NEW_CSV_FLIGHT)

    def test_multiple_plausible_candidates_are_ambiguous(self):
        result = self.reconcile([self.flight(), self.flight()])
        self.assertEqual(result.classification, ReconciliationClassification.AMBIGUOUS_EXISTING)
        self.assertTrue(result.review_required)

    def test_genuinely_new_csv_flight(self):
        result = self.reconcile([])
        self.assertEqual(result.classification, ReconciliationClassification.NEW_CSV_FLIGHT)

    def test_business_isolation(self):
        other = self.flight(business=self.other_business)
        scoped = list(FlightLog.objects.filter(business=self.business))
        result = self.reconcile(scoped)
        self.assertEqual(result.classification, ReconciliationClassification.NEW_CSV_FLIGHT)
        self.assertNotIn(other, scoped)

    def test_management_command_is_read_only(self):
        self.flight()
        before_logs = FlightLog.objects.count()
        before_sources = FlightLogSource.objects.count()
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "airdata.csv"
            output_path = Path(directory) / "report.csv"
            with input_path.open("w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(
                    csv_file,
                    fieldnames=[
                        "Flight Date/Time", "Takeoff Lat/Long", "Air Time",
                        "Drone Serial Number", "Bat Internal Serial",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "Flight Date/Time": self.row["datetime_raw"],
                        "Takeoff Lat/Long": "41.8781,-87.6298",
                        "Air Time": "00:10:00",
                        "Drone Serial Number": self.row["aircraft_serial"],
                        "Bat Internal Serial": self.row["battery_serial"],
                    }
                )
            call_command(
                "reconcile_airdata_flights",
                str(input_path),
                dry_run=True,
                output=str(output_path),
                business_id=self.business.pk,
                stdout=StringIO(),
            )
            self.assertTrue(output_path.exists())
        self.assertEqual(FlightLog.objects.count(), before_logs)
        self.assertEqual(FlightLogSource.objects.count(), before_sources)
