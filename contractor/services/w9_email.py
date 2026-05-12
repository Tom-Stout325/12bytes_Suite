from __future__ import annotations

import mimetypes
from pathlib import Path

from django.contrib.staticfiles import finders
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from core.emailing import business_from_email, formatted_from_header, normalize_reply_to, uses_preview_backend


def _w9_attachment_path() -> Path | None:
    located = finders.find("images/W-9_form.pdf")
    if not located:
        return None
    if isinstance(located, (list, tuple)):
        located = located[0] if located else None
    return Path(located) if located else None


def send_w9_request_email(*, business, contractor_name: str, contractor_email: str, portal_url: str, owner_user=None) -> tuple[bool, str]:
    from_name, from_email, reply_to = business_from_email(business=business, owner_user=owner_user)

    attachment_path = _w9_attachment_path()
    has_pdf_attachment = bool(attachment_path and attachment_path.exists())

    context = {
        "business_name": business.name,
        "contractor_name": contractor_name,
        "portal_url": portal_url,
        "support_email": reply_to or from_email,
        "has_pdf_attachment": has_pdf_attachment,
    }
    subject = render_to_string("contractor/emails/w9_request_subject.txt", context).strip().replace("\n", " ")
    text_body = render_to_string("contractor/emails/w9_request_email.txt", context)
    html_body = render_to_string("contractor/emails/w9_request_email.html", context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=formatted_from_header(display_name=from_name, email=from_email),
        to=[contractor_email],
        reply_to=normalize_reply_to(reply_to),
    )
    message.attach_alternative(html_body, "text/html")

    if has_pdf_attachment:
        mime_type = mimetypes.guess_type(str(attachment_path))[0] or "application/pdf"
        message.attach(attachment_path.name, attachment_path.read_bytes(), mime_type)

    message.send(fail_silently=False)

    if uses_preview_backend():
        if has_pdf_attachment:
            return False, "Email preview generated using the current placeholder/local email backend. W-9 PDF attachment was included."
        return False, "Email preview generated using the current placeholder/local email backend."
    if has_pdf_attachment:
        return True, "W-9 request email sent with secure portal link and attached W-9 PDF."
    return True, "W-9 request email sent with secure portal link."
