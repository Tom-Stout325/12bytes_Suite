from __future__ import annotations

from django.contrib import admin

from .models import FlightLog


@admin.register(FlightLog)
class FlightLogAdmin(admin.ModelAdmin):
    list_display = ("flight_date", "flight_title", "pilot_in_command", "drone_name", "takeoff_address", "air_time", "business")
    list_filter = ("business", "flight_date", "drone_name", "drone_type")
    search_fields = ("flight_title", "flight_description", "pilot_in_command", "license_number", "drone_name", "drone_type", "drone_serial", "drone_reg_number", "takeoff_address", "tags", "notes")
    ordering = ("-flight_date",)
    date_hierarchy = "flight_date"
    list_select_related = ("business",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        business = getattr(request, "business", None)
        if business is None:
            return qs.none()
        return qs.filter(business=business)

    def save_model(self, request, obj, form, change):
        if not change or not obj.business_id:
            obj.business = request.business
        super().save_model(request, obj, form, change)
