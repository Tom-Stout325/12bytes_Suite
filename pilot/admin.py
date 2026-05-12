from django.contrib import admin

from .models import PilotProfile, Training


@admin.register(PilotProfile)
class PilotProfileAdmin(admin.ModelAdmin):
    list_display = ("id", "business", "user", "license_number", "license_date")
    list_filter = ("business",)
    search_fields = ("business__name", "user__username", "user__email", "user__first_name", "user__last_name", "license_number")
    list_select_related = ("business", "user")


@admin.register(Training)
class TrainingAdmin(admin.ModelAdmin):
    list_display = ("title", "date_completed", "required", "business", "pilot")
    list_filter = ("business", "required", "date_completed")
    search_fields = ("title", "business__name", "pilot__user__username", "pilot__user__first_name", "pilot__user__last_name")
    list_select_related = ("business", "pilot", "pilot__user")
