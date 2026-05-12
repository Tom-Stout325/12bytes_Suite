from __future__ import annotations

from django.contrib import admin

from assets.models import Asset


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "asset_type",
        "purchase_date",
        "purchase_price",
        "depreciation_method",
        "business",
    )
    list_filter = ("asset_type", "depreciation_method")
    search_fields = ("name",)
    ordering = ("-purchase_date", "name")
