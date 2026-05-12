from __future__ import annotations

import os
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from core.models import BusinessOwnedModelMixin


def asset_receipt_upload_to(instance: "Asset", filename: str) -> str:
    """
    Upload path for asset receipts.

    Example:
      assets/<business_id>/<asset_uuid>/<filename>
    """
    base, ext = os.path.splitext(filename)
    ext = ext.lower()[:10]
    safe_name = f"{base[:80]}{ext}"
    return f"assets/{instance.business_id}/{instance.uid}/{safe_name}"


class AssetType(BusinessOwnedModelMixin):
    """Business-owned asset type list, such as Drone, Controller, Computer, etc."""

    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["business", "slug"], name="uniq_asset_type_business_slug"),
        ]
        indexes = [
            models.Index(fields=["business", "is_active"], name="assets_asse_busines_fb2138_idx"),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or "asset-type"
            slug = base
            i = 2
            qs = AssetType.objects.filter(business=self.business, slug=slug)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            while qs.exists():
                slug = f"{base}-{i}"
                i += 1
                qs = AssetType.objects.filter(business=self.business, slug=slug)
                if self.pk:
                    qs = qs.exclude(pk=self.pk)
            self.slug = slug
        return super().save(*args, **kwargs)


class Asset(BusinessOwnedModelMixin):
    class DepreciationMethod(models.TextChoices):
        MACRS_5 = "macrs_5", "MACRS 5-Year"
        MACRS_7 = "macrs_7", "MACRS 7-Year"
        STRAIGHT_LINE = "straight_line", "Straight Line"
        SECTION_179 = "section_179", "Section 179"

    uid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    name = models.CharField(max_length=255)
    asset_type = models.ForeignKey(
        AssetType,
        on_delete=models.PROTECT,
        related_name="assets",
    )
    is_active = models.BooleanField(default=True)

    purchase_date = models.DateField()
    placed_in_service_date = models.DateField(blank=True, null=True)

    purchase_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    useful_life_years = models.PositiveSmallIntegerField(default=5)
    depreciation_method = models.CharField(
        max_length=50,
        choices=DepreciationMethod.choices,
        default=DepreciationMethod.MACRS_5,
    )
    section_179_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="If using Section 179, enter the amount elected for immediate deduction.",
    )

    receipt = models.FileField(upload_to=asset_receipt_upload_to, blank=True, null=True)

    disposed_date = models.DateField(blank=True, null=True)
    disposal_price = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-purchase_date", "name"]
        indexes = [
            models.Index(fields=["business", "asset_type"], name="assets_asse_busines_624ed1_idx"),
            models.Index(fields=["business", "is_active"], name="assets_asse_busines_620d2e_idx"),
            models.Index(fields=["business", "purchase_date"], name="assets_asse_busines_067ce9_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name}"

    @property
    def in_service(self) -> bool:
        return bool(self.placed_in_service_date or self.purchase_date)

    @property
    def basis(self) -> Decimal:
        return self.purchase_price or Decimal("0.00")

    def clean(self):
        # Default placed_in_service_date to purchase_date if not provided.
        if not self.placed_in_service_date and self.purchase_date:
            self.placed_in_service_date = self.purchase_date

        if self.asset_type_id and self.business_id and self.asset_type.business_id != self.business_id:
            raise ValidationError({"asset_type": "Select an asset type for this business."})
