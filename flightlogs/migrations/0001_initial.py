# Generated manually for 12bytes Suite flightlogs integration.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="FlightLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("flight_date", models.DateField()),
                ("flight_title", models.CharField(blank=True, max_length=200)),
                ("flight_description", models.TextField(blank=True)),
                ("pilot_in_command", models.CharField(blank=True, max_length=100)),
                ("license_number", models.CharField(blank=True, max_length=100)),
                ("flight_application", models.CharField(blank=True, max_length=100)),
                ("remote_id", models.CharField(blank=True, max_length=100)),
                ("takeoff_latlong", models.CharField(blank=True, max_length=100)),
                ("takeoff_address", models.CharField(blank=True, max_length=255)),
                ("landing_time", models.TimeField(blank=True, null=True)),
                ("air_time", models.DurationField(blank=True, null=True)),
                ("above_sea_level_ft", models.FloatField(blank=True, null=True)),
                ("drone_name", models.CharField(blank=True, max_length=100)),
                ("drone_type", models.CharField(blank=True, max_length=100)),
                ("drone_serial", models.CharField(blank=True, max_length=100)),
                ("drone_reg_number", models.CharField(blank=True, max_length=100)),
                ("battery_name", models.CharField(blank=True, max_length=100)),
                ("battery_serial_printed", models.CharField(blank=True, max_length=100)),
                ("battery_serial_internal", models.CharField(blank=True, max_length=100)),
                ("takeoff_battery_pct", models.IntegerField(blank=True, null=True)),
                ("takeoff_mah", models.IntegerField(blank=True, null=True)),
                ("takeoff_volts", models.FloatField(blank=True, null=True)),
                ("landing_battery_pct", models.IntegerField(blank=True, null=True)),
                ("landing_mah", models.IntegerField(blank=True, null=True)),
                ("landing_volts", models.FloatField(blank=True, null=True)),
                ("max_altitude_ft", models.FloatField(blank=True, null=True)),
                ("max_distance_ft", models.FloatField(blank=True, null=True)),
                ("max_battery_temp_f", models.FloatField(blank=True, null=True)),
                ("max_speed_mph", models.FloatField(blank=True, null=True)),
                ("total_mileage_ft", models.FloatField(blank=True, null=True)),
                ("signal_score", models.FloatField(blank=True, null=True)),
                ("max_compass_rate", models.FloatField(blank=True, null=True)),
                ("avg_wind", models.FloatField(blank=True, null=True)),
                ("max_gust", models.FloatField(blank=True, null=True)),
                ("signal_losses", models.IntegerField(blank=True, null=True)),
                ("ground_weather_summary", models.CharField(blank=True, max_length=255)),
                ("ground_temp_f", models.FloatField(blank=True, null=True)),
                ("visibility_miles", models.FloatField(blank=True, null=True)),
                ("wind_speed", models.FloatField(blank=True, null=True)),
                ("wind_direction", models.CharField(blank=True, max_length=50)),
                ("cloud_cover", models.CharField(blank=True, max_length=100)),
                ("humidity_pct", models.IntegerField(blank=True, null=True)),
                ("dew_point_f", models.FloatField(blank=True, null=True)),
                ("pressure_inhg", models.FloatField(blank=True, null=True)),
                ("rain_rate", models.CharField(blank=True, max_length=50)),
                ("rain_chance", models.CharField(blank=True, max_length=50)),
                ("sunrise", models.CharField(blank=True, max_length=50)),
                ("sunset", models.CharField(blank=True, max_length=50)),
                ("moon_phase", models.CharField(blank=True, max_length=50)),
                ("moon_visibility", models.CharField(blank=True, max_length=50)),
                ("photos", models.IntegerField(blank=True, null=True)),
                ("videos", models.IntegerField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
                ("tags", models.CharField(blank=True, max_length=255)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="core.business")),
            ],
            options={
                "db_table": "flightplan_flightlog",
                "ordering": ["-flight_date"],
            },
        ),
        migrations.AddIndex(model_name="flightlog", index=models.Index(fields=["business", "flight_date"], name="flightplan__busines_894019_idx")),
        migrations.AddIndex(model_name="flightlog", index=models.Index(fields=["business", "drone_name"], name="flightplan__busines_1fdb23_idx")),
        migrations.AddIndex(model_name="flightlog", index=models.Index(fields=["business", "battery_serial_internal"], name="flightplan__busines_8eede7_idx")),
    ]
