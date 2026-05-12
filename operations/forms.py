from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from ledger.models import Contact

from .models import OpsPlan


class OpsPlanForm(forms.ModelForm):
    class Meta:
        model = OpsPlan
        fields = [
            "event_name",
            "plan_year",
            "start_date",
            "end_date",
            "client",
            "address",
            "pilot_in_command",
            "visual_observers",
            "airspace_class",
            "waivers_required",
            "airport",
            "airport_phone",
            "notes",
            "emergency_procedures",
            "waiver",
            "location_map",
            "client_approval",
            "client_approval_notes",
        ]
        widgets = {
            "event_name": forms.TextInput(attrs={"class": "form-control"}),
            "plan_year": forms.NumberInput(attrs={"class": "form-control", "min": 2000, "max": 2100, "step": 1}),
            "start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "end_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "client": forms.Select(attrs={"class": "form-select"}),
            "address": forms.TextInput(attrs={"class": "form-control"}),
            "pilot_in_command": forms.TextInput(attrs={"class": "form-control"}),
            "visual_observers": forms.TextInput(attrs={"class": "form-control"}),
            "airspace_class": forms.TextInput(attrs={"class": "form-control"}),
            "waivers_required": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "airport": forms.TextInput(attrs={"class": "form-control"}),
            "airport_phone": forms.TextInput(attrs={"class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "emergency_procedures": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "client_approval_notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        self._bound_job = kwargs.pop("job", None)
        self._business = kwargs.pop("business", None)
        super().__init__(*args, **kwargs)

        self.fields["start_date"].input_formats = ["%Y-%m-%d", "%m/%d/%Y"]
        self.fields["end_date"].input_formats = ["%Y-%m-%d", "%m/%d/%Y"]

        qs = Contact.objects.none()
        if self._business is not None:
            qs = Contact.objects.filter(business=self._business).order_by("display_name")
        self.fields["client"].queryset = qs

        if self._bound_job is not None:
            self.instance.job = self._bound_job
            if self._business is not None:
                self.instance.business = self._business

            if not self.initial.get("event_name") and not getattr(self.instance, "event_name", None):
                self.initial["event_name"] = getattr(self._bound_job, "label", None) or str(self._bound_job)

            if not self.initial.get("client") and not getattr(self.instance, "client_id", None):
                if getattr(self._bound_job, "client_id", None):
                    self.initial["client"] = self._bound_job.client_id

        if not self.instance.pk and not self.initial.get("plan_year"):
            job_year = getattr(self._bound_job, "job_year", None)
            self.initial["plan_year"] = job_year or timezone.now().year

    def clean_plan_year(self):
        year = self.cleaned_data.get("plan_year")
        if not year:
            raise ValidationError("Plan year is required.")
        if year < 2000 or year > 2100:
            raise ValidationError("Please enter a valid year between 2000 and 2100.")
        return year

    def clean_client(self):
        client = self.cleaned_data.get("client")
        if client and self._business is not None and client.business_id != self._business.id:
            raise ValidationError("Client does not belong to this business.")
        return client

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "End date must be after the start date.")

        job = self._bound_job or getattr(self.instance, "job", None)
        plan_year = cleaned.get("plan_year")
        if self._business is not None and job and plan_year:
            qs = OpsPlan.objects.filter(business=self._business, job=job, plan_year=plan_year)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("plan_year", f"An Ops Plan for '{job}' already exists for {plan_year}.")
        return cleaned


class OpsPlanApprovalForm(forms.Form):
    approve = forms.BooleanField(label="I have reviewed and approve this Operations Plan.", required=True)
    full_name = forms.CharField(
        label="Full Name (Digital Signature)",
        max_length=200,
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
