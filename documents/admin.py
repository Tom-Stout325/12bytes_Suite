from __future__ import annotations

from django.contrib import admin

from .models import DroneIncidentReport, GeneralDocument, SOPDocument


@admin.register(DroneIncidentReport)
class DroneIncidentReportAdmin(admin.ModelAdmin):
    list_display = ("report_date", "reported_by", "location", "drone_model", "business")
    list_filter = ("business", "report_date", "injuries", "damage", "faa_report")
    search_fields = ("reported_by", "location", "description", "drone_model", "registration")
    date_hierarchy = "report_date"
    ordering = ("-report_date", "-id")


@admin.register(SOPDocument)
class SOPDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "created_at", "business")
    list_filter = ("business", "created_at")
    search_fields = ("title", "description")
    ordering = ("title",)


@admin.register(GeneralDocument)
class GeneralDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "uploaded_at", "business")
    list_filter = ("business", "category", "uploaded_at")
    search_fields = ("title", "description")
    ordering = ("title",)
