import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("vehicles", "0002_vehiclyear_standard_rate"),
    ]

    operations = [
        migrations.AddField(
            model_name="vehicleyear",
            name="annual_interest_paid",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Optional manual entry for total vehicle-loan interest paid during this calendar year.",
                max_digits=12,
                null=True,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
    ]
