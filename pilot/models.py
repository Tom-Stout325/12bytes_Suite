from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.timezone import now

from core.models import BusinessOwnedModelMixin
from flightlogs.models import FlightLog


def _safe_slug_part(value: str | None, fallback: str = "unknown") -> str:
    value = (value or "").strip().replace("/", "-")
    return value or fallback


def license_upload_path(instance: "PilotProfile", filename: str) -> str:
    business_id = instance.business_id or "business"
    username = _safe_slug_part(instance.user.username if instance.user_id else None)
    return f"pilot_licenses/business-{business_id}/{username}/{filename}"


def training_certificate_upload_path(instance: "Training", filename: str) -> str:
    business_id = instance.business_id or "business"
    username = _safe_slug_part(instance.pilot.user.username if instance.pilot_id and instance.pilot.user_id else None)
    return f"training_certificates/business-{business_id}/{username}/{filename}"


class PilotProfile(BusinessOwnedModelMixin):
    """Pilot credentials for a Suite user within a business tenant."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pilot_profile",
    )
    license_number = models.CharField(max_length=100, blank=True, null=True)
    license_date = models.DateField(blank=True, null=True)
    license_image = models.ImageField(upload_to=license_upload_path, blank=True, null=True)

    class Meta:
        db_table = "app_pilotprofile"
        ordering = ["user__last_name", "user__first_name", "user__username"]
        indexes = [
            models.Index(fields=["business", "user"]),
        ]

    @property
    def pilot_name(self) -> str:
        return (self.user.get_full_name() or self.user.get_username()).strip()

    def _flightlogs(self):
        full_name = self.user.get_full_name().strip()
        qs = FlightLog.objects.filter(business=self.business)
        if full_name:
            return qs.filter(pilot_in_command__iexact=full_name)
        return qs.filter(pilot_in_command__iexact=self.user.get_username())

    def flights_this_year(self):
        return self._flightlogs().filter(flight_date__year=now().year).count()

    def flights_total(self):
        return self._flightlogs().count()

    def flight_time_this_year(self):
        logs = self._flightlogs().filter(flight_date__year=now().year).values_list("air_time", flat=True)
        return sum((t.total_seconds() for t in logs if t), 0)

    def flight_time_total(self):
        logs = self._flightlogs().values_list("air_time", flat=True)
        return sum((t.total_seconds() for t in logs if t), 0)

    def clean(self):
        super().clean()
        if self.user_id and self.business_id:
            membership_exists = self.user.business_memberships.filter(
                business_id=self.business_id,
                is_active=True,
            ).exists()
            if not membership_exists:
                raise ValidationError({"business": "Pilot user must belong to this business."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.pilot_name


class Training(BusinessOwnedModelMixin):
    """Business-owned pilot training record."""

    pilot = models.ForeignKey(
        PilotProfile,
        on_delete=models.CASCADE,
        related_name="trainings",
    )
    title = models.CharField(max_length=200)
    date_completed = models.DateField()
    required = models.BooleanField(default=False)
    certificate = models.FileField(upload_to=training_certificate_upload_path, blank=True, null=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "app_training"
        ordering = ["-date_completed", "-id"]
        indexes = [
            models.Index(fields=["business", "date_completed"]),
            models.Index(fields=["business", "pilot"]),
        ]

    def clean(self):
        super().clean()
        if self.pilot_id:
            if not self.business_id and self.pilot.business_id:
                self.business_id = self.pilot.business_id
            elif self.business_id and self.pilot.business_id != self.business_id:
                raise ValidationError({"pilot": "Training pilot must belong to the same business."})

    def save(self, *args, **kwargs):
        if self.pilot_id and not self.business_id:
            self.business_id = self.pilot.business_id
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.title} ({self.date_completed:%m/%d/%Y})"
