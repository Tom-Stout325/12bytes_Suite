# Generated for 12bytes Suite documents integration.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="GeneralDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)),
                ("category", models.CharField(choices=[("Insurance", "Insurance"), ("FAA Airspace Waivers", "FAA Airspace Waivers"), ("FAA Operational Waivers", "FAA Operational Waivers"), ("Registrations", "Drone Registrations"), ("event", "Event Instructions"), ("Policies", "Policies"), ("Compliance", "Compliance"), ("Legal", "Legal"), ("Other", "Other")], default="Other", max_length=50)),
                ("description", models.TextField(blank=True)),
                ("file", models.FileField(upload_to="general_documents/")),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="core.business")),
            ],
            options={"db_table": "flightplan_generaldocument", "ordering": ["title"]},
        ),
        migrations.CreateModel(
            name="SOPDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("file", models.FileField(upload_to="sop_docs/")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="core.business")),
            ],
            options={"db_table": "flightplan_sopdocument", "ordering": ["title"]},
        ),
        migrations.CreateModel(
            name="DroneIncidentReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("report_date", models.DateField()),
                ("reported_by", models.CharField(max_length=100)),
                ("contact", models.CharField(max_length=100)),
                ("role", models.CharField(max_length=100)),
                ("event_date", models.DateField()),
                ("event_time", models.TimeField()),
                ("location", models.CharField(max_length=200)),
                ("event_type", models.CharField(max_length=50)),
                ("description", models.TextField()),
                ("injuries", models.BooleanField(default=False)),
                ("injury_details", models.TextField(blank=True)),
                ("damage", models.BooleanField(default=False)),
                ("damage_cost", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("damage_desc", models.TextField(blank=True)),
                ("drone_model", models.CharField(max_length=100)),
                ("registration", models.CharField(max_length=100)),
                ("controller", models.CharField(blank=True, max_length=100)),
                ("payload", models.CharField(blank=True, max_length=100)),
                ("battery", models.CharField(blank=True, max_length=50)),
                ("weather", models.CharField(blank=True, max_length=100)),
                ("wind", models.CharField(blank=True, max_length=50)),
                ("temperature", models.CharField(blank=True, max_length=50)),
                ("lighting", models.CharField(blank=True, max_length=100)),
                ("witnesses", models.BooleanField(default=False)),
                ("witness_details", models.TextField(blank=True)),
                ("emergency", models.BooleanField(default=False)),
                ("agency_response", models.TextField(blank=True)),
                ("scene_action", models.TextField(blank=True)),
                ("faa_report", models.BooleanField(default=False)),
                ("faa_ref", models.CharField(blank=True, max_length=100)),
                ("cause", models.TextField(blank=True)),
                ("notes", models.TextField(blank=True)),
                ("signature", models.CharField(max_length=100)),
                ("sign_date", models.DateField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="core.business")),
            ],
            options={"db_table": "flightplan_droneincidentreport", "ordering": ["-report_date", "-id"]},
        ),
        migrations.AddIndex(model_name="generaldocument", index=models.Index(fields=["business", "category"], name="flightplan__busines_038bb6_idx")),
        migrations.AddIndex(model_name="generaldocument", index=models.Index(fields=["business", "uploaded_at"], name="flightplan__busines_33f045_idx")),
        migrations.AddIndex(model_name="sopdocument", index=models.Index(fields=["business", "title"], name="flightplan__busines_71b715_idx")),
        migrations.AddIndex(model_name="droneincidentreport", index=models.Index(fields=["business", "report_date"], name="flightplan__busines_9242cd_idx")),
        migrations.AddIndex(model_name="droneincidentreport", index=models.Index(fields=["business", "event_date"], name="flightplan__busines_1da949_idx")),
    ]
