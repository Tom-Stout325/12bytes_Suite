from __future__ import annotations

from django import forms

from .models import FlightLog

DJI_UPLOAD_MAX_BYTES = 100 * 1024 * 1024


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
    dji_file = forms.FileField(
        label="DJI FlightRecord .txt file",
        widget=forms.ClearableFileInput(
            attrs={"class": "form-control", "accept": ".txt,text/plain"}
        ),
        help_text="Maximum file size: 100 MB.",
    )

    def clean_dji_file(self):
        uploaded = self.cleaned_data["dji_file"]
        name = (getattr(uploaded, "name", "") or "").lower()
        if not name.endswith(".txt"):
            raise forms.ValidationError("Please upload a DJI FlightRecord .txt file.")
        if uploaded.size > DJI_UPLOAD_MAX_BYTES:
            raise forms.ValidationError("The DJI flight record must be 100 MB or smaller.")
        if uploaded.size == 0:
            raise forms.ValidationError("The DJI flight record is empty.")
        return uploaded


class FlightLogForm(forms.ModelForm):
    class Meta:
        model = FlightLog
        exclude = (
            "business",
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
