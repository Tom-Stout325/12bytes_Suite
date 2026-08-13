from django.contrib import admin

from .models import (
    BatteryFamily,
    DroneModel,
    DroneModelAlias,
    DroneModelIdentifier,
    DroneSafetyProfile,
)


class AliasInline(admin.TabularInline):
    model = DroneModelAlias
    extra = 0


class IdentifierInline(admin.TabularInline):
    model = DroneModelIdentifier
    extra = 0


@admin.register(DroneModel)
class DroneModelAdmin(admin.ModelAdmin):
    list_display = ("full_display_name", "year_released", "is_enterprise", "battery_family", "active")
    list_filter = ("manufacturer", "is_enterprise", "active", "battery_family")
    search_fields = ("manufacturer", "name", "full_display_name", "aliases__alias", "identifiers__identifier")
    readonly_fields = ("normalized_name", "full_display_name", "created_at", "updated_at")
    inlines = (AliasInline, IdentifierInline)


@admin.register(BatteryFamily)
class BatteryFamilyAdmin(admin.ModelAdmin):
    list_display = ("name", "manufacturer", "active")
    list_filter = ("manufacturer", "active")
    search_fields = ("manufacturer", "name")
    readonly_fields = ("normalized_name", "created_at", "updated_at")


@admin.register(DroneModelAlias)
class DroneModelAliasAdmin(admin.ModelAdmin):
    list_display = ("alias", "drone_model", "source")
    list_filter = ("source", "drone_model__manufacturer")
    search_fields = ("alias", "drone_model__name")
    readonly_fields = ("normalized_alias", "created_at")


@admin.register(DroneModelIdentifier)
class DroneModelIdentifierAdmin(admin.ModelAdmin):
    list_display = ("provider", "identifier", "drone_model")
    list_filter = ("provider",)
    search_fields = ("identifier", "drone_model__name")
    readonly_fields = ("normalized_identifier", "created_at")


@admin.register(DroneSafetyProfile)
class DroneSafetyProfileAdmin(admin.ModelAdmin):
    list_display = ("drone_model", "updated_at")
    search_fields = ("drone_model__manufacturer", "drone_model__name", "safety_features")
    readonly_fields = ("created_at", "updated_at")
