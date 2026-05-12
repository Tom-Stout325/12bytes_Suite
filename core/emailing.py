from __future__ import annotations

from email.utils import formataddr
from typing import Iterable

from django.conf import settings

from core.models import Business, BusinessEmailSettings, get_or_create_business_email_settings


PREVIEW_BACKENDS = {
    "django.core.mail.backends.console.emailbackend",
    "django.core.mail.backends.filebased.emailbackend",
    "django.core.mail.backends.locmem.emailbackend",
    "django.core.mail.backends.dummy.emailbackend",
}


def uses_preview_backend() -> bool:
    backend = str(getattr(settings, "EMAIL_BACKEND", "") or "").strip().lower()
    return backend in PREVIEW_BACKENDS


def platform_from_email() -> str:
    return (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()


def platform_reply_to_email() -> str:
    return (
        getattr(settings, "REPLY_TO_EMAIL", "")
        or getattr(settings, "DEFAULT_FROM_EMAIL", "")
        or ""
    ).strip()


def app_display_name() -> str:
    return (getattr(settings, "APP_NAME", "MoneyPro") or "MoneyPro").strip()


def formatted_from_header(*, display_name: str | None = None, email: str | None = None) -> str:
    final_email = (email or platform_from_email()).strip()
    final_name = (display_name or app_display_name()).strip()
    return formataddr((final_name, final_email)) if final_name else final_email


def get_business_email_settings(*, business: Business, owner_user=None) -> BusinessEmailSettings:
    return get_or_create_business_email_settings(business=business, owner_user=owner_user)


def business_from_email(*, business: Business, owner_user=None) -> tuple[str, str, str]:
    email_settings = get_business_email_settings(business=business, owner_user=owner_user)
    from_name = (
        email_settings.from_name
        or email_settings.display_name
        or business.name
        or app_display_name()
    ).strip()
    from_email = (email_settings.from_email or platform_from_email()).strip()
    reply_to = (email_settings.reply_to_email or platform_reply_to_email()).strip()
    return from_name, from_email, reply_to


def normalize_reply_to(*addresses: str | None) -> list[str] | None:
    cleaned = [addr.strip() for addr in addresses if addr and addr.strip()]
    return cleaned or None
