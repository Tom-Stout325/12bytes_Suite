from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMultiAlternatives
from django.db import models, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from weasyprint import HTML

from core.models import OutgoingEmailLog, get_or_create_business_email_settings
from ledger.models import Transaction

from .models import (
    Invoice,
    InvoiceCounter,
    InvoiceItem,
    allocate_next_invoice_number,
    bump_counter_if_needed,
    next_revision_suffix,
    _max_existing_invoice_seq,
    _max_invoiceable_job_seq,
)


def get_next_invoice_number_preview(*, business, issue_date=None) -> str:
    """Return the next numeric invoice number (YY####) WITHOUT reserving it.

    This is for UI display only (placeholder/help text). Draft-save reservation
    happens via `allocate_next_invoice_number()`.
    """
    issue_date = issue_date or timezone.localdate()
    year = issue_date.year

    counter = InvoiceCounter.objects.filter(business=business, year=year).only("last_seq").first()
    last_seq = counter.last_seq if counter else 0
    last_seq = max(
        int(last_seq or 0),
        _max_existing_invoice_seq(business=business, year=year),
        _max_invoiceable_job_seq(business=business, year=year),
    )

    return f"{issue_date.year % 100:02d}{last_seq + 1:04d}"


def recalc_totals(*, invoice: Invoice, save: bool = False) -> tuple[Decimal, Decimal]:
    """Option A totals: compute live totals from items.

    We still set invoice.subtotal/total in-memory for templates/PDF, but callers
    can choose to persist these cached fields by passing save=True.
    """
    subtotal = invoice.subtotal_amount
    total = invoice.total_amount

    invoice.subtotal = subtotal
    invoice.total = total

    if save:
        invoice.save(update_fields=["subtotal", "total", "updated_at"])

    return subtotal, total


def snapshot_bill_to(*, invoice: Invoice) -> None:
    p = invoice.contact
    invoice.bill_to_name = getattr(p, "display_name", "") or ""
    invoice.bill_to_email = getattr(p, "email", "") or ""
    invoice.bill_to_address1 = getattr(p, "address1", "") or ""
    invoice.bill_to_address2 = getattr(p, "address2", "") or ""
    invoice.bill_to_city = getattr(p, "city", "") or ""
    invoice.bill_to_state = getattr(p, "state", "") or ""
    invoice.bill_to_postal_code = getattr(p, "zip_code", "") or ""
    invoice.bill_to_country = getattr(p, "country", "") or "US"


def ensure_number(*, invoice: Invoice) -> None:
    """Ensure invoice has a numeric YY#### number (or existing revision number).

    If the invoice already has a number, we bump the business/year counter if needed.
    If missing, allocate the next number.
    """
    if invoice.invoice_number:
        bump_counter_if_needed(
            business=invoice.business,
            issue_date=invoice.issue_date,
            invoice_number=invoice.invoice_number,
        )
        return

    invoice.invoice_number = allocate_next_invoice_number(
        business=invoice.business,
        issue_date=invoice.issue_date,
        job=invoice.job,
    )
    invoice.save(update_fields=["invoice_number"])


def render_invoice_pdf_bytes(*, invoice: Invoice, base_url: str | None = None) -> bytes:
    """Render an invoice PDF from HTML using WeasyPrint.

    `base_url` is critical so WeasyPrint can resolve relative URLs for static/media.
    - For requests, pass `request.build_absolute_uri('/')`
    - For offline rendering, we fall back to BASE_DIR
    """
    company = getattr(invoice.business, "company_profile", None)

    # Ensure totals are current for rendering (Option A)
    recalc_totals(invoice=invoice, save=False)

    html = render_to_string(
        "invoices/pdf/invoice_final.html",
        {
            "invoice": invoice,
            "business": invoice.business,
            "company": company,
        },
    )

    resolved_base_url = base_url or str(settings.BASE_DIR)
    return HTML(string=html, base_url=resolved_base_url).write_pdf()


def send_invoice(*, invoice: Invoice, base_url: str | None = None, sent_by=None) -> None:
    if invoice.status != Invoice.Status.DRAFT:
        raise ValueError("Only draft invoices can be sent.")

    ensure_number(invoice=invoice)
    recalc_totals(invoice=invoice, save=False)
    snapshot_bill_to(invoice=invoice)

    invoice.sent_date = timezone.localdate()
    pdf_bytes = render_invoice_pdf_bytes(invoice=invoice, base_url=base_url)
    invoice.pdf_file.save(f"{invoice.invoice_number}.pdf", ContentFile(pdf_bytes), save=False)

    _send_invoice_email(invoice=invoice, pdf_bytes=pdf_bytes, base_url=base_url, sent_by=sent_by)

    invoice.status = Invoice.Status.SENT
    invoice.save()


