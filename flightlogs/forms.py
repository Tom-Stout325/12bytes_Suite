from __future__ import annotations

from django import forms
from django.conf import settings

from pilot.models import PilotProfile

from .models import FlightLog


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(item, initial) for item in data]
        return [single_clean(data, initial)]


class FlightLogCSVUploadForm(forms.Form):
    csv_file = forms.FileField(
        label="Upload Flight Log CSV",
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".csv,text/csv"}),
    )

    def clean_csv_file(self):
        f = self.cleaned_data["csv_file"]
        name = (getattr(f, "name", "") or "").lower()
        if name and not name.endswith(".csv"):
            raise forms.ValidationError("Please upload a .csv file.")
        return f


class FlightLogDJIUploadForm(forms.Form):
    pilot = forms.ModelChoiceField(
        queryset=PilotProfile.objects.none(),
        empty_label="Select a pilot",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    dji_file = MultipleFileField(
        label="DJI FlightRecord .txt files",
        widget=MultipleFileInput(
            attrs={
                "class": "form-control",
                "accept": ".txt,text/plain,application/octet-stream",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        business = kwargs.pop("business", None)
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        pilots = PilotProfile.objects.none()
        if business is not None:
            pilots = PilotProfile.objects.filter(business=business).select_related("user")
        self.fields["pilot"].queryset = pilots
        if not self.is_bound and user is not None:
            current = list(pilots.filter(user=user)[:2])
            if len(current) == 1:
                self.initial["pilot"] = current[0]
        self.max_files = settings.DJI_BULK_MAX_FILES
        self.fields["dji_file"].help_text = (
            f"Choose up to {self.max_files} files. "
            f"Maximum {settings.DJI_UPLOAD_MAX_BYTES // (1024 * 1024)} MB per file and "
            f"{settings.DJI_BULK_MAX_TOTAL_BYTES // (1024 * 1024)} MB total."
        )

    def clean_dji_file(self):
        uploads = self.cleaned_data["dji_file"]
        if len(uploads) > settings.DJI_BULK_MAX_FILES:
            raise forms.ValidationError(
                f"Select no more than {settings.DJI_BULK_MAX_FILES} DJI flight records per batch."
            )
        total_size = 0
        for uploaded in uploads:
            name = (getattr(uploaded, "name", "") or "").lower()
            if not name.endswith(".txt"):
                raise forms.ValidationError("Every DJI flight record must be a .txt file.")
            if uploaded.size > settings.DJI_UPLOAD_MAX_BYTES:
                maximum = settings.DJI_UPLOAD_MAX_BYTES // (1024 * 1024)
                raise forms.ValidationError(
                    f"Each DJI flight record must be {maximum} MB or smaller."
                )
            if uploaded.size == 0:
                raise forms.ValidationError(f"{uploaded.name or 'A selected file'} is empty.")
            total_size += uploaded.size
        if total_size > settings.DJI_BULK_MAX_TOTAL_BYTES:
            maximum = settings.DJI_BULK_MAX_TOTAL_BYTES // (1024 * 1024)
            raise forms.ValidationError(
                f"The selected DJI files must total {maximum} MB or less."
            )
        return uploads


class FlightLogForm(forms.ModelForm):
    class Meta:
        model = FlightLog
        exclude = (
            "business",
            "aircraft_model",
            "pilot",
            "equipment",
            "rc_serial",
            "camera_serial",
            "battery_cycle_count",
            "minimum_cell_voltage_v",
            "maximum_cell_voltage_v",
            "battery_life_raw",
            "maximum_vertical_speed_mps",
            "maximum_satellites",
            "minimum_airborne_satellites",
            "minimum_airborne_gps_level",
            "flight_modes",
            "dji_warnings",
            "dji_serious_warnings",
            "dji_tips",
        )
        widgets = {
            "flight_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "takeoff_datetime": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": "form-control"},
                format="%Y-%m-%dT%H:%M",
            ),
            "landing_time": forms.TimeInput(attrs={"type": "time", "class": "form-control"}),
            "flight_description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "takeoff_datetime" in self.fields:
            self.fields["takeoff_datetime"].input_formats = ["%Y-%m-%dT%H:%M"]
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.RadioSelect, forms.CheckboxSelectMultiple)):
                continue
            css = widget.attrs.get("class", "")
            if "form-control" not in css and "form-select" not in css:
                widget.attrs["class"] = (css + " form-control").strip()
