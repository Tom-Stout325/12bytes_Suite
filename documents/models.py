from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from core.models import BusinessOwnedModelMixin


class DroneIncidentReport(BusinessOwnedModelMixin):
    report_date = models.DateField()
    reported_by = models.CharField(max_length=100)
    contact = models.CharField(max_length=100)
    role = models.CharField(max_length=100)

    event_date = models.DateField()
    event_time = models.TimeField()
    location = models.CharField(max_length=200)
    event_type = models.CharField(max_length=50)
    description = models.TextField()
    injuries = models.BooleanField(default=False)
    injury_details = models.TextField(blank=True)
    damage = models.BooleanField(default=False)
    damage_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    damage_desc = models.TextField(blank=True)

    drone_model = models.CharField(max_length=100)
    registration = models.CharField(max_length=100)
    controller = models.CharField(max_length=100, blank=True)
    payload = models.CharField(max_length=100, blank=True)
    battery = models.CharField(max_length=50, blank=True)

    weather = models.CharField(max_length=100, blank=True)
    wind = models.CharField(max_length=50, blank=True)
    temperature = models.CharField(max_length=50, blank=True)
    lighting = models.CharField(max_length=100, blank=True)

    witnesses = models.BooleanField(default=False)
    witness_details = models.TextField(blank=True)

    emergency = models.BooleanField(default=False)
    agency_response = models.TextField(blank=True)
    scene_action = models.TextField(blank=True)
    faa_report = models.BooleanField(default=False)
    faa_ref = models.CharField(max_length=100, blank=True)

    cause = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    signature = models.CharField(max_length=100)
    sign_date = models.DateField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "flightplan_droneincidentreport"
        ordering = ["-report_date", "-id"]
        indexes = [
            models.Index(fields=["business", "report_date"], name="flightplan__busines_9242cd_idx"),
            models.Index(fields=["business", "event_date"], name="flightplan__busines_1da949_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.report_date:%m/%d/%Y} - {self.reported_by}"

    def clean(self):
        super().clean()
        if self.injuries and not self.injury_details.strip():
            raise ValidationError({"injury_details": "Add injury details when injuries are selected."})
        if self.damage and not self.damage_desc.strip():
            raise ValidationError({"damage_desc": "Add damage details when damage is selected."})


class SOPDocument(BusinessOwnedModelMixin):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    file = models.FileField(upload_to="sop_docs/")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "flightplan_sopdocument"
        ordering = ["title"]
        indexes = [models.Index(fields=["business", "title"], name="flightplan__busines_71b715_idx")]

    def __str__(self) -> str:
        return self.title


class GeneralDocument(BusinessOwnedModelMixin):
    class Category(models.TextChoices):
        INSURANCE = "Insurance", "Insurance"
        FAA_AIRSPACE_WAIVERS = "FAA Airspace Waivers", "FAA Airspace Waivers"
        FAA_OPERATIONAL_WAIVERS = "FAA Operational Waivers", "FAA Operational Waivers"
        REGISTRATIONS = "Registrations", "Drone Registrations"
        EVENT = "event", "Event Instructions"
        POLICIES = "Policies", "Policies"
        COMPLIANCE = "Compliance", "Compliance"
        LEGAL = "Legal", "Legal"
        OTHER = "Other", "Other"

    title = models.CharField(max_length=255)
    category = models.CharField(max_length=50, choices=Category.choices, default=Category.OTHER)
    description = models.TextField(blank=True)
    file = models.FileField(upload_to="general_documents/")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "flightplan_generaldocument"
        ordering = ["title"]
        indexes = [
            models.Index(fields=["business", "category"], name="flightplan__busines_038bb6_idx"),
            models.Index(fields=["business", "uploaded_at"], name="flightplan__busines_33f045_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.get_category_display()})"
