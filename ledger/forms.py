# ledger/forms.py
from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Div, Field, HTML, Layout
from django.db.models.functions import Lower
from django.utils import timezone

from ledger.models import Category, Job, Contact, SubCategory, Transaction, Team
from vehicles.models import Vehicle
from assets.models import Asset


class TransactionForm(forms.ModelForm):
    """Business-scoped transaction form.

    - Filters all dropdowns by the active Business.
    - Shows Type + Category as derived badges after Subcategory selection.
    - Uses explicit Transport + Vehicle fields (Vehicle appears only when relevant).
    """

    class Meta:
        model = Transaction
        fields = [
            "date",
            "amount",
            "subcategory",
            "is_refund",
            "invoice_number",
            "receipt",
            "asset",
            "contact",
            "team",
            "job",
            "transport_type",
            "vehicle",
            "description",
            "notes",
        ]

    def __init__(self, *args, **kwargs):
        self.business = kwargs.pop("business", None)
        super().__init__(*args, **kwargs)

        if not self.business:
            raise ValueError("TransactionForm requires business=...")

        # Scope dropdowns
        self.fields["subcategory"].queryset = (
            SubCategory.objects
            .filter(business=self.business, is_active=True)
            .select_related("category")
            .order_by(Lower("name"))
        )
        self.fields["contact"].queryset = Contact.objects.filter(business=self.business).order_by("display_name")
        self.fields["contact"].label = "Contact"
        self.fields["team"].queryset = Team.objects.filter(business=self.business, is_active=True).order_by("sort_order", "name")
        self.fields["job"].queryset = Job.objects.filter(business=self.business).order_by("-is_active", "-job_year", "job_number", "label")

        self.fields["is_refund"].widget.attrs.setdefault("class", "form-check-input")

        # Transport + Vehicle fields
        self.fields["transport_type"].label = "Transport"
        self.fields["transport_type"].widget.attrs.setdefault("class", "form-select")
        self.fields["vehicle"].queryset = Vehicle.objects.filter(
            business=self.business,
            is_active=True,
            is_business=True,
        ).order_by("sort_order", "label")
        self.fields["vehicle"].required = False
        self.fields["vehicle"].widget.attrs.setdefault("class", "form-select")

        self.fields["date"].widget = forms.DateInput(
            attrs={"type": "date", "class": "form-control"},
            format="%Y-%m-%d",
        )
        self.fields["date"].input_formats = ["%Y-%m-%d", "%m-%d-%Y", "%m/%d/%Y"]
        if not (self.instance and self.instance.pk) and not self.is_bound and not self.initial.get("date"):
            self.initial["date"] = timezone.localdate()

        self.fields["amount"].widget.attrs.setdefault("class", "form-control")
        self.fields["amount"].widget.attrs.setdefault("inputmode", "decimal")
        self.fields["amount"].widget.attrs.setdefault("step", "0.01")
        self.fields["amount"].widget.attrs.setdefault("placeholder", "")
        if not (self.instance and self.instance.pk) and not self.is_bound:
            self.initial["amount"] = ""

        # Asset dropdown (used for depreciation / 179 / capitalizable items)
        self.fields["asset"].required = False
        self.fields["asset"].label = "Asset"
        self.fields["asset"].queryset = Asset.objects.filter(business=self.business).order_by("name")
        self.fields["asset"].widget.attrs.setdefault("class", "form-select")

        # Receipt upload
        self.fields["receipt"].required = False
        self.fields["receipt"].label = "Receipt"
        self.fields["receipt"].widget.attrs.setdefault("class", "form-control")
        # Helpful on mobile (camera / files). Browsers ignore unsupported tokens.
        self.fields["receipt"].widget.attrs.setdefault("accept", "image/*,application/pdf")

        # Invoice number: prefill next number for convenience (still user-editable)
        if not (self.instance and self.instance.pk) and not (self.initial.get("invoice_number") or "").strip():
            nxt = self._next_invoice_number()
            if nxt:
                self.initial["invoice_number"] = nxt

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True

        self.helper.layout = Layout(
            HTML('<div class="fw-semibold mb-2">Details</div>'),
            Div(
                Div(Field("date"), css_class="col-12 col-sm-6 col-md-4"),
                Div(Field("amount"), css_class="col-12 col-sm-6 col-md-4"),
                css_class="row g-3",
            ),
            HTML('{% include "ledger/partials/_subcategory_select.html" %}'),
            Div(
                Div(Field("is_refund"), css_class="col-12 col-md-3"),
                Div(Field("invoice_number"), css_class="col-12 col-md-5"),
                Div(HTML('{% include "ledger/partials/_contact_select.html" %}'), css_class="col-12 col-md-4"),
                css_class="row g-3",
            ),
            Div(
                Div(Field("team"), css_class="col-12 col-md-4"),
                Div(Field("job"), css_class="col-12 col-md-4"),
                Div(Field("transport_type"), css_class="col-12 col-md-4"),
                css_class="row g-3",
            ),
            Div(
                Div(Field("vehicle"), css_class="col-12 col-md-6", css_id="vehicleWrap"),
                Div(HTML(""), css_class="col-12 col-md-6"),
                css_class="row g-3",
            ),
            HTML("<hr class='my-4'>"),
            HTML('<div class="fw-semibold mb-2">Notes</div>'),
            Field("description"),
            Field("notes"),
            HTML('{% include "ledger/partials/_receipt_upload.html" %}'),
        )

    def clean(self):
        cleaned = super().clean()

        sc = cleaned.get("subcategory")
        transport = (cleaned.get("transport_type") or "").strip()
        vehicle = cleaned.get("vehicle")

        # No transport selected.
        # If the subcategory requires a vehicle (without transport), keep vehicle as-is.
        if not transport:
            self.instance.transport_type = ""
            if sc and getattr(sc, "requires_vehicle", False):
                self.instance.vehicle = vehicle
            else:
                self.instance.vehicle = None
            return cleaned

        if transport in ("personal_vehicle", "rental_car"):
            self.instance.transport_type = transport
            self.instance.vehicle = None
            return cleaned

        if transport == "business_vehicle":
            self.instance.transport_type = "business_vehicle"
            self.instance.vehicle = vehicle
            return cleaned

        raise ValidationError({"transport_type": "Invalid transport type."})
    def _next_invoice_number(self) -> str:
        """Best-effort next invoice number.

        Uses the max numeric invoice_number for this business + 1.
        If none exist, returns empty string.
        """
        qs = (
            Transaction.objects
            .filter(business=self.business)
            .exclude(invoice_number="")
            .values_list("invoice_number", flat=True)
        )

        best = None
        for s in qs:
            s = (s or "").strip()
            if not s.isdigit():
                continue
            try:
                n = int(s)
            except Exception:
                continue
            if best is None or n > best:
                best = n

        if best is None:
            return ""
        return str(best + 1)



