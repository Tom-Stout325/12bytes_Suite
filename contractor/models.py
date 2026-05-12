from __future__ import annotations

from django.db import models
from django.utils import timezone

from core.models import BusinessOwnedModelMixin
from ledger.models import Contact


class ContractorW9Submission(BusinessOwnedModelMixin):
    """Audit record of a W-9 submission via the public portal.

    Security note:
    - We intentionally do NOT store full TIN long-term. We store last4 only.
    """

    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "Pending Review"
        VERIFIED = "verified", "Verified"
        NEEDS_UPDATE = "needs_update", "Needs Update"

    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="w9_submissions")

    # Audit timestamps (migration 0001 already includes these fields)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Core W-9 fields
    full_name = models.CharField(max_length=255)
    business_name = models.CharField(max_length=255, blank=True)
    entity_type = models.CharField(max_length=25, blank=True)
    tin_type = models.CharField(max_length=10, blank=True)
    tin_last4 = models.CharField(max_length=4, blank=True)

    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=50, blank=True)
    zip_code = models.CharField(max_length=20, blank=True)

    signature_name = models.CharField(max_length=255, blank=True)
    signature_data = models.TextField(blank=True)  # base64 png from signature pad (optional)
    signature_date = models.DateField(null=True, blank=True)
    certification_accepted = models.BooleanField(default=False)
    uploaded_w9_document = models.FileField(upload_to="w9/submissions/", blank=True, null=True)

    submitted_ip = models.GenericIPAddressField(null=True, blank=True)
    submitted_ua = models.CharField(max_length=255, blank=True)

    submitted_at = models.DateTimeField(default=timezone.now)

    review_status = models.CharField(
        max_length=20,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by_name = models.CharField(max_length=255, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        indexes = [
            models.Index(fields=["business", "contact"], name="ctr_w9_bus_contact_idx"),
            models.Index(fields=["business", "review_status"], name="ctr_w9_bus_review_idx"),
        ]

    def __str__(self) -> str:
        return f"W-9 submission for {self.contact} on {self.submitted_at:%Y-%m-%d}"


def contractor_1099_upload_path(instance: "Contractor1099", filename: str) -> str:
    return f"tax/1099/{instance.business_id}/{instance.tax_year}/contact_{instance.contact_id}/{filename}"


class Contractor1099(BusinessOwnedModelMixin):
    """Stored 1099-NEC PDFs per contact/year (Copy B + Copy 1) with email audit."""

    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="forms_1099")
    tax_year = models.PositiveIntegerField(db_index=True)

    copy_b_pdf = models.FileField(upload_to=contractor_1099_upload_path, blank=True, null=True)
    copy_1_pdf = models.FileField(upload_to=contractor_1099_upload_path, blank=True, null=True)
    generated_at = models.DateTimeField(default=timezone.now)

    # Email/audit for Copy B only
    emailed_at = models.DateTimeField(blank=True, null=True)
    emailed_to = models.EmailField(blank=True)
    email_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-tax_year", "-generated_at", "-id"]
        indexes = [
            models.Index(fields=["business", "tax_year"], name="ctr_1099_bus_year_idx"),
            models.Index(fields=["business", "contact", "tax_year"], name="ctr_1099_bus_contact_year_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["business", "contact", "tax_year"], name="uniq_1099_per_contact_year"),
        ]

    def __str__(self) -> str:
        return f"1099 {self.tax_year} — {self.contact}"
