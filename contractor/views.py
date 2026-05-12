from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.decorators.http import require_POST
from django.core.mail import EmailMessage
from django.core.files.base import ContentFile
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import DetailView, ListView

from ledger.models import Contact, Transaction

from .forms import ContractorYearForm, W9PortalForm, W9ReviewForm
from .models import Contractor1099, ContractorW9Submission
from .renderer_1099nec import render_1099nec_pdf_bytes, render_1099nec_pdf_response
from .services.nec1099 import nec_total_for_contact, nec_totals_for_year, default_tax_year
from core.emailing import business_from_email, normalize_reply_to
from .services.w9_email import send_w9_request_email
from .utils_token import build_portal_url, issue_portal_token, verify_portal_token


def _current_year() -> int:
    return timezone.localdate().year


def _year_choices() -> list[int]:
    y = _current_year()
    return list(range(y - 5, y + 1))


def _get_business(request: HttpRequest):
    business = getattr(request, "business", None)
    if business is None:
        raise Http404("Business not found on request")
    return business


class ContractorListView(LoginRequiredMixin, ListView):
    template_name = "contractor/contractor_list.html"
    context_object_name = "contractors"
    paginate_by = 50
    model = Contact

    def get_queryset(self):
        business = _get_business(self.request)
        return (
            Contact.objects.filter(business=business, is_contractor=True, is_active=True)
            .order_by("display_name", "legal_name", "id")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["year_form"] = ContractorYearForm(initial={"year": default_tax_year()}, year_choices=_year_choices())
        ctx["year"] = default_tax_year()
        return ctx


class ContractorDetailView(LoginRequiredMixin, DetailView):
    template_name = "contractor/contractor_detail.html"
    model = Contact
    context_object_name = "contractor"

    def get_object(self, queryset=None):
        business = _get_business(self.request)
        pk = self.kwargs.get("pk")
        return get_object_or_404(Contact, business=business, pk=pk, is_contractor=True)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        business = _get_business(self.request)
        contractor: Contact = ctx["contractor"]

        year = int(self.request.GET.get("year") or default_tax_year())
        total = nec_total_for_contact(business_id=business.id, contact_id=contractor.id, year=year)

        # Linked transactions (same filter used for totals)
        tx_qs = (
            Transaction.objects.filter(
                business=business,
                contact=contractor,
                date__year=year,
                trans_type=Transaction.TransactionType.EXPENSE,
                is_refund=False,
                subcategory__is_1099_reportable_default=True,
            )
            .select_related("subcategory", "job", "team")
            .order_by("-date", "-id")
        )

        ctx.update(
            {
                "year": year,
                "year_form": ContractorYearForm(initial={"year": year}, year_choices=_year_choices()),
                "total_1099": total,
                "linked_transactions": tx_qs,
            }
        )

        token = issue_portal_token(business_id=business.id, contact_id=contractor.id)
        ctx["w9_portal_url"] = build_portal_url(self.request, token)
        return ctx


@login_required
@require_POST
def mark_w9_requested(request: HttpRequest, pk: int) -> HttpResponse:
    business = _get_business(request)
    contact = get_object_or_404(Contact, business=business, pk=pk, is_contractor=True)

    contact.w9_status = "requested"
    contact.w9_sent_date = contact.w9_sent_date or timezone.localdate()
    contact.save(update_fields=["w9_status", "w9_sent_date"])

    messages.success(request, "W-9 marked as requested.")
    return redirect("contractor:detail", pk=contact.pk)


@login_required
@require_POST
def send_w9_email(request: HttpRequest, pk: int) -> HttpResponse:
    business = _get_business(request)
    contact = get_object_or_404(Contact, business=business, pk=pk, is_contractor=True)

    if not contact.email:
        messages.error(request, "This contractor has no email address.")
        return redirect("contractor:detail", pk=contact.pk)

    token = issue_portal_token(business_id=business.id, contact_id=contact.id)
    portal_url = build_portal_url(request, token)

    try:
        _sent, message = send_w9_request_email(
            business=business,
            contractor_name=contact.display_name or contact.legal_name or "Contractor",
            contractor_email=contact.email,
            portal_url=portal_url,
            owner_user=request.user,
        )
    except Exception as exc:
        messages.error(request, f"W-9 email could not be sent: {exc}")
        return redirect("contractor:detail", pk=contact.pk)

    # Update status automatically (requested)
    contact.w9_status = "requested"
    contact.w9_sent_date = timezone.localdate()
    contact.save(update_fields=["w9_status", "w9_sent_date"])

    messages.success(request, f"{message} Status set to Requested.")
    return redirect("contractor:detail", pk=contact.pk)


@login_required
def w9_view(request: HttpRequest, pk: int) -> HttpResponse:
    business = _get_business(request)
    contact = get_object_or_404(Contact, business=business, pk=pk, is_contractor=True)
    w9_history = contact.w9_submissions.filter(business=business).order_by("-submitted_at", "-id")
    return render(request, "contractor/w9_view.html", {"contractor": contact, "w9_history": w9_history})



@login_required
def w9_review_list(request: HttpRequest) -> HttpResponse:
    business = _get_business(request)
    submissions = (
        ContractorW9Submission.objects.filter(business=business)
        .select_related("contact")
        .order_by("-submitted_at", "-id")
    )
    pending_count = submissions.filter(review_status=ContractorW9Submission.ReviewStatus.PENDING).count()
    verified_count = submissions.filter(review_status=ContractorW9Submission.ReviewStatus.VERIFIED).count()
    return render(
        request,
        "contractor/w9_review_list.html",
        {
            "submissions": submissions,
            "pending_count": pending_count,
            "verified_count": verified_count,
        },
    )


@login_required
def w9_review_detail(request: HttpRequest, pk: int) -> HttpResponse:
    business = _get_business(request)
    submission = get_object_or_404(
        ContractorW9Submission.objects.select_related("contact", "business"),
        business=business,
        pk=pk,
    )
    history = submission.contact.w9_submissions.filter(business=business).exclude(pk=submission.pk).order_by("-submitted_at", "-id")[:10]

    if request.method == "POST":
        form = W9ReviewForm(request.POST, request.FILES, instance=submission)
        if form.is_valid():
            submission = form.save(commit=False)
            submission.reviewed_at = timezone.now()
            reviewer_name = request.user.get_full_name().strip() if hasattr(request.user, "get_full_name") else ""
            submission.reviewed_by_name = reviewer_name or getattr(request.user, "username", "") or "Staff"
            submission.save()

            contact = submission.contact
            if form.cleaned_data.get("replace_contact_w9_document") and hasattr(contact, "w9_document"):
                contact.w9_document = form.cleaned_data["replace_contact_w9_document"]

            if form.cleaned_data.get("verify_and_update_contact"):
                if hasattr(contact, "w9_status"):
                    if submission.review_status == ContractorW9Submission.ReviewStatus.VERIFIED:
                        contact.w9_status = "verified"
                    elif submission.review_status == ContractorW9Submission.ReviewStatus.NEEDS_UPDATE:
                        contact.w9_status = "requested"
                    else:
                        contact.w9_status = "received"
                if hasattr(contact, "w9_received_date") and submission.submitted_at:
                    contact.w9_received_date = (contact.w9_received_date or timezone.localdate())
                if hasattr(contact, "tin_type") and submission.tin_type:
                    contact.tin_type = submission.tin_type
                if hasattr(contact, "tin_last4") and submission.tin_last4:
                    contact.tin_last4 = submission.tin_last4
                if hasattr(contact, "is_1099_eligible"):
                    contact.is_1099_eligible = True
                if hasattr(contact, "business_name") and submission.business_name:
                    contact.business_name = submission.business_name
                if hasattr(contact, "address1") and submission.address_line1:
                    contact.address1 = submission.address_line1
                if hasattr(contact, "address2"):
                    contact.address2 = submission.address_line2
                if hasattr(contact, "city"):
                    contact.city = submission.city
                if hasattr(contact, "state"):
                    contact.state = submission.state
                if hasattr(contact, "zip_code"):
                    contact.zip_code = submission.zip_code
                if hasattr(contact, "save"):
                    contact.save()
            elif form.cleaned_data.get("replace_contact_w9_document") and hasattr(contact, "save"):
                contact.save(update_fields=["w9_document"])

            messages.success(request, "W-9 review saved.")
            return redirect("contractor:w9_review_detail", pk=submission.pk)
    else:
        form = W9ReviewForm(instance=submission)

    return render(
        request,
        "contractor/w9_review_detail.html",
        {
            "submission": submission,
            "form": form,
            "history": history,
        },
    )

def w9_portal(request: HttpRequest, token: str) -> HttpResponse:
    verified = verify_portal_token(token)
    if not verified:
        raise Http404("Invalid or expired token")

    business_id = verified["business_id"]
    contact_id = verified["contact_id"]

    contact = get_object_or_404(Contact, business_id=business_id, pk=contact_id, is_contractor=True)

    if request.method == "POST":
        form = W9PortalForm(request.POST, request.FILES)
        if form.is_valid():
            cleaned = form.cleaned_data

            submission = ContractorW9Submission.objects.create(
                business_id=business_id,
                contact=contact,
                full_name=(cleaned.get("full_name") or "").strip(),
                business_name=(cleaned.get("business_name") or "").strip(),
                entity_type=(cleaned.get("entity_type") or "").strip(),
                tin_type=cleaned["taxpayer_id_type"],
                tin_last4=str(cleaned["tin"])[-4:],
                address_line1=(cleaned.get("address1") or "").strip(),
                address_line2=(cleaned.get("address2") or "").strip(),
                city=(cleaned.get("city") or "").strip(),
                state=(cleaned.get("state") or "").strip(),
                zip_code=(cleaned.get("zip_code") or "").strip(),
                signature_name=(cleaned.get("signature_name") or "").strip(),
                signature_data=(cleaned.get("signature_data") or "").strip(),
                signature_date=cleaned.get("signature_date"),
                certification_accepted=bool(cleaned.get("certification_accepted")),
                uploaded_w9_document=cleaned.get("upload_w9_document"),
                submitted_ip=request.META.get("REMOTE_ADDR") or None,
                submitted_ua=(request.META.get("HTTP_USER_AGENT") or "")[:255],
            )

            # Update Contact metadata (non-sensitive)
            contact.w9_status = "received"
            contact.w9_received_date = contact.w9_received_date or timezone.localdate()
            contact.tin_type = cleaned["taxpayer_id_type"]
            contact.tin_last4 = str(cleaned["tin"])[-4:]
            contact.is_1099_eligible = True

            # Optional sync back of basic info
            bn = (cleaned.get("business_name") or "").strip()
            if bn and hasattr(contact, "business_name"):
                contact.business_name = bn
            if hasattr(contact, "entity_type") and cleaned.get("entity_type"):
                contact.entity_type = cleaned["entity_type"]

            contact.address1 = (cleaned.get("address1") or "").strip()
            contact.address2 = (cleaned.get("address2") or "").strip()
            contact.city = (cleaned.get("city") or "").strip()
            contact.state = (cleaned.get("state") or "").strip()
            contact.zip_code = (cleaned.get("zip_code") or "").strip()

            contact.save()

            return render(
                request,
                "contractor/w9_thanks.html",
                {"contact": contact, "business": contact.business, "submission": submission},
            )
    else:
        form = W9PortalForm(initial={
            "full_name": contact.display_name or contact.legal_name,
            "business_name": getattr(contact, "business_name", ""),
            "address1": getattr(contact, "address1", ""),
            "address2": getattr(contact, "address2", ""),
            "city": getattr(contact, "city", ""),
            "state": getattr(contact, "state", ""),
            "zip_code": getattr(contact, "zip_code", ""),
            "entity_type": getattr(contact, "entity_type", ""),
            "taxpayer_id_type": getattr(contact, "tin_type", "") or "ssn",
            "signature_name": contact.display_name or contact.legal_name,
        })

    return render(request, "contractor/w9_portal.html", {"form": form, "contact": contact, "business": contact.business})


def _ensure_1099_record(*, business, contact, year: int, regenerate: bool = False) -> tuple[Contractor1099, Decimal]:
    total = nec_total_for_contact(business_id=business.id, contact_id=contact.id, year=year)
    obj, _ = Contractor1099.objects.get_or_create(business=business, contact=contact, tax_year=year)

    if regenerate or not obj.copy_b_pdf:
        b_bytes = render_1099nec_pdf_bytes(
            business=business,
            contractor=contact,
            year=year,
            nonemployee_comp=total,
            copy="b",
        )
        obj.copy_b_pdf.save(f"1099-NEC_{year}_copyB.pdf", ContentFile(b_bytes), save=False)

    if regenerate or not obj.copy_1_pdf:
        one_bytes = render_1099nec_pdf_bytes(
            business=business,
            contractor=contact,
            year=year,
            nonemployee_comp=total,
            copy="1",
        )
        obj.copy_1_pdf.save(f"1099-NEC_{year}_copy1.pdf", ContentFile(one_bytes), save=False)

    obj.generated_at = timezone.now()
    obj.save()
    return obj, total


@login_required
def contractor_1099_center(request: HttpRequest, pk: int) -> HttpResponse:
    business = _get_business(request)
    year = int(request.GET.get("year") or default_tax_year())
    contractor = get_object_or_404(Contact, business=business, pk=pk, is_contractor=True)

    transactions = (
        Transaction.objects.filter(
            business=business,
            contact=contractor,
            date__year=year,
            trans_type=Transaction.TransactionType.EXPENSE,
            is_refund=False,
            subcategory__is_1099_reportable_default=True,
        )
        .select_related("subcategory", "job", "team")
        .order_by("-date", "-id")
    )

    total = nec_total_for_contact(business_id=business.id, contact_id=contractor.id, year=year)
    stored_1099 = Contractor1099.objects.filter(
        business=business,
        contact=contractor,
        tax_year=year,
    ).first()

    return render(
        request,
        "contractor/1099_center_detail.html",
        {
            "business": business,
            "contractor": contractor,
            "year": year,
            "year_form": ContractorYearForm(initial={"year": year}, year_choices=_year_choices()),
            "transactions": transactions,
            "total_1099": total,
            "stored_1099": stored_1099,
        },
    )


@login_required
def nec_1099_center(request: HttpRequest) -> HttpResponse:
    business = _get_business(request)
    year = int(request.GET.get("year") or default_tax_year())
    totals = nec_totals_for_year(business_id=business.id, year=year)
    return render(
        request,
        "contractor/1099_list.html",
        {
            "year": year,
            "year_form": ContractorYearForm(initial={"year": year}, year_choices=_year_choices()),
            "totals": totals,
        },
    )


@login_required
def nec_1099_preview(request: HttpRequest, pk: int) -> HttpResponse:
    business = _get_business(request)
    year = int(request.GET.get("year") or default_tax_year())
    contact = get_object_or_404(Contact, business=business, pk=pk, is_contractor=True)
    total = nec_total_for_contact(business_id=business.id, contact_id=contact.id, year=year)

    return render(
        request,
        "contractor/1099-nec.html",
        {"business": business, "contractor": contact, "year": year, "amount_nonemployee_comp": total},
    )


@login_required
def nec_1099_pdf(request: HttpRequest, pk: int) -> HttpResponse:
    business = _get_business(request)
    year = int(request.GET.get("year") or default_tax_year())
    contact = get_object_or_404(Contact, business=business, pk=pk, is_contractor=True)
    total = nec_total_for_contact(business_id=business.id, contact_id=contact.id, year=year)

    return render_1099nec_pdf_response(
        request=request,
        business=business,
        contractor=contact,
        year=year,
        nonemployee_comp=total,
    )


@login_required
def store_1099_pdf(request: HttpRequest, pk: int) -> HttpResponse:
    business = _get_business(request)
    year = int(request.GET.get("year") or default_tax_year())
    contact = get_object_or_404(Contact, business=business, pk=pk, is_contractor=True)

    _ensure_1099_record(business=business, contact=contact, year=year, regenerate=True)
    messages.success(request, f"Stored 1099 PDFs for tax year {year}.")
    return redirect("contractor:contractor_1099_center", pk=contact.pk)


@login_required
def email_1099_copy_b(request: HttpRequest, pk: int) -> HttpResponse:
    business = _get_business(request)
    year = int(request.GET.get("year") or default_tax_year())
    contact = get_object_or_404(Contact, business=business, pk=pk, is_contractor=True)

    if not contact.email:
        messages.error(request, "This contractor has no email address.")
        return redirect("contractor:contractor_1099_center", pk=contact.pk)

    obj, total = _ensure_1099_record(business=business, contact=contact, year=year, regenerate=False)

    from_name, from_email, reply_to = business_from_email(business=business, owner_user=request.user)

    subject = f"Your 1099-NEC Copy B for tax year {year}"
    body = (
        f"Hi {contact.display_name},\n\n"
        f"Attached is your Form 1099-NEC Copy B for tax year {year}. "
        f"Please keep it with your tax records.\n\n"
        f"Thank you,\n{business.name}"
    )
    msg = EmailMessage(
        subject=subject,
        body=body,
        to=[contact.email],
        from_email=f"{from_name} <{from_email}>" if from_name and from_email else from_email or None,
        reply_to=normalize_reply_to(reply_to),
    )
    with obj.copy_b_pdf.open("rb") as fh:
        msg.attach(filename=f"1099-NEC_{year}_copyB.pdf", content=fh.read(), mimetype="application/pdf")

    try:
        msg.send(fail_silently=False)
    except Exception as exc:
        messages.warning(
            request,
            "Copy B PDF was generated and stored, but the email was not sent because the email backend is not configured yet: "
            f"{exc}"
        )
        return redirect("contractor:contractor_1099_center", pk=contact.pk)

    obj.emailed_at = timezone.now()
    obj.emailed_to = contact.email
    obj.email_count = (obj.email_count or 0) + 1
    obj.save(update_fields=["emailed_at", "emailed_to", "email_count"])

    messages.success(request, f"Emailed Copy B for tax year {year} to {contact.email}.")
    return redirect("contractor:contractor_1099_center", pk=contact.pk)
