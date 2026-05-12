
from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ("vehicles", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="vehicleyear",
            name="standard_mileage_rate",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text="Optional standard mileage rate for this year.",
                max_digits=6,
                null=True,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
    ]
