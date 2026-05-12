from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("ledger", "0002_initial"),
        ("invoices", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="team",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="invoices",
                to="ledger.team",
            ),
        ),
    ]