@transaction.atomic
def create_revision(*, invoice: Invoice) -> Invoice:
    if invoice.status != Invoice.Status.SENT:
        raise ValueError("Only sent invoices can be revised.")
    if not invoice.invoice_number:
        raise ValueError("Invoice must have a number to revise.")

    base = invoice.invoice_number[:6]
    suffix = next_revision_suffix(business=invoice.business, base_number=base)
    new_number = f"{base}{suffix}"

    rev = Invoice.objects.create(
        business=invoice.business,
        status=Invoice.Status.DRAFT,
        issue_date=timezone.localdate(),
        due_date=invoice.due_date,
        contact=invoice.contact,
        job=invoice.job,
        team=invoice.team,
        location=invoice.location,
        invoice_number=new_number,
        revises=invoice,
        memo=invoice.memo,
    )

    for it in invoice.items.all():
        InvoiceItem.objects.create(
            business=invoice.business,
            invoice=rev,
            description=it.description,
            qty=it.qty,
            unit_price=it.unit_price,
            subcategory=it.subcategory,
            sort_order=it.sort_order,
        )

    # NOTE: alpha revisions do NOT update numeric counter.
    recalc_totals(invoice=rev, save=False)
    return rev


@transaction.atomic
def mark_paid(*, invoice: Invoice, paid_date=None) -> Transaction:
    if invoice.status != Invoice.Status.SENT:
        raise ValueError("Only sent invoices can be marked paid.")
    if invoice.income_transaction_id:
        raise ValueError("This invoice is already posted to the ledger.")

    paid_date = paid_date or timezone.localdate()

    # Ensure totals current (Option A)
    _, total = recalc_totals(invoice=invoice, save=False)

    # choose posting subcategory: first line item with subcategory required
    first = invoice.items.exclude(subcategory__isnull=True).select_related("subcategory").first()
    if not first or not first.subcategory_id:
        raise ValueError("At least one line item must have a Sub Category to post income to the ledger.")

    t = Transaction.objects.create(
        business=invoice.business,
        date=paid_date,
        amount=total,
        description=f"Invoice payment {invoice.invoice_number} - {invoice.contact.display_name}",
        subcategory=first.subcategory,
        contact=invoice.contact,
        job=invoice.job,
        team=invoice.team,
        invoice_number=invoice.invoice_number,
        notes="Auto-generated from paid invoice.",
    )

    invoice.paid_date = paid_date
    invoice.status = Invoice.Status.PAID
    invoice.income_transaction = t
    invoice.save(update_fields=["paid_date", "status", "income_transaction"])
    return t


@transaction.atomic
def void_invoice(*, invoice: Invoice) -> None:
    if invoice.status == Invoice.Status.PAID:
        raise ValueError("Paid invoices cannot be voided.")
    invoice.status = Invoice.Status.VOID
    invoice.save(update_fields=["status"])



def _invoice_recipient(invoice: Invoice) -> str:
    return (invoice.bill_to_email or getattr(invoice.contact, "email", "") or "").strip()


def _send_invoice_email(*, invoice: Invoice, pdf_bytes: bytes, base_url: str | None = None, sent_by=None) -> None:
    recipient = _invoice_recipient(invoice)
    if not recipient:
        raise ValueError("Invoice contact does not have an email address.")

    email_settings = get_or_create_business_email_settings(business=invoice.business, owner_user=sent_by)
    if not email_settings.sending_ready:
        raise ValueError("Business email settings are not ready for sending.")

    company = getattr(invoice.business, "company_profile", None)
    subject = f"Invoice {invoice.invoice_number} from {email_settings.display_name or invoice.business.name}"
    context = {
        "invoice": invoice,
        "business": invoice.business,
        "company": company,
        "email_settings": email_settings,
    }
    text_body = render_to_string("invoices/email/invoice_email.txt", context)
    html_body = render_to_string("invoices/email/invoice_email.html", context)

    from_name = (email_settings.from_name or email_settings.display_name or invoice.business.name).strip()
    from_email = (email_settings.from_email or settings.DEFAULT_FROM_EMAIL).strip()
    cc = [email_settings.invoice_cc_email] if (email_settings.invoice_cc_email or "").strip() else []
    reply_to = [email_settings.reply_to_email] if (email_settings.reply_to_email or "").strip() else None

    log = OutgoingEmailLog.objects.create(
        business=invoice.business,
        invoice=invoice,
        template_type=OutgoingEmailLog.TemplateType.INVOICE,
        recipient_email=recipient,
        cc_email=(email_settings.invoice_cc_email or "").strip(),
        subject=subject,
        from_email=from_email,
        reply_to_email=(email_settings.reply_to_email or "").strip(),
        sent_by=sent_by,
        status=OutgoingEmailLog.Status.PENDING,
    )

    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=f'{from_name} <{from_email}>' if from_name else from_email,
            to=[recipient],
            cc=cc,
            reply_to=reply_to,
        )
        msg.attach_alternative(html_body, "text/html")
        msg.attach(f"{invoice.invoice_number}.pdf", pdf_bytes, "application/pdf")
        msg.send(fail_silently=False)

        log.status = OutgoingEmailLog.Status.SENT
        log.sent_at = timezone.now()
        log.save(update_fields=["status", "sent_at"])
    except Exception as exc:
        log.status = OutgoingEmailLog.Status.FAILED
        log.error_message = str(exc)
        log.save(update_fields=["status", "error_message"])
        raise