#<------------------------------------  P A Y E E   F O R M   ---------------------------->

class ContactForm(forms.ModelForm):
    """Business-scoped Contact form."""
    class Meta:
        model = Contact
        fields = [
            "display_name",
            "client_code",
            "legal_name",
            "business_name",
            "email",
            "phone",
            "address1",
            "address2",
            "city",
            "state",
            "zip_code",
            "country",
            "is_vendor",
            "is_customer",
            "is_contractor",
            # Contractor-only (accordion)
            "is_active",
            "contractor_number",
            "entity_type",
            "is_1099_eligible",
            "tin_type",
            "tin_last4",
            "w9_status",
            "w9_sent_date",
            "w9_received_date",
            "w9_document",
            "edelivery_consent",
            "edelivery_consent_date",
            "contractor_notes",
        ]

    def __init__(self, *args, **kwargs):
        self.business = kwargs.pop("business", None)
        super().__init__(*args, **kwargs)

        if not self.business:
            raise ValueError("ContactForm requires business=...")

        # Mobile-friendly defaults
        for name, field in self.fields.items():
            widget = field.widget
            if hasattr(widget, "attrs"):
                if getattr(widget, "input_type", "") != "checkbox":
                    widget.attrs.setdefault("class", "form-control")

        # Checkbox styling
        for cb in ("is_vendor", "is_customer", "is_contractor", "is_active", "is_1099_eligible", "edelivery_consent"):
            if cb in self.fields:
                self.fields[cb].widget.attrs.setdefault("class", "form-check-input")

        # Date inputs
        for d in ("w9_sent_date", "w9_received_date", "edelivery_consent_date"):
            if d in self.fields:
                self.fields[d].widget.attrs.setdefault("type", "date")
                self.fields[d].widget.attrs.setdefault("class", "form-control")


        # Lock client_code once created.
        if self.instance and self.instance.pk:
            self.fields["client_code"].disabled = True
            self.fields["client_code"].help_text = "Client Code is locked once set. Create a new client to use a different code."




