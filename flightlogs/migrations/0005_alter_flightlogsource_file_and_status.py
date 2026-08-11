import flightlogs.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("flightlogs", "0004_flightlog_battery_cycle_count_and_more")]

    operations = [
        migrations.AlterField(
            model_name="flightlogsource",
            name="file",
            field=models.FileField(blank=True, upload_to=flightlogs.models.dji_source_upload_path),
        ),
        migrations.AlterField(
            model_name="flightlogsource",
            name="status",
            field=models.CharField(
                choices=[
                    ("uploaded", "Uploaded"),
                    ("parsing", "Parsing"),
                    ("complete", "Complete"),
                    ("failed", "Failed"),
                    ("review", "Review required"),
                ],
                default="uploaded",
                max_length=20,
            ),
        ),
    ]
