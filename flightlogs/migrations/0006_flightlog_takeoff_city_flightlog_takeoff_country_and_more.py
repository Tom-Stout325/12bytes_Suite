from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("flightlogs", "0005_alter_flightlogsource_file_and_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="flightlog",
            name="takeoff_city",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="flightlog",
            name="takeoff_country",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="flightlog",
            name="takeoff_postal_code",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="flightlog",
            name="takeoff_state",
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
