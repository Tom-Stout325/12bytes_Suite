from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


def normalize_catalog_name(value: str | None) -> str:
    """Normalize harmless formatting while preserving product distinctions."""
    return slugify(" ".join((value or "").strip().casefold().split()))


class BatteryFamily(models.Model):
    manufacturer = models.CharField(max_length=100)
    normalized_manufacturer = models.SlugField(max_length=120, editable=False)
    name = models.CharField(max_length=150)
    normalized_name = models.SlugField(max_length=180, editable=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["manufacturer", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["normalized_manufacturer", "normalized_name"],
                name="uniq_drone_battery_family_name",
            ),
        ]
        verbose_name_plural = "Battery families"

    def __str__(self) -> str:
        return " ".join(part for part in (self.manufacturer, self.name) if part)

    def save(self, *args, **kwargs):
        self.manufacturer = " ".join(self.manufacturer.strip().split())
        self.name = " ".join(self.name.strip().split())
        self.normalized_manufacturer = normalize_catalog_name(self.manufacturer)
        self.normalized_name = normalize_catalog_name(self.name)
        return super().save(*args, **kwargs)


class DroneModel(models.Model):
    manufacturer = models.CharField(max_length=100)
    normalized_manufacturer = models.SlugField(max_length=120, editable=False)
    name = models.CharField(max_length=150)
    normalized_name = models.SlugField(max_length=180, editable=False)
    full_display_name = models.CharField(max_length=255, editable=False)
    year_released = models.PositiveSmallIntegerField(null=True, blank=True)
    is_enterprise = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    battery_family = models.ForeignKey(
        BatteryFamily,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="drone_models",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["manufacturer", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["normalized_manufacturer", "normalized_name"],
                name="uniq_drone_model_manufacturer_name",
            ),
        ]

    def __str__(self) -> str:
        return self.full_display_name

    def save(self, *args, **kwargs):
        self.manufacturer = " ".join(self.manufacturer.strip().split())
        self.name = " ".join(self.name.strip().split())
        self.normalized_manufacturer = normalize_catalog_name(self.manufacturer)
        self.normalized_name = normalize_catalog_name(self.name)
        self.full_display_name = f"{self.manufacturer} {self.name}".strip()
        return super().save(*args, **kwargs)


class DroneModelAlias(models.Model):
    drone_model = models.ForeignKey(DroneModel, on_delete=models.CASCADE, related_name="aliases")
    alias = models.CharField(max_length=255)
    normalized_alias = models.SlugField(max_length=255, editable=False, unique=True)
    source = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["alias"]
        verbose_name_plural = "Drone model aliases"

    def __str__(self) -> str:
        return self.alias

    def save(self, *args, **kwargs):
        self.alias = " ".join(self.alias.strip().split())
        self.normalized_alias = normalize_catalog_name(self.alias)
        if not self.normalized_alias:
            raise ValidationError({"alias": "Enter a usable alias."})
        return super().save(*args, **kwargs)


class DroneModelIdentifier(models.Model):
    drone_model = models.ForeignKey(DroneModel, on_delete=models.CASCADE, related_name="identifiers")
    provider = models.CharField(max_length=50)
    normalized_provider = models.SlugField(max_length=60, editable=False)
    identifier = models.CharField(max_length=255)
    normalized_identifier = models.CharField(max_length=255, editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["provider", "identifier"]
        constraints = [
            models.UniqueConstraint(
                fields=["normalized_provider", "normalized_identifier"],
                name="uniq_drone_provider_identifier",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider}: {self.identifier}"

    def save(self, *args, **kwargs):
        self.provider = " ".join(self.provider.strip().split())
        self.identifier = " ".join(self.identifier.strip().split())
        self.normalized_provider = normalize_catalog_name(self.provider)
        self.normalized_identifier = self.identifier.casefold()
        if not self.provider or not self.normalized_identifier:
            raise ValidationError("Provider and identifier are required.")
        return super().save(*args, **kwargs)


class DroneSafetyProfile(models.Model):
    drone_model = models.OneToOneField(
        DroneModel,
        on_delete=models.CASCADE,
        related_name="safety_profile",
    )
    safety_features = models.TextField()
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["drone_model__manufacturer", "drone_model__name"]

    def __str__(self) -> str:
        return f"Safety profile: {self.drone_model}"
