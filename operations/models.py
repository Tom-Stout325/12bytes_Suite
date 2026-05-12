from __future__ import annotations

import hashlib
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse

from core.models import BusinessOwnedModelMixin

User = settings.AUTH_USER_MODEL


class OpsPlan(BusinessOwnedModelMixin):
    """Business-owned operations plan tied to a MoneyPro Job."""

    DRAFT = "Draft"
    IN_REVIEW = "In Review"
    APPROVED = "Approved"
    ARCHIVED = "Archived"
    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (IN_REVIEW, "In Review"),
        (APPROVED, "Approved"),
        (ARCHIVED, "Archived"),
    ]

    job = models.ForeignKey("ledger.Job", on_delete=models.CASCADE, related_name="ops_plans")
    event_name = models.CharField("plan name", max_length=200, blank=True)
    plan_year = models.PositiveIntegerField(validators=[MinValueValidator(2000), MaxValueValidator(2100)])
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=DRAFT)

    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    client = models.ForeignKey("ledger.Contact", on_delete=models.SET_NULL, null=True, blank=True, related_name="ops_plans")
    address = models.CharField(max_length=255, blank=True)
    pilot_in_command = models.CharField(max_length=150, blank=True)
    visual_observers = models.CharField(max_length=255, blank=True, help_text="Comma-separated names")
    airspace_class = models.CharField(max_length=50, blank=True)
    waivers_required = models.BooleanField(default=False)
    airport = models.CharField(max_length=50, blank=True)
    airport_phone = models.CharField(max_length=50, blank=True)
    contact = models.CharField(max_length=50, blank=True)
    emergency_procedures = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    waiver = models.FileField(upload_to="ops_plans/", null=True, blank=True)
    location_map = models.FileField(upload_to="ops_plans/", null=True, blank=True)
    client_approval = models.FileField(upload_to="ops_plans/", null=True, blank=True)
    client_approval_notes = models.TextField(blank=True)

    approval_requested_at = models.DateTimeField(null=True, blank=True, help_text="When the approval link was generated/sent.")
    approval_token = models.CharField(max_length=64, null=True, blank=True, db_index=True, help_text="One-time token embedded in approval URL.")
    approval_token_expires_at = models.DateTimeField(null=True, blank=True)

    approved_name = models.CharField(max_length=200, blank=True, help_text="Typed full name used to approve.")
    approved_email = models.EmailField(blank=True, help_text="Expected recipient email (optional but recommended).")
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_ip = models.GenericIPAddressField(null=True, blank=True)
    approved_user_agent = models.TextField(blank=True)
    approved_notes_snapshot = models.TextField(blank=True, help_text="Immutable copy of Notes as seen by the approver.")
    attestation_hash = models.CharField(max_length=64, blank=True)

    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="opsplans_created")
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="opsplans_updated")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "flightplan_opsplan"
        ordering = ["-plan_year", "-updated_at"]
        constraints = [
            models.UniqueConstraint(fields=["business", "job", "plan_year"], name="uniq_opsplan_business_job_year"),
        ]
        indexes = [
            models.Index(fields=["business", "job", "plan_year"], name="opsplan_bus_job_year_idx"),
            models.Index(fields=["business", "status"], name="opsplan_bus_status_idx"),
            models.Index(fields=["status"], name="opsplan_status_idx"),
            models.Index(fields=["updated_at"], name="opsplan_updated_idx"),
            models.Index(fields=["approved_at"], name="opsplan_approved_idx"),
        ]

    def __str__(self) -> str:
        return f"Ops Plan: {self.event_name or self.job} ({self.plan_year}) [{self.status}]"

    @property
    def event(self):
        """Compatibility alias for older templates that used plan.event."""
        return self.job

    def get_absolute_url(self) -> str:
        return reverse("operations:ops_plan_detail", kwargs={"pk": self.pk})

    def clean(self) -> None:
        super().clean()
        errors = {}

        if self.start_date and self.end_date and self.end_date < self.start_date:
            errors["end_date"] = ValidationError("End date must be after start date.")

        if self.business_id:
            if self.job_id and self.job.business_id != self.business_id:
                errors["job"] = ValidationError("Job does not belong to this business.")
            if self.client_id and self.client.business_id != self.business_id:
                errors["client"] = ValidationError("Client does not belong to this business.")

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.job_id and not self.event_name:
            self.event_name = getattr(self.job, "label", None) or str(self.job)
        if self.job_id and not self.client_id and getattr(self.job, "client_id", None):
            self.client = self.job.client
        self.full_clean()
        return super().save(*args, **kwargs)

    def generate_approval_token(self) -> str:
        token = uuid.uuid4().hex + uuid.uuid4().hex
        self.approval_token = token
        return token

    def compute_attestation_hash(self) -> str:
        parts = [
            (self.approved_name or "").strip(),
            self.approved_notes_snapshot or "",
            self.approved_at.isoformat() if self.approved_at else "",
            str(self.pk or ""),
        ]
        self.attestation_hash = hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()
        return self.attestation_hash

    @property
    def is_approved(self) -> bool:
        return bool(self.approved_at and self.status == self.APPROVED)
