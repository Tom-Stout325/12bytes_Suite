from __future__ import annotations

from django.contrib import admin

from .models import Contractor1099, ContractorW9Submission


@admin.register(ContractorW9Submission)
class ContractorW9SubmissionAdmin(admin.ModelAdmin):
    list_display = ("id", "business", "contact", "tin_last4", "entity_type", "review_status", "submitted_at")
    list_filter = ("business", "entity_type", "tin_type", "review_status", "submitted_at")
    search_fields = ("contact__display_name", "full_name", "business_name", "tin_last4")
    readonly_fields = (
        "created_at",
        "updated_at",
        "submitted_at",
        "submitted_ip",
        "submitted_ua",
        "reviewed_at",
        "reviewed_by_name",
    )
    ordering = ("-submitted_at",)


@admin.register(Contractor1099)
class Contractor1099Admin(admin.ModelAdmin):
    list_display = ("id", "business", "contact", "tax_year", "generated_at", "emailed_at", "email_count")
    list_filter = ("business", "tax_year")
    search_fields = ("contact__display_name", "contact__email")
    ordering = ("-tax_year", "-generated_at")
