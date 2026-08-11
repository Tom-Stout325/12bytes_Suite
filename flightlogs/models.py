from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.models import BusinessOwnedModelMixin


def dji_source_upload_path(instance, filename):
    """Store sources under tenant and generated identifiers, never user paths."""
    return f"flightlogs/dji/{instance.business_id}/{uuid.uuid4().hex}.txt"


class FlightLog(BusinessOwnedModelMixin):
    """Canonical business-owned normalized drone flight log."""

    # Core Flight Info
    flight_date = models.DateField()
    takeoff_datetime = models.DateTimeField(null=True, blank=True)
    flight_title = models.CharField(max_length=200, blank=True)
    flight_description = models.TextField(blank=True)
    pilot_in_command = models.CharField(max_length=100, blank=True)
    license_number = models.CharField(max_length=100, blank=True)
    flight_application = models.CharField(max_length=100, blank=True)
    remote_id = models.CharField(max_length=100, blank=True)

    # Takeoff & Landing
    takeoff_latlong = models.CharField(max_length=100, blank=True)
    takeoff_address = models.CharField(max_length=255, blank=True)
    landing_time = models.TimeField(null=True, blank=True)
    air_time = models.DurationField(null=True, blank=True)
    above_sea_level_ft = models.FloatField(null=True, blank=True)

    # Drone Info
    drone_name = models.CharField(max_length=100, blank=True)
    drone_type = models.CharField(max_length=100, blank=True)
    drone_serial = models.CharField(max_length=100, blank=True)
    drone_reg_number = models.CharField(max_length=100, blank=True)
    rc_serial = models.CharField(max_length=100, blank=True)
    camera_serial = models.CharField(max_length=100, blank=True)

    # Battery Info
    battery_name = models.CharField(max_length=100, blank=True)
    battery_serial_printed = models.CharField(max_length=100, blank=True)
    battery_serial_internal = models.CharField(max_length=100, blank=True)
    takeoff_battery_pct = models.IntegerField(null=True, blank=True)
    takeoff_mah = models.IntegerField(null=True, blank=True)
    takeoff_volts = models.FloatField(null=True, blank=True)
    landing_battery_pct = models.IntegerField(null=True, blank=True)
    landing_mah = models.IntegerField(null=True, blank=True)
    landing_volts = models.FloatField(null=True, blank=True)
    battery_cycle_count = models.PositiveIntegerField(null=True, blank=True)
    minimum_cell_voltage_v = models.FloatField(null=True, blank=True)
    maximum_cell_voltage_v = models.FloatField(null=True, blank=True)
    battery_life_raw = models.PositiveSmallIntegerField(null=True, blank=True)

    # Flight Performance Metrics
    max_altitude_ft = models.FloatField(null=True, blank=True)
    max_distance_ft = models.FloatField(null=True, blank=True)
    max_battery_temp_f = models.FloatField(null=True, blank=True)
    max_speed_mph = models.FloatField(null=True, blank=True)
    maximum_vertical_speed_mps = models.FloatField(null=True, blank=True)
    total_mileage_ft = models.FloatField(null=True, blank=True)
    signal_score = models.FloatField(null=True, blank=True)
    max_compass_rate = models.FloatField(null=True, blank=True)
    avg_wind = models.FloatField(null=True, blank=True)
    max_gust = models.FloatField(null=True, blank=True)
    signal_losses = models.IntegerField(null=True, blank=True)
    maximum_satellites = models.PositiveSmallIntegerField(null=True, blank=True)
    minimum_airborne_satellites = models.PositiveSmallIntegerField(null=True, blank=True)
    minimum_airborne_gps_level = models.PositiveSmallIntegerField(null=True, blank=True)
    flight_modes = models.CharField(max_length=500, blank=True)

    # DJI-native operational alerts. These remain separate from user notes.
    dji_warnings = models.TextField(blank=True)
    dji_serious_warnings = models.TextField(blank=True)
    dji_tips = models.TextField(blank=True)

    # Ground Weather Conditions
    ground_weather_summary = models.CharField(max_length=255, blank=True)
    ground_temp_f = models.FloatField(null=True, blank=True)
    visibility_miles = models.FloatField(null=True, blank=True)
    wind_speed = models.FloatField(null=True, blank=True)
    wind_direction = models.CharField(max_length=50, blank=True)
    cloud_cover = models.CharField(max_length=100, blank=True)
    humidity_pct = models.IntegerField(null=True, blank=True)
    dew_point_f = models.FloatField(null=True, blank=True)
    pressure_inhg = models.FloatField(null=True, blank=True)
    rain_rate = models.CharField(max_length=50, blank=True)
    rain_chance = models.CharField(max_length=50, blank=True)

    # Sun & Moon
    sunrise = models.CharField(max_length=50, blank=True)
    sunset = models.CharField(max_length=50, blank=True)
    moon_phase = models.CharField(max_length=50, blank=True)
    moon_visibility = models.CharField(max_length=50, blank=True)

    # Media & Notes
    photos = models.IntegerField(null=True, blank=True)
    videos = models.IntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    tags = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "flightplan_flightlog"
        ordering = ["-flight_date"]
        indexes = [
            models.Index(fields=["business", "flight_date"]),
            models.Index(fields=["business", "drone_name"]),
            models.Index(fields=["business", "battery_serial_internal"]),
        ]

    def __str__(self) -> str:
        return f"{self.flight_title or 'Flight'} on {self.flight_date}"

    def clean(self):
        super().clean()
        if self.takeoff_battery_pct is not None and not 0 <= self.takeoff_battery_pct <= 100:
            raise ValidationError({"takeoff_battery_pct": "Battery percentage must be between 0 and 100."})
        if self.landing_battery_pct is not None and not 0 <= self.landing_battery_pct <= 100:
            raise ValidationError({"landing_battery_pct": "Battery percentage must be between 0 and 100."})


class FlightLogSource(BusinessOwnedModelMixin):
    class SourceType(models.TextChoices):
        DJI_TXT = "dji_txt", "DJI FlightRecord"

    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        PARSING = "parsing", "Parsing"
        COMPLETE = "complete", "Complete"
        FAILED = "failed", "Failed"
        REVIEW = "review", "Review required"

    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    original_filename = models.CharField(max_length=255)
    file = models.FileField(upload_to=dji_source_upload_path, blank=True)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveBigIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UPLOADED)
    safe_error_code = models.CharField(max_length=64, blank=True)
    safe_error_detail = models.CharField(max_length=255, blank=True)
    parser_version = models.CharField(max_length=32, blank=True)
    log_version = models.PositiveSmallIntegerField(null=True, blank=True)
    encrypted = models.BooleanField(null=True, blank=True)
    aircraft_model_code = models.PositiveSmallIntegerField(null=True, blank=True)
    aircraft_serial = models.CharField(max_length=100, blank=True)
    aircraft_serial_header = models.CharField(max_length=100, blank=True)
    battery_serial = models.CharField(max_length=100, blank=True)
    battery_serial_header = models.CharField(max_length=100, blank=True)
    flight_log = models.ForeignKey(
        FlightLog,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sources",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="flightlog_sources_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "sha256"],
                name="uniq_flightlog_source_business_sha256",
            )
        ]
        indexes = [models.Index(fields=["business", "status"])]

    def __str__(self):
        return f"{self.get_source_type_display()}: {self.original_filename}"
