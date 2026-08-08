from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("flightlogs", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="flightlog",
            name="takeoff_datetime",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
