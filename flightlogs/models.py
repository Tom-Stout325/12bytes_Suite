from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from core.models import BusinessOwnedModelMixin


class FlightLog(BusinessOwnedModelMixin):
    """Business-owned drone flight log imported from AirData/FlightPlan CSV files."""

    # Core Flight Info
    flight_date = models.DateField()
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

    # Flight Performance Metrics
    max_altitude_ft = models.FloatField(null=True, blank=True)
    max_distance_ft = models.FloatField(null=True, blank=True)
    max_battery_temp_f = models.FloatField(null=True, blank=True)
    max_speed_mph = models.FloatField(null=True, blank=True)
    total_mileage_ft = models.FloatField(null=True, blank=True)
    signal_score = models.FloatField(null=True, blank=True)
    max_compass_rate = models.FloatField(null=True, blank=True)
    avg_wind = models.FloatField(null=True, blank=True)
    max_gust = models.FloatField(null=True, blank=True)
    signal_losses = models.IntegerField(null=True, blank=True)

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
