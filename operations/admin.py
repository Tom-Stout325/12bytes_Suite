from __future__ import annotations

from django.contrib import admin

from .models import OpsPlan


@admin.register(OpsPlan)
class OpsPlanAdmin(admin.ModelAdmin):
    list_display = ("event_name", "job", "plan_year", "status", "waivers_required", "business", "updated_at")
    list_filter = ("business", "waivers_required", "status", "plan_year")
    search_fields = ("event_name", "job__label", "pilot_in_command", "airport", "address", "client__display_name")
    list_select_related = ("business", "job", "client")
    ordering = ("-plan_year", "-updated_at")

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
        if not obj.created_by_id:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
