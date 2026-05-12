from __future__ import annotations

from django import forms

from assets.models import Asset
from vehicles.models import Vehicle


class AssetForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = [
            "name",
            "asset_type",
            "purchase_date",
            "placed_in_service_date",
            "purchase_price",
            "useful_life_years",
            "depreciation_method",
            "section_179_amount",
            "vehicle",
            "receipt",
            "disposed_date",
            "disposal_price",
            "notes",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g., DJI Air 3S"}),
            "asset_type": forms.Select(attrs={"class": "form-select"}),
            "purchase_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "placed_in_service_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "purchase_price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "useful_life_years": forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "depreciation_method": forms.Select(attrs={"class": "form-select"}),
            "section_179_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "vehicle": forms.Select(attrs={"class": "form-select"}),
            # Hidden input – the template provides a drag/drop UI that triggers this field.
            "receipt": forms.ClearableFileInput(attrs={"class": "d-none", "accept": "image/*,application/pdf"}),
            "disposed_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "disposal_price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 4, "placeholder": "Optional notes..."}),
        }

    def __init__(self, *args, **kwargs):
        business = kwargs.pop("business", None)
        super().__init__(*args, **kwargs)

        # Vehicle dropdown should be business-scoped
        if business is not None:
            self.fields["vehicle"].queryset = Vehicle.objects.filter(business=business).order_by("-is_active", "label")
        else:
            self.fields["vehicle"].queryset = Vehicle.objects.none()

        self.fields["vehicle"].required = False
        self.fields["placed_in_service_date"].required = False
        self.fields["section_179_amount"].required = False
        self.fields["receipt"].required = False
        self.fields["disposed_date"].required = False
        self.fields["disposal_price"].required = False
        self.fields["notes"].required = False
