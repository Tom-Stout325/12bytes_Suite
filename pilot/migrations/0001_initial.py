# Generated for 12bytes Suite pilot integration

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import pilot.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("core", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PilotProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("license_number", models.CharField(blank=True, max_length=100, null=True)),
                ("license_date", models.DateField(blank=True, null=True)),
                ("license_image", models.ImageField(blank=True, null=True, upload_to=pilot.models.license_upload_path)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="core.business")),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="pilot_profile", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "app_pilotprofile",
                "ordering": ["user__last_name", "user__first_name", "user__username"],
            },
        ),
        migrations.CreateModel(
            name="Training",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("date_completed", models.DateField()),
                ("required", models.BooleanField(default=False)),
                ("certificate", models.FileField(blank=True, null=True, upload_to=pilot.models.training_certificate_upload_path)),
                ("notes", models.TextField(blank=True)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="core.business")),
                ("pilot", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="trainings", to="pilot.pilotprofile")),
            ],
            options={
                "db_table": "app_training",
                "ordering": ["-date_completed", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="pilotprofile",
            index=models.Index(fields=["business", "user"], name="app_pilotpr_busines_05fb9c_idx"),
        ),
        migrations.AddIndex(
            model_name="training",
            index=models.Index(fields=["business", "date_completed"], name="app_trainin_busines_55efc2_idx"),
        ),
        migrations.AddIndex(
            model_name="training",
            index=models.Index(fields=["business", "pilot"], name="app_trainin_busines_1d95c9_idx"),
        ),
    ]
