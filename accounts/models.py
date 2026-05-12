from __future__ import annotations

from datetime import timedelta
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone
from core.models import Business


class CompanyProfile(models.Model):
    business      = models.OneToOneField(Business, on_delete=models.CASCADE, related_name="company_profile")
    created_by    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_company_profiles",)
    company_name  = models.CharField(max_length=120)
    legal_name    = models.CharField(max_length=120, blank=True)
    ein           = models.CharField(max_length=15, blank=True)
    phone         = models.CharField(max_length=30, blank=True)
    billing_email = models.EmailField(blank=True)
    website       = models.CharField(max_length=120, blank=True)
    address_line1 = models.CharField(max_length=120, blank=True)
    address_line2 = models.CharField(max_length=120, blank=True)
    city          = models.CharField(max_length=80, blank=True)
    state         = models.CharField(max_length=50, blank=True)
    postal_code   = models.CharField(max_length=20, blank=True)
    country       = models.CharField(max_length=50, default="US")
    logo          = models.ImageField(upload_to="company_logos/", blank=True, null=True)
    timezone      = models.CharField(max_length=64, default="America/Indiana/Indianapolis")
    currency      = models.CharField(max_length=10, default="USD")
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.company_name or f"CompanyProfile for {self.business}"

    @property
    def phone_display(self):
        p = (self.phone or "").strip()
        if p.isdigit() and len(p) == 10:
            return f"({p[:3]}) {p[3:6]}-{p[6:]}"
        return p

    @property
    def is_complete(self) -> bool:
        # Minimal completion rule for now; expand later as needed.
        return bool((self.company_name or "").strip())


class Invitation(models.Model):
    """Invite-only signup token.

    Admin creates an Invitation for an email, sends the invite link.
    The user can register only via that link.
    """

    email           = models.EmailField()
    token           = models.CharField(max_length=64, unique=True, editable=False)
    invited_by      = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sent_invitations",
    )
    created_at      = models.DateTimeField(auto_now_add=True)
    expires_at      = models.DateTimeField()
    accepted_at     = models.DateTimeField(null=True, blank=True)
    accepted_user   = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accepted_invitations",
    )

    class Meta:
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["token"]),
        ]

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(days=7)

        if not self.token:
            # token_urlsafe(48) is typically ~64 chars; slice to guarantee <=64.
            for _ in range(5):
                self.token = secrets.token_urlsafe(48)[:64]
                try:
                    return super().save(*args, **kwargs)
                except Exception:
                    # If this was a uniqueness collision, try again; otherwise re-raise.
                    self.token = ""
            raise ValueError("Could not generate a unique invitation token.")

        return super().save(*args, **kwargs)

    @property
    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at
    is_expired.fget.short_description = "Expired"
    is_expired.fget.boolean = True

    @property
    def is_used(self) -> bool:
        return self.accepted_at is not None
    is_used.fget.short_description = "Used"
    is_used.fget.boolean = True

    def __str__(self) -> str:
        return f"Invite {self.email}"
