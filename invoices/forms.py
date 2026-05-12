from __future__ import annotations

from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Div, Field

from ledger.models import Job, Contact, SubCategory, Team
from .models import Invoice, InvoiceItem, validate_manual_invoice_number
from .services import get_next_invoice_number_preview


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = [
            "invoice_number",
            "issue_date",
            "due_date",
            "contact",
            "job",
            "team",
            "location",
            "paid_date",
            "status",
            "memo",
          
        ]

        widgets = {
            "issue_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "due_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "paid_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "memo": forms.Textarea(attrs={"rows": 3}),
       
        }

    def __init__(self, *args, business, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)

        self.fields["contact"].queryset = Contact.objects.filter(business=business, is_customer=True).order_by("display_name")
        self.fields["job"].queryset = Job.objects.filter(business=business).order_by("-job_year", "job_number", "label")
        self.fields["team"].queryset = Team.objects.filter(business=business, is_active=True).order_by("sort_order", "name")
        self.fields["status"].disabled = True
        self.fields["paid_date"].disabled = True  # controlled by Mark Paid

        # Draft-only edits enforced by views; still show invoice_number placeholder
        self.fields["invoice_number"].required = False
        preview = get_next_invoice_number_preview(
            business=business,
            issue_date=self.initial.get("issue_date") or getattr(self.instance, "issue_date", None),
        )
        self.fields["invoice_number"].widget.attrs.setdefault("placeholder", preview)
        self.fields["invoice_number"].help_text = (
            "Next invoice number (preview). Leave blank to auto-assign when you save, "
            "or manually enter a higher number."
        )

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Div(
                Div(Field("invoice_number"), css_class="col-12 col-md-4"),
                Div(Field("issue_date"), css_class="col-6 col-md-4"),
                Div(Field("due_date"), css_class="col-6 col-md-4"),
                css_class="row g-2",
            ),
            Div(
                Div(Field("contact"), css_class="col-12 col-md-6"),
                Div(Field("job"), css_class="col-12 col-md-6"),
                css_class="row g-2",
            ),
            Div(
                Div(Field("team"), css_class="col-12 col-md-4"),
                Div(Field("location"), css_class="col-12 col-md-4"),
                Div(Field("status"), css_class="col-6 col-md-2"),
                Div(Field("paid_date"), css_class="col-6 col-md-2"),
                css_class="row g-2",
            ),
            Field("memo"),
   
        )

    def clean_invoice_number(self):
        num = (self.cleaned_data.get("invoice_number") or "").strip()
        if not num:
            return ""
        issue_date = self.cleaned_data.get("issue_date") or self.instance.issue_date
        validate_manual_invoice_number(
            business=self.business,
            issue_date=issue_date,
            invoice_number=num,
            exclude_pk=self.instance.pk if self.instance and self.instance.pk else None,
        )
        return num


class InvoiceItemForm(forms.ModelForm):
    class Meta:
        model = InvoiceItem
        fields = ["description", "subcategory", "qty", "unit_price", "sort_order"]

    def __init__(self, *args, business, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)

        self.fields["subcategory"].queryset = SubCategory.objects.filter(business=business).order_by("name")

        # Bootstrap-friendly widgets
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.TextInput):
                field.widget.attrs.setdefault("class", "form-control")
            elif isinstance(field.widget, forms.NumberInput):
                field.widget.attrs.setdefault("class", "form-control")
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "form-select")
