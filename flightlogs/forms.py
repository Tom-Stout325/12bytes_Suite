from __future__ import annotations

from django import forms

from .models import FlightLog


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


class FlightLogForm(forms.ModelForm):
    class Meta:
        model = FlightLog
        exclude = ("business",)
        widgets = {
            "flight_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "landing_time": forms.TimeInput(attrs={"type": "time", "class": "form-control"}),
            "flight_description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.RadioSelect, forms.CheckboxSelectMultiple)):
                continue
            css = widget.attrs.get("class", "")
            if "form-control" not in css and "form-select" not in css:
                widget.attrs["class"] = (css + " form-control").strip()