#<------------------------------------  J O B   F O R M   ---------------------------->


class JobForm(forms.ModelForm):
    """Business-scoped Job form."""

    class Meta:
        model = Job
        fields = [
            "label",
            "client",
            "job_year",
            "job_type",
            "city",
            "address",
            "notes",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        self.business = kwargs.pop("business", None)
        super().__init__(*args, **kwargs)

        if not self.business:
            raise ValueError("JobForm requires business=...")

        # Scope client dropdown to contacts marked as customers in this business
        self.fields["client"].queryset = (
            Contact.objects.filter(business=self.business, is_customer=True)
            .order_by("display_name")
        )

        # Year defaults to current year; keep it as a simple number input for now.
        self.fields["job_year"].initial = getattr(self.instance, "job_year", None) or timezone.now().year

        # Show job_number as read-only display when editing.
        # (Job.job_number is generated; users don't edit it.)
        if self.instance and self.instance.pk:
            self.fields["label"].help_text = f"Job Number: {self.instance.job_number}"

        # Mobile-friendly defaults
        for name, field in self.fields.items():
            widget = field.widget
            if hasattr(widget, "attrs"):
                if getattr(widget, "input_type", "") != "checkbox":
                    widget.attrs.setdefault("class", "form-control")

        self.fields["is_active"].widget.attrs.setdefault("class", "form-check-input")


#<------------------------------------  T E A M   F O R M   ---------------------------->


class TeamForm(forms.ModelForm):
    """Business-scoped Team form."""

    class Meta:
        model = Team
        fields = [
            "name",
            "is_active",
            "sort_order",
        ]

    def __init__(self, *args, **kwargs):
        self.business = kwargs.pop("business", None)
        super().__init__(*args, **kwargs)

        if not self.business:
            raise ValueError("TeamForm requires business=...")

        for name, field in self.fields.items():
            widget = field.widget
            if hasattr(widget, "attrs"):
                if getattr(widget, "input_type", "") != "checkbox":
                    widget.attrs.setdefault("class", "form-control")

        self.fields["is_active"].widget.attrs.setdefault("class", "form-check-input")


# <------------------------------------  S U B - C A T E G O R Y   F O R M  ---------------------------->


class SubCategoryForm(forms.ModelForm):
    """Business-scoped SubCategory form."""

    class Meta:
        model = SubCategory
        fields = [
            "category",
            "name",
            "slug",
            "is_active",
            "sort_order",

            "book_enabled",
            "tax_enabled",
            "schedule_c_line",
            "deduction_rule",
            "account_type",

            "is_1099_reportable_default",
            "is_capitalizable",

            "requires_contact",
            "contact_role",
            "requires_receipt",
            "requires_team",
            "requires_job",
            "requires_invoice_number",
            "requires_transport",
            "requires_vehicle",
            "requires_asset",
        ]

    def __init__(self, *args, **kwargs):
        self.business = kwargs.pop("business", None)
        super().__init__(*args, **kwargs)

        if not self.business:
            raise ValueError("SubCategoryForm requires business=...")

        self.fields["category"].queryset = (
            Category.objects.filter(business=self.business, is_active=True)
            .order_by("category_type", "sort_order", "name")
        )

        # Default styling
        for name, field in self.fields.items():
            widget = field.widget
            if hasattr(widget, "attrs") and getattr(widget, "input_type", "") != "checkbox":
                widget.attrs.setdefault("class", "form-control")

        checkbox_fields = (
            "is_active",
            "book_enabled",
            "tax_enabled",
            "is_1099_reportable_default",
            "is_capitalizable",
            "requires_contact",
            "requires_receipt",
            "requires_team",
            "requires_job",
            "requires_invoice_number",
            "requires_transport",
            "requires_vehicle",
            "requires_asset",
        )
        for cb in checkbox_fields:
            self.fields[cb].widget.attrs["class"] = "form-check-input"

        select_fields = (
            "category",
            "schedule_c_line",
            "deduction_rule",
            "account_type",
            "contact_role",
        )
        for sf in select_fields:
            self.fields[sf].widget.attrs["class"] = "form-select"

        self.fields["slug"].required = False
        self.fields["slug"].help_text = "Optional. Leave blank to auto-generate."
        self.fields["schedule_c_line"].help_text = "Leave blank to inherit from the parent Category."
        self.fields["contact_role"].help_text = "Only applies when Contact is required."

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        self.helper.layout = Layout(
            HTML('<div class="fw-semibold mb-2">Basics</div>'),
            Div(
                Div(Field("category"), css_class="col-12 col-md-6"),
                Div(Field("name"), css_class="col-12 col-md-6"),
                css_class="row g-3",
            ),
            Div(
                Div(Field("slug"), css_class="col-12 col-md-6"),
                Div(Field("sort_order"), css_class="col-12 col-md-3"),
                Div(Field("is_active"), css_class="col-12 col-md-3 pt-md-4"),
                css_class="row g-3 mt-0",
            ),

            HTML("<hr class='my-4'>"),
            HTML('<div class="fw-semibold mb-2">Reporting</div>'),
            Div(
                Div(Field("book_enabled"), css_class="col-12 col-md-3"),
                Div(Field("tax_enabled"), css_class="col-12 col-md-3"),
                Div(Field("schedule_c_line"), css_class="col-12 col-md-3"),
                Div(Field("deduction_rule"), css_class="col-12 col-md-3"),
                css_class="row g-3",
            ),
            Div(
                Div(Field("account_type"), css_class="col-12 col-md-4"),
                Div(Field("is_1099_reportable_default"), css_class="col-12 col-md-4 pt-md-4"),
                Div(Field("is_capitalizable"), css_class="col-12 col-md-4 pt-md-4"),
                css_class="row g-3 mt-0",
            ),

            HTML("<hr class='my-4'>"),
            HTML('<div class="fw-semibold mb-2">Requirements</div>'),
            Div(
                Div(Field("requires_contact"), css_class="col-12 col-md-3"),
                Div(Field("contact_role"), css_class="col-12 col-md-3"),
                Div(Field("requires_receipt"), css_class="col-12 col-md-3"),
                Div(Field("requires_team"), css_class="col-12 col-md-3"),
                css_class="row g-3",
            ),
            Div(
                Div(Field("requires_job"), css_class="col-12 col-md-3"),
                Div(Field("requires_invoice_number"), css_class="col-12 col-md-3"),
                Div(Field("requires_transport"), css_class="col-12 col-md-3"),
                Div(Field("requires_vehicle"), css_class="col-12 col-md-3"),
                css_class="row g-3 mt-0",
            ),
            Div(
                Div(Field("requires_asset"), css_class="col-12 col-md-3"),
                css_class="row g-3 mt-0",
            ),
        )