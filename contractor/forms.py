from __future__ import annotations

from datetime import date
from django import forms

from ledger.models import Contact

from .models import ContractorW9Submission


class ContractorYearForm(forms.Form):
    year = forms.ChoiceField(choices=[], required=True)

    def __init__(self, *args, year_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        if year_choices:
            self.fields["year"].choices = [(y, y) for y in year_choices]
        self.fields["year"].widget.attrs.update({"class": "form-select form-select-sm"})


class W9PortalForm(forms.Form):
    full_name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    business_name = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    address1 = forms.CharField(max_length=255, widget=forms.TextInput(attrs={"class": "form-control"}))
    address2 = forms.CharField(max_length=255, required=False, widget=forms.TextInput(attrs={"class": "form-control"}))
    city = forms.CharField(max_length=128, widget=forms.TextInput(attrs={"class": "form-control"}))
    state = forms.CharField(max_length=2, widget=forms.TextInput(attrs={"class": "form-control", "maxlength": "2"}))
    zip_code = forms.CharField(max_length=10, widget=forms.TextInput(attrs={"class": "form-control"}))

    entity_type = forms.ChoiceField(
        choices=Contact.ENTITY_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    taxpayer_id_type = forms.ChoiceField(
        choices=[("ssn", "SSN"), ("ein", "EIN")],
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    tin = forms.CharField(
        max_length=11,
        widget=forms.PasswordInput(attrs={"class": "form-control"}, render_value=False),
        help_text="Enter your SSN or EIN. We store only the last 4 digits.",
    )

    upload_w9_document = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
        help_text="Optional: upload a signed W-9 PDF or image.",
    )
    signature_name = forms.CharField(max_length=255, widget=forms.TextInput(attrs={"class": "form-control"}))
    signature_date = forms.DateField(
        initial=date.today,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    signature_data = forms.CharField(required=False, widget=forms.HiddenInput())
    certification_accepted = forms.BooleanField(
        required=True,
        label="I certify that the information provided is correct and may be used to prepare tax reporting forms.",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def clean_state(self):
        val = (self.cleaned_data.get("state") or "").strip().upper()
        if len(val) != 2:
            raise forms.ValidationError("Enter a 2-letter state code.")
        return val

    def clean_tin(self):
        raw = (self.cleaned_data.get("tin") or "").strip()
        digits = "".join(ch for ch in raw if ch.isdigit())
        if len(digits) != 9:
            raise forms.ValidationError("Enter a valid 9-digit SSN/EIN.")
        return digits

    def clean(self):
        cleaned = super().clean()
        signature_name = (cleaned.get("signature_name") or "").strip()
        signature_data = (cleaned.get("signature_data") or "").strip()
        uploaded = cleaned.get("upload_w9_document")
        if signature_name and not signature_data and not uploaded:
            self.add_error("signature_name", "Draw your signature in the signature box or upload a signed W-9 document.")
        return cleaned


class W9ReviewForm(forms.ModelForm):
    verify_and_update_contact = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        label="Mark contractor W-9 as verified and sync reviewed details to the contractor record.",
    )
    replace_contact_w9_document = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
        help_text="Optional: upload or replace the stored W-9 document on the contractor record.",
    )

    class Meta:
        model = ContractorW9Submission
        fields = ["review_status", "review_notes"]
        widgets = {
            "review_status": forms.Select(attrs={"class": "form-select"}),
            "review_notes": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }
