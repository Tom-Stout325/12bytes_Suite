from __future__ import annotations

from django import forms

from .models import DroneIncidentReport, GeneralDocument, SOPDocument


class DateInput(forms.DateInput):
    input_type = "date"


class DroneIncidentReportForm(forms.ModelForm):
    class Meta:
        model = DroneIncidentReport
        fields = [
            "report_date", "reported_by", "contact", "role",
            "event_date", "event_time", "location", "event_type", "description",
            "injuries", "injury_details", "damage", "damage_cost", "damage_desc",
            "drone_model", "registration", "controller", "payload", "battery",
            "weather", "wind", "temperature", "lighting",
            "witnesses", "witness_details",
            "emergency", "agency_response", "scene_action", "faa_report", "faa_ref",
            "cause", "notes", "signature", "sign_date",
        ]
        widgets = {
            "report_date": DateInput(attrs={"class": "form-control"}, format="%Y-%m-%d"),
            "event_date": DateInput(attrs={"class": "form-control"}, format="%Y-%m-%d"),
            "event_time": forms.TimeInput(attrs={"class": "form-control", "type": "time"}),
            "sign_date": DateInput(attrs={"class": "form-control"}, format="%Y-%m-%d"),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "injury_details": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "damage_desc": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "witness_details": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "agency_response": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "scene_action": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "cause": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "damage_cost": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.CheckboxInput,)):
                field.widget.attrs.setdefault("class", "form-check-input")
            else:
                existing = field.widget.attrs.get("class", "")
                if "form-control" not in existing and "form-select" not in existing:
                    field.widget.attrs["class"] = f"{existing} form-control".strip()

        optional_fields = [
            "injury_details", "damage_cost", "damage_desc", "controller", "payload", "battery",
            "weather", "wind", "temperature", "lighting", "witness_details", "agency_response",
            "scene_action", "faa_ref", "cause", "notes",
        ]
        for field_name in optional_fields:
            self.fields[field_name].required = False


class SOPDocumentForm(forms.ModelForm):
    class Meta:
        model = SOPDocument
        fields = ["title", "description", "file"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "file": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }


class GeneralDocumentForm(forms.ModelForm):
    class Meta:
        model = GeneralDocument
        fields = ["title", "category", "description", "file"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "category": forms.Select(attrs={"class": "form-select"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "file": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }
