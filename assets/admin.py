from __future__ import annotations

from django.contrib import admin

from assets.models import Asset, AssetType


@admin.register(AssetType)
class AssetTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "sort_order", "business")
    list_filter = ("is_active", "business")
    search_fields = ("name",)
    ordering = ("business", "sort_order", "name")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "asset_type",
        "is_active",
        "purchase_date",
        "purchase_price",
        "depreciation_method",
        "business",
    )
    list_filter = ("asset_type", "is_active", "depreciation_method")
    search_fields = ("name",)
    ordering = ("-purchase_date", "name")
