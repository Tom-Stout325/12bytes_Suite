from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.db.models import Q
from .business_features import BusinessFeature


class Business(models.Model):
    """Tenant model (a company/business that owns data)."""

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or "business"
            slug = base
            i = 2
            while Business.objects.filter(slug=slug).exists():
                slug = f"{base}-{i}"
                i += 1
            self.slug = slug
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name
    
    def has_feature(self, code):
        return self.features.filter(code=code).exists()
    
    


class BusinessMembership(models.Model):
    """User-to-business association with a role."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"
        VIEWER = "viewer", "Viewer"

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="business_memberships",
    )
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(is_active=True),
                name="uniq_user_single_active_business_membership",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} @ {self.business} ({self.role})"




class BusinessOwnedModelMixin(models.Model):
    """Tenant ownership mixin (Phase 1): data owned by a business."""

    business = models.ForeignKey(Business, on_delete=models.CASCADE)

    class Meta:
        abstract = True


class BusinessEmailSettings(models.Model):
    class SendMode(models.TextChoices):
        PLATFORM_DEFAULT = "platform_default", "MoneyPro default"
        CUSTOM_DOMAIN = "custom_domain", "Custom domain"
        DISABLED = "disabled", "Disabled"

    class DomainStatus(models.TextChoices):
        NOT_CONFIGURED = "not_configured", "Not configured"
        PENDING = "pending", "Pending verification"
        VERIFIED = "verified", "Verified"
        FAILED = "failed", "Verification failed"

    business = models.OneToOneField(Business, on_delete=models.CASCADE, related_name="email_settings")
    display_name = models.CharField(max_length=120, blank=True)
    from_name = models.CharField(max_length=120, blank=True)
    from_email = models.EmailField(blank=True)
    reply_to_email = models.EmailField(blank=True)
    invoice_cc_email = models.EmailField(blank=True)
    payment_questions_email = models.EmailField(blank=True)
    email_signature = models.TextField(blank=True)
    send_mode = models.CharField(max_length=20, choices=SendMode.choices, default=SendMode.PLATFORM_DEFAULT)
    is_active = models.BooleanField(default=True)
    verified_for_sending = models.BooleanField(default=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)

    custom_domain = models.CharField(max_length=120, blank=True)
    custom_domain_status = models.CharField(
        max_length=20,
        choices=DomainStatus.choices,
        default=DomainStatus.NOT_CONFIGURED,
    )
    custom_return_path_domain = models.CharField(max_length=120, blank=True)
    dkim_verified = models.BooleanField(default=False)
    spf_verified = models.BooleanField(default=False)
    tracking_domain_verified = models.BooleanField(default=False)
    sendgrid_domain_id = models.CharField(max_length=60, blank=True)
    verification_notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Business email settings"
        verbose_name_plural = "Business email settings"

    def __str__(self) -> str:
        return f"Email settings for {self.business}"

    @property
    def sending_ready(self) -> bool:
        if not self.is_active or self.send_mode == self.SendMode.DISABLED:
            return False
        if not self.verified_for_sending:
            return False
        if self.send_mode == self.SendMode.CUSTOM_DOMAIN:
            return self.custom_domain_status == self.DomainStatus.VERIFIED
        return True

    @property
    def status_label(self) -> str:
        if not self.is_active or self.send_mode == self.SendMode.DISABLED:
            return "Disabled"
        if not self.verified_for_sending:
            return "Needs verification"
        if self.send_mode == self.SendMode.CUSTOM_DOMAIN and self.custom_domain_status != self.DomainStatus.VERIFIED:
            return "Custom domain pending"
        return "Ready"

    def clean(self):
        super().clean()
        if self.send_mode != self.SendMode.DISABLED and not (self.reply_to_email or "").strip():
            raise ValidationError({"reply_to_email": "Reply-to email is required unless sending is disabled."})

        if self.send_mode == self.SendMode.PLATFORM_DEFAULT:
            from_email = (self.from_email or "").strip().lower()
            if from_email and "@" in from_email:
                domain = from_email.split("@", 1)[1]
                allowed = (getattr(settings, "BUSINESS_EMAIL_PLATFORM_DOMAIN", "") or "").strip().lower()
                if allowed and domain != allowed:
                    raise ValidationError({"from_email": f"From email must use the platform domain ({allowed})."})

    def save(self, *args, **kwargs):
        if self.verified_for_sending and self.last_verified_at is None:
            self.last_verified_at = timezone.now()
        self.full_clean()
        return super().save(*args, **kwargs)


class OutgoingEmailLog(models.Model):
    class TemplateType(models.TextChoices):
        INVOICE = "invoice", "Invoice"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="outgoing_email_logs")
    invoice = models.ForeignKey("invoices.Invoice", null=True, blank=True, on_delete=models.SET_NULL, related_name="email_logs")
    template_type = models.CharField(max_length=20, choices=TemplateType.choices, default=TemplateType.INVOICE)
    recipient_email = models.EmailField()
    cc_email = models.EmailField(blank=True)
    subject = models.CharField(max_length=255)
    from_email = models.EmailField(blank=True)
    reply_to_email = models.EmailField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    provider_message_id = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="sent_email_logs")
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.template_type} -> {self.recipient_email} ({self.status})"


def get_or_create_business_email_settings(*, business: Business, owner_user=None):
    settings_obj, created = BusinessEmailSettings.objects.get_or_create(
        business=business,
        defaults={
            "display_name": business.name,
            "from_name": business.name,
            "from_email": build_platform_from_email(business=business),
            "reply_to_email": default_reply_to_email(business=business, owner_user=owner_user),
            "payment_questions_email": default_reply_to_email(business=business, owner_user=owner_user),
            "send_mode": BusinessEmailSettings.SendMode.PLATFORM_DEFAULT,
            "is_active": True,
            "verified_for_sending": True,
        },
    )
    needs_save = False
    if not settings_obj.display_name:
        settings_obj.display_name = business.name
        needs_save = True
    if not settings_obj.from_name:
        settings_obj.from_name = settings_obj.display_name or business.name
        needs_save = True
    if not settings_obj.from_email:
        settings_obj.from_email = build_platform_from_email(business=business)
        needs_save = True
    default_reply = default_reply_to_email(business=business, owner_user=owner_user)
    if default_reply and not settings_obj.reply_to_email:
        settings_obj.reply_to_email = default_reply
        needs_save = True
    if default_reply and not settings_obj.payment_questions_email:
        settings_obj.payment_questions_email = default_reply
        needs_save = True
    if needs_save:
        settings_obj.save()
    return settings_obj


def build_platform_from_email(*, business: Business) -> str:
    local_part = (getattr(settings, "BUSINESS_EMAIL_LOCALPART", "invoices") or "invoices").strip() or "invoices"
    domain = (getattr(settings, "BUSINESS_EMAIL_PLATFORM_DOMAIN", "") or "").strip()
    if not domain:
        return getattr(settings, "DEFAULT_FROM_EMAIL", "") or ""
    return f"{local_part}@{domain}"


def default_reply_to_email(*, business: Business, owner_user=None) -> str:
    billing_email = ""
    company_profile = getattr(business, "company_profile", None)
    if company_profile is not None:
        billing_email = (getattr(company_profile, "billing_email", "") or "").strip().lower()
        if billing_email:
            return billing_email
    if owner_user is not None:
        user_email = (getattr(owner_user, "email", "") or "").strip().lower()
        if user_email:
            return user_email
    return (getattr(settings, "REPLY_TO_EMAIL", "") or getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip().lower()


class BackupLog(models.Model):
    """History of generated MoneyPro business backups."""

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        DELETED = "deleted", "Deleted"

    class BackupType(models.TextChoices):
        XLSX = "xlsx", "Excel workbook"
        JSON = "json", "JSON package"

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="backup_logs")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_backup_logs",
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RUNNING)
    backup_type = models.CharField(max_length=10, choices=BackupType.choices, default=BackupType.XLSX)
    storage_key = models.CharField(max_length=500, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    table_count = models.PositiveIntegerField(default=0)
    row_count = models.PositiveIntegerField(default=0)
    retention_days = models.PositiveIntegerField(default=7)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Backup log"
        verbose_name_plural = "Backup logs"

    def __str__(self) -> str:
        return f"{self.business} backup {self.created_at:%Y-%m-%d %H:%M} ({self.status})"

    @property
    def filename(self) -> str:
        return self.storage_key.rsplit("/", 1)[-1] if self.storage_key else ""

    @property
    def size_mb(self) -> float:
        return round((self.size_bytes or 0) / (1024 * 1024), 2)
