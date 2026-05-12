from __future__ import annotations

from email.utils import formataddr
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

from accounts.models import Invitation


def _get_setting(name: str, default: Any = None) -> Any:
    return getattr(settings, name, default)


def _required_setting(name: str) -> Any:
    value = _get_setting(name)
    if value in (None, ""):
        raise ImproperlyConfigured(f"Missing required setting: {name}")
    return value


def _clean_subject(value: str) -> str:
    return " ".join((value or "").splitlines()).strip()


def _build_invite_url(invitation: Invitation, request_obj=None) -> str:
    path = reverse("accounts:invite_start", args=[invitation.token])

    if request_obj is not None:
        return request_obj.build_absolute_uri(path)

    site_url = (
        _get_setting("SITE_URL")
        or _get_setting("APP_BASE_URL")
        or ""
    ).strip().rstrip("/")

    if not site_url:
        raise ImproperlyConfigured(
            "SITE_URL or APP_BASE_URL is required when sending invitation email without an HTTP request."
        )

    return f"{site_url}{path}"


def _email_context(*, invitation: Invitation, request_obj=None) -> tuple[dict[str, Any], str, str, str, str, str]:
    from_email = _required_setting("DEFAULT_FROM_EMAIL")
    reply_to = (_get_setting("REPLY_TO_EMAIL", "") or "").strip()
    app_name = (_get_setting("APP_NAME", "MoneyPro") or "MoneyPro").strip()
    invite_url = _build_invite_url(invitation, request_obj=request_obj)

    context = {
        "invitation": invitation,
        "invite_url": invite_url,
        "app_name": app_name,
        "expires_at": invitation.expires_at,
        "reply_to_email": reply_to,
        "support_email": reply_to or from_email,
    }

    subject = _clean_subject(
        render_to_string("accounts/emails/invitation_subject.txt", context)
    )
    text_body = render_to_string("accounts/emails/invitation_email.txt", context)
    html_body = render_to_string("accounts/emails/invitation_email.html", context)

    return context, from_email, reply_to, app_name, subject, text_body, html_body


def send_invitation_email(*, invitation: Invitation, request_obj=None) -> None:
    """
    Send invitation email through Django's configured email backend.

    This works for:
    - local development using console/file/locmem backends
    - production using SMTP (SendGrid)

    Reply-To uses REPLY_TO_EMAIL when configured.
    """
    _context, from_email, reply_to, app_name, subject, text_body, html_body = _email_context(
        invitation=invitation,
        request_obj=request_obj,
    )

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=formataddr((app_name, from_email)),
        to=[invitation.email],
        reply_to=[reply_to] if reply_to else None,
    )
    message.attach_alternative(html_body, "text/html")
    message.send(fail_silently=False)