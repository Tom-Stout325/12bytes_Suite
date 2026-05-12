from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.forms import inlineformset_factory
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import DetailView, ListView
from decimal import Decimal

from .forms import InvoiceForm, InvoiceItemForm
from .models import Invoice, InvoiceItem, allocate_next_invoice_number, bump_counter_if_needed
from .services import (
    create_revision,
    mark_paid,
    render_invoice_pdf_bytes,
    send_invoice,
    void_invoice,
)


class BusinessScopedMixin:
    def get_business(self):
        b = getattr(self.request, "business", None)
        if not b:
            raise Http404("No active business.")
        return b


class InvoiceListView(LoginRequiredMixin, BusinessScopedMixin, ListView):
    model = Invoice
    template_name = "invoices/invoice_list.html"
    context_object_name = "invoices"
    paginate_by = 25

    def get_queryset(self):
        return (
            Invoice.objects.filter(business=self.get_business())
            .select_related("contact", "job")
            .order_by("-issue_date", "-id")
        )




class InvoiceDetailView(LoginRequiredMixin, BusinessScopedMixin, DetailView):
    model = Invoice
    template_name = "invoices/invoice_detail.html"
    context_object_name = "invoice"

    def get_queryset(self):
        return (
            Invoice.objects.filter(business=self.get_business())
            .select_related("contact", "job", "income_transaction")
            .prefetch_related("items")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        invoice: Invoice = ctx["invoice"]
        business = self.get_business()

        # ------------------------------------------------------------------
        # Mileage entries linked to this invoice (UNCHANGED from your version)
        # ------------------------------------------------------------------
        try:
            from vehicles.models import VehicleMiles
            from django.db.models import Sum, Value
            from django.db.models.functions import Coalesce

            mileage_qs = (
                VehicleMiles.objects.filter(business=business, invoice=invoice)
                .select_related("vehicle", "job")
                .order_by("date", "pk")
            )
            ctx["mileage_entries"] = list(mileage_qs)
            ctx["mileage_total_miles"] = mileage_qs.aggregate(
                t=Coalesce(Sum("total"), Value(Decimal("0.0")))
            )["t"]
            mileage_deduction_total = Decimal("0.00")
            for entry in ctx["mileage_entries"]:
                try:
                    vy = entry.vehicle.years.filter(business=business, year=entry.date.year).first()
                    if vy and entry.mileage_type == entry.MileageType.BUSINESS and vy.standard_mileage_deduction is not None and vy.business_miles:
                        rate = Decimal(str(vy.standard_mileage_rate or 0))
                        miles = Decimal(str(entry.total or 0))
                        mileage_deduction_total += (miles * rate)
                except Exception:
                    continue
            ctx["mileage_deduction_total"] = mileage_deduction_total.quantize(Decimal("0.01"))
        except Exception:
            ctx["mileage_entries"] = []
            ctx["mileage_total_miles"] = None
            ctx["mileage_deduction_total"] = None

        # ------------------------------------------------------------------
        # Related ledger transactions (income + expenses) linked by invoice_number
        # ------------------------------------------------------------------
        tx_list: list = []
        income_total = Decimal("0.00")
        expense_total = Decimal("0.00")
        taxable_income_total = Decimal("0.00")
        taxable_expense_total = Decimal("0.00")

        inv_num = (invoice.invoice_number or "").strip()

        if inv_num:
            from ledger.models import Transaction

            qs = (
                Transaction.objects.filter(business=business, invoice_number__iexact=inv_num)
                .select_related("subcategory", "subcategory__category", "contact", "job")
                .order_by("date", "pk")
            )

            def _kind(t: Transaction) -> str:
                trans_type = getattr(t, "trans_type", None)
                if trans_type:
                    val = str(trans_type).strip().lower()
                    if "income" in val:
                        return "income"
                    if "expense" in val:
                        return "expense"

                sub = getattr(t, "subcategory", None)
                cat = getattr(sub, "category", None) if sub else None
                cat_type = getattr(cat, "category_type", None) if cat else None
                if cat_type:
                    val = str(cat_type).strip().lower()
                    if "income" in val:
                        return "income"
                    if "expense" in val:
                        return "expense"

                amt = getattr(t, "amount", None)
                try:
                    return "income" if (amt or Decimal("0")) >= 0 else "expense"
                except Exception:
                    return "expense"

            def _deductible_expense_amount(t: Transaction, amt: Decimal) -> Decimal:
                da = getattr(t, "deductible_amount", None)
                try:
                    val = da() if callable(da) else da
                    if val is not None:
                        return Decimal(val)
                except Exception:
                    pass
                return amt

            def _mark_pills(t: Transaction) -> None:
                is_meals_50 = getattr(t, "is_meals_50", None)
                is_travel_gas = getattr(t, "is_travel_gas", None)

                try:
                    t.pill_meals = bool(is_meals_50()) if callable(is_meals_50) else False
                except Exception:
                    t.pill_meals = False

                try:
                    t.pill_gas = bool(is_travel_gas()) if callable(is_travel_gas) else False
                except Exception:
                    t.pill_gas = False

            tx_list = list(qs)
            for t in tx_list:
                amt = Decimal(getattr(t, "amount", None) or Decimal("0.00"))
                kind = _kind(t)
                _mark_pills(t)

                if kind == "income":
                    income_total += amt
                    taxable_income_total += amt
                else:
                    expense_total += amt
                    taxable_expense_total += _deductible_expense_amount(t, amt)

        # Mileage linked directly to this invoice is not a ledger transaction,
        # but standard business mileage is deductible and should be included in
        # the taxable expense view. Keep actual expenses based on linked ledger
        # transactions only; add mileage only to the taxable totals.
        taxable_mileage_total = ctx.get("mileage_deduction_total") or Decimal("0.00")
        try:
            taxable_mileage_total = Decimal(taxable_mileage_total)
        except Exception:
            taxable_mileage_total = Decimal("0.00")

        taxable_expense_total += taxable_mileage_total

        net_income = income_total - expense_total
        taxable_net_income = taxable_income_total - taxable_expense_total

        ctx.update(
            {
                "tx_list": tx_list,
                "income_total": income_total,
                "expense_total": expense_total,
                "net_income": net_income,
                "taxable_income_total": taxable_income_total,
                "taxable_expense_total": taxable_expense_total,
                "taxable_mileage_total": taxable_mileage_total,
                "taxable_net_income": taxable_net_income,
                "has_transactions": bool(tx_list or taxable_mileage_total),
            }
        )
        return ctx





def _get_item_formset(*, business):
    return inlineformset_factory(
        Invoice,
        InvoiceItem,
        form=InvoiceItemForm,
        fields=["description", "subcategory", "qty", "unit_price", "sort_order"],
        extra=0,
        can_delete=True,
    )


@login_required
def invoice_create(request: HttpRequest) -> HttpResponse:
    business = request.business
    invoice = Invoice(business=business)

    ItemFormSet = _get_item_formset(business=business)

    if request.method == "POST":
        form = InvoiceForm(request.POST, instance=invoice, business=business)
        formset = ItemFormSet(request.POST, instance=invoice, prefix="items", form_kwargs={"business": business})

        if form.is_valid() and formset.is_valid():
            invoice = form.save(commit=False)
            invoice.business = business
            invoice.status = Invoice.Status.DRAFT

            entered = (form.cleaned_data.get("invoice_number") or "").strip()
            if entered:
                # Manual number already validated by InvoiceForm.clean_invoice_number()
                invoice.invoice_number = entered
                bump_counter_if_needed(
                    business=business,
                    issue_date=invoice.issue_date,
                    invoice_number=invoice.invoice_number,
                )
            else:
                # Reserve next number on draft save
                invoice.invoice_number = allocate_next_invoice_number(
                    business=business,
                    issue_date=invoice.issue_date,
                    job=invoice.job,
                )

            invoice.save()

            items = formset.save(commit=False)
            for it in items:
                it.business = business
                it.invoice = invoice
                it.save()
            for it in formset.deleted_objects:
                it.delete()
            messages.success(request, "Invoice created.")
            return redirect("invoices:invoice_detail", pk=invoice.pk)

        messages.error(request, "Please fix the errors below.")
    else:
        form = InvoiceForm(instance=invoice, business=business)
        formset = ItemFormSet(instance=invoice, prefix="items", form_kwargs={"business": business})

    return render(
        request,
        "invoices/invoice_form.html",
        {"form": form, "formset": formset, "invoice": None},
    )


@login_required
def invoice_update(request: HttpRequest, pk: int) -> HttpResponse:
    business = request.business
    invoice = get_object_or_404(Invoice, business=business, pk=pk)

    if invoice.status != Invoice.Status.DRAFT:
        messages.error(request, "This invoice is locked and cannot be edited.")
        return redirect("invoices:invoice_detail", pk=invoice.pk)

    ItemFormSet = _get_item_formset(business=business)

    if request.method == "POST":
        form = InvoiceForm(request.POST, instance=invoice, business=business)
        formset = ItemFormSet(request.POST, instance=invoice, prefix="items", form_kwargs={"business": business})

        if form.is_valid() and formset.is_valid():
            invoice = form.save(commit=False)
            invoice.business = business

            entered = (form.cleaned_data.get("invoice_number") or "").strip()
            if entered:
                invoice.invoice_number = entered
                bump_counter_if_needed(
                    business=business,
                    issue_date=invoice.issue_date,
                    invoice_number=invoice.invoice_number,
                )
            else:
                # Do not allow clearing an existing reserved number
                if not invoice.invoice_number:
                    invoice.invoice_number = allocate_next_invoice_number(
                        business=business,
                        issue_date=invoice.issue_date,
                        job=invoice.job,
                    )

            invoice.save()

            items = formset.save(commit=False)
            for it in items:
                it.business = business
                it.invoice = invoice
                it.save()
            for it in formset.deleted_objects:
                it.delete()
            messages.success(request, "Invoice updated.")
            return redirect("invoices:invoice_detail", pk=invoice.pk)

        messages.error(request, "Please fix the errors below.")
    else:
        form = InvoiceForm(instance=invoice, business=business)
        formset = ItemFormSet(instance=invoice, prefix="items", form_kwargs={"business": business})

    return render(
        request,
        "invoices/invoice_form.html",
        {"form": form, "formset": formset, "invoice": invoice},
    )


@login_required
def invoice_send(request: HttpRequest, pk: int) -> HttpResponse:
    invoice = get_object_or_404(Invoice, business=request.business, pk=pk)
    if request.method != "POST":
        return redirect("invoices:invoice_detail", pk=invoice.pk)

    try:
        send_invoice(invoice=invoice, base_url=request.build_absolute_uri("/"), sent_by=request.user)
        messages.success(request, "Invoice sent (PDF frozen).")
    except Exception as e:
        messages.error(request, f"Could not send invoice: {e}")
    return redirect("invoices:invoice_detail", pk=invoice.pk)


@login_required
def invoice_mark_paid(request: HttpRequest, pk: int) -> HttpResponse:
    invoice = get_object_or_404(Invoice, business=request.business, pk=pk)
    if request.method != "POST":
        return redirect("invoices:invoice_detail", pk=invoice.pk)

    try:
        mark_paid(invoice=invoice)
        messages.success(request, "Invoice marked as paid and income transaction recorded.")
    except Exception as e:
        messages.error(request, f"Could not mark as paid: {e}")
    return redirect("invoices:invoice_detail", pk=invoice.pk)


@login_required
def invoice_void(request: HttpRequest, pk: int) -> HttpResponse:
    invoice = get_object_or_404(Invoice, business=request.business, pk=pk)
    if request.method != "POST":
        return redirect("invoices:invoice_detail", pk=invoice.pk)

    try:
        void_invoice(invoice=invoice)
        messages.success(request, "Invoice voided.")
    except Exception as e:
        messages.error(request, f"Could not void invoice: {e}")
    return redirect("invoices:invoice_detail", pk=invoice.pk)


@login_required
def invoice_revise(request: HttpRequest, pk: int) -> HttpResponse:
    invoice = get_object_or_404(Invoice, business=request.business, pk=pk)
    if request.method != "POST":
        return redirect("invoices:invoice_detail", pk=invoice.pk)

    try:
        rev = create_revision(invoice=invoice)
        messages.success(request, f"Revision created: {rev.invoice_number}")
        return redirect("invoices:invoice_update", pk=rev.pk)
    except Exception as e:
        messages.error(request, f"Could not create revision: {e}")
        return redirect("invoices:invoice_detail", pk=invoice.pk)


# -----------------------------------------------------------------------------
# PDF preview/download
# -----------------------------------------------------------------------------


def _invoice_pdf_fileresponse(*, invoice: Invoice, inline: bool) -> FileResponse:
    """Serve the stored PDF file field."""
    filename = f"{invoice.invoice_number or 'invoice'}.pdf"
    resp = FileResponse(invoice.pdf_file.open("rb"), content_type="application/pdf")
    disp = "inline" if inline else "attachment"
    resp["Content-Disposition"] = f'{disp}; filename="{filename}"'
    return resp


@login_required
def invoice_pdf_preview(request: HttpRequest, pk: int) -> HttpResponse:
    """Inline PDF preview.

    - If a frozen PDF exists (sent/paid), serve it.
    - Otherwise render on-demand via WeasyPrint.
    """
    invoice = get_object_or_404(
        Invoice.objects.filter(business=request.business)
        .select_related("contact", "job")
        .prefetch_related("items"),
        pk=pk,
    )

    if invoice.pdf_file:
        return _invoice_pdf_fileresponse(invoice=invoice, inline=True)

    pdf_bytes = render_invoice_pdf_bytes(invoice=invoice, base_url=request.build_absolute_uri("/"))
    filename = f"{invoice.invoice_number or 'invoice'}.pdf"
    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{filename}"'
    return resp


@login_required
def invoice_pdf_download(request: HttpRequest, pk: int) -> HttpResponse:
    """Download PDF.

    - If a frozen PDF exists, serve it as attachment.
    - Otherwise render on-demand and download.
    """
    invoice = get_object_or_404(
        Invoice.objects.filter(business=request.business)
        .select_related("contact", "job")
        .prefetch_related("items"),
        pk=pk,
    )

    if invoice.pdf_file:
        return _invoice_pdf_fileresponse(invoice=invoice, inline=False)

    pdf_bytes = render_invoice_pdf_bytes(invoice=invoice, base_url=request.build_absolute_uri("/"))
    filename = f"{invoice.invoice_number or 'invoice'}.pdf"
    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp