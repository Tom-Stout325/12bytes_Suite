from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("invoices", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="BusinessEmailSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("display_name", models.CharField(blank=True, max_length=120)),
                ("from_name", models.CharField(blank=True, max_length=120)),
                ("from_email", models.EmailField(blank=True, max_length=254)),
                ("reply_to_email", models.EmailField(blank=True, max_length=254)),
                ("invoice_cc_email", models.EmailField(blank=True, max_length=254)),
                ("payment_questions_email", models.EmailField(blank=True, max_length=254)),
                ("email_signature", models.TextField(blank=True)),
                ("send_mode", models.CharField(choices=[("platform_default", "MoneyPro default"), ("custom_domain", "Custom domain"), ("disabled", "Disabled")], default="platform_default", max_length=20)),
                ("is_active", models.BooleanField(default=True)),
                ("verified_for_sending", models.BooleanField(default=True)),
                ("last_verified_at", models.DateTimeField(blank=True, null=True)),
                ("custom_domain", models.CharField(blank=True, max_length=120)),
                ("custom_domain_status", models.CharField(choices=[("not_configured", "Not configured"), ("pending", "Pending verification"), ("verified", "Verified"), ("failed", "Verification failed")], default="not_configured", max_length=20)),
                ("custom_return_path_domain", models.CharField(blank=True, max_length=120)),
                ("dkim_verified", models.BooleanField(default=False)),
                ("spf_verified", models.BooleanField(default=False)),
                ("tracking_domain_verified", models.BooleanField(default=False)),
                ("sendgrid_domain_id", models.CharField(blank=True, max_length=60)),
                ("verification_notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("business", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="email_settings", to="core.business")),
            ],
            options={
                "verbose_name": "Business email settings",
                "verbose_name_plural": "Business email settings",
            },
        ),
        migrations.CreateModel(
            name="OutgoingEmailLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("template_type", models.CharField(choices=[("invoice", "Invoice")], default="invoice", max_length=20)),
                ("recipient_email", models.EmailField(max_length=254)),
                ("cc_email", models.EmailField(blank=True, max_length=254)),
                ("subject", models.CharField(max_length=255)),
                ("from_email", models.EmailField(blank=True, max_length=254)),
                ("reply_to_email", models.EmailField(blank=True, max_length=254)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("sent", "Sent"), ("failed", "Failed")], default="pending", max_length=10)),
                ("provider_message_id", models.CharField(blank=True, max_length=255)),
                ("error_message", models.TextField(blank=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="outgoing_email_logs", to="core.business")),
                ("invoice", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="email_logs", to="invoices.invoice")),
                ("sent_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sent_email_logs", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
