from __future__ import annotations

from django.contrib import admin

from assets.models import AircraftModel, Asset, AssetType


@admin.register(AircraftModel)
class AircraftModelAdmin(admin.ModelAdmin):
    list_display = ("name", "manufacturer", "dji_model_code", "business")
    list_filter = ("manufacturer", "business")
    search_fields = ("name", "manufacturer", "aliases")


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
        "manufacturer",
        "model",
        "serial_number",
        "faa_registration",
        "asset_type",
        "drone_model",
        "is_active",
        "purchase_date",
        "purchase_price",
        "depreciation_method",
        "business",
    )
    list_filter = ("asset_type", "is_active", "depreciation_method")
    search_fields = ("name", "manufacturer", "model", "serial_number", "faa_registration")
    ordering = ("-purchase_date", "name")
