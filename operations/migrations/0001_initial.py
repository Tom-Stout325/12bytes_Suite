# Generated manually for 12bytes Suite operations integration.

from django.conf import settings
import django.core.validators
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("core", "0001_initial"),
        ("ledger", "0003_vehicle_subcategory_requires_vehicle"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OpsPlan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_name", models.CharField(blank=True, max_length=200, verbose_name="plan name")),
                ("plan_year", models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(2000), django.core.validators.MaxValueValidator(2100)])),
                ("status", models.CharField(choices=[("Draft", "Draft"), ("In Review", "In Review"), ("Approved", "Approved"), ("Archived", "Archived")], default="Draft", max_length=12)),
                ("start_date", models.DateField(blank=True, null=True)),
                ("end_date", models.DateField(blank=True, null=True)),
                ("address", models.CharField(blank=True, max_length=255)),
                ("pilot_in_command", models.CharField(blank=True, max_length=150)),
                ("visual_observers", models.CharField(blank=True, help_text="Comma-separated names", max_length=255)),
                ("airspace_class", models.CharField(blank=True, max_length=50)),
                ("waivers_required", models.BooleanField(default=False)),
                ("airport", models.CharField(blank=True, max_length=50)),
                ("airport_phone", models.CharField(blank=True, max_length=50)),
                ("contact", models.CharField(blank=True, max_length=50)),
                ("emergency_procedures", models.TextField(blank=True)),
                ("notes", models.TextField(blank=True)),
                ("waiver", models.FileField(blank=True, null=True, upload_to="ops_plans/")),
                ("location_map", models.FileField(blank=True, null=True, upload_to="ops_plans/")),
                ("client_approval", models.FileField(blank=True, null=True, upload_to="ops_plans/")),
                ("client_approval_notes", models.TextField(blank=True)),
                ("approval_requested_at", models.DateTimeField(blank=True, help_text="When the approval link was generated/sent.", null=True)),
                ("approval_token", models.CharField(blank=True, db_index=True, help_text="One-time token embedded in approval URL.", max_length=64, null=True)),
                ("approval_token_expires_at", models.DateTimeField(blank=True, null=True)),
                ("approved_name", models.CharField(blank=True, help_text="Typed full name used to approve.", max_length=200)),
                ("approved_email", models.EmailField(blank=True, help_text="Expected recipient email (optional but recommended).", max_length=254)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("approved_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("approved_user_agent", models.TextField(blank=True)),
                ("approved_notes_snapshot", models.TextField(blank=True, help_text="Immutable copy of Notes as seen by the approver.")),
                ("attestation_hash", models.CharField(blank=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="core.business")),
                ("client", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ops_plans", to="ledger.contact")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="opsplans_created", to=settings.AUTH_USER_MODEL)),
                ("job", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ops_plans", to="ledger.job")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="opsplans_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "flightplan_opsplan",
                "ordering": ["-plan_year", "-updated_at"],
                "indexes": [
                    models.Index(fields=["business", "job", "plan_year"], name="opsplan_bus_job_year_idx"),
                    models.Index(fields=["business", "status"], name="opsplan_bus_status_idx"),
                    models.Index(fields=["status"], name="opsplan_status_idx"),
                    models.Index(fields=["updated_at"], name="opsplan_updated_idx"),
                    models.Index(fields=["approved_at"], name="opsplan_approved_idx"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="opsplan",
            constraint=models.UniqueConstraint(fields=("business", "job", "plan_year"), name="uniq_opsplan_business_job_year"),
        ),
    ]
