# Generated manually for 12bytes Suite asset cleanup.

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def forwards(apps, schema_editor):
    Asset = apps.get_model("assets", "Asset")
    AssetType = apps.get_model("assets", "AssetType")

    defaults = [
        ("equipment", "Equipment", 10),
        ("computer", "Computer", 20),
        ("furniture", "Furniture", 30),
        ("other", "Other", 90),
    ]

    business_ids = set(Asset.objects.values_list("business_id", flat=True).distinct())

    for business_id in business_ids:
        created_types = {}
        for slug, name, sort_order in defaults:
            obj, _ = AssetType.objects.get_or_create(
                business_id=business_id,
                slug=slug,
                defaults={"name": name, "sort_order": sort_order, "is_active": True},
            )
            created_types[slug] = obj

        for asset in Asset.objects.filter(business_id=business_id):
            old_value = (asset.asset_type or "equipment").strip().lower()
            if old_value == "vehicle":
                # Vehicle records now live in the vehicles app, so old vehicle-type asset rows are kept as Other.
                old_value = "other"

            asset_type = created_types.get(old_value)
            if asset_type is None:
                label = old_value.replace("_", " ").replace("-", " ").title() or "Other"
                asset_type, _ = AssetType.objects.get_or_create(
                    business_id=business_id,
                    slug=old_value,
                    defaults={"name": label, "sort_order": 80, "is_active": True},
                )
                created_types[old_value] = asset_type

            asset.asset_type_fk_id = asset_type.pk
            asset.is_active = True
            asset.save(update_fields=["asset_type_fk", "is_active"])


def backwards(apps, schema_editor):
    Asset = apps.get_model("assets", "Asset")
    for asset in Asset.objects.select_related("asset_type"):
        asset.asset_type_text = asset.asset_type.slug if asset.asset_type_id else "equipment"
        asset.save(update_fields=["asset_type_text"])


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0002_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="AssetType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=80)),
                ("slug", models.SlugField(blank=True, max_length=100)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="core.business")),
            ],
            options={
                "ordering": ["sort_order", "name"],
            },
        ),
        migrations.AddConstraint(
            model_name="assettype",
            constraint=models.UniqueConstraint(fields=("business", "slug"), name="uniq_asset_type_business_slug"),
        ),
        migrations.AddIndex(
            model_name="assettype",
            index=models.Index(fields=["business", "is_active"], name="assets_asse_busines_7d2e48_idx"),
        ),
        migrations.AddField(
            model_name="asset",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="asset",
            name="asset_type_fk",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="assets", to="assets.assettype"),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.RemoveIndex(
            model_name="asset",
            name="assets_asse_busines_622638_idx",
        ),
        migrations.RemoveField(
            model_name="asset",
            name="vehicle",
        ),
        migrations.RemoveField(
            model_name="asset",
            name="asset_type",
        ),
        migrations.RenameField(
            model_name="asset",
            old_name="asset_type_fk",
            new_name="asset_type",
        ),
        migrations.AlterField(
            model_name="asset",
            name="asset_type",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="assets", to="assets.assettype"),
        ),
        migrations.AddIndex(
            model_name="asset",
            index=models.Index(fields=["business", "asset_type"], name="assets_asse_busines_d2219d_idx"),
        ),
        migrations.AddIndex(
            model_name="asset",
            index=models.Index(fields=["business", "is_active"], name="assets_asse_busines_705612_idx"),
        ),
    ]
