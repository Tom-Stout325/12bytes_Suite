from __future__ import annotations

from django import forms

from assets.models import Asset, AssetType


class AssetTypeForm(forms.ModelForm):
    class Meta:
        model = AssetType
        fields = ["name", "is_active", "sort_order"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g., Drone, Controller"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "sort_order": forms.NumberInput(attrs={"class": "form-control", "min": "0"}),
        }


class AssetForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = [
            "name",
            "asset_type",
            "is_active",
            "purchase_date",
            "placed_in_service_date",
            "purchase_price",
            "useful_life_years",
            "depreciation_method",
            "section_179_amount",
            "receipt",
            "disposed_date",
            "disposal_price",
            "notes",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g., DJI Air 3S"}),
            "asset_type": forms.Select(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "purchase_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "placed_in_service_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "purchase_price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "useful_life_years": forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "depreciation_method": forms.Select(attrs={"class": "form-select"}),
            "section_179_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            # Hidden input – the template provides a drag/drop UI that triggers this field.
            "receipt": forms.ClearableFileInput(attrs={"class": "d-none", "accept": "image/*,application/pdf"}),
            "disposed_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "disposal_price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 4, "placeholder": "Optional notes..."}),
        }

    def __init__(self, *args, **kwargs):
        business = kwargs.pop("business", None)
        super().__init__(*args, **kwargs)

        if business is not None:
            type_qs = AssetType.objects.filter(business=business, is_active=True).order_by("sort_order", "name")
            if self.instance and self.instance.pk and self.instance.asset_type_id:
                type_qs = AssetType.objects.filter(business=business).filter(
                    pk__in=list(type_qs.values_list("pk", flat=True)) + [self.instance.asset_type_id]
                ).order_by("sort_order", "name")
            self.fields["asset_type"].queryset = type_qs
        else:
            self.fields["asset_type"].queryset = AssetType.objects.none()

        self.fields["placed_in_service_date"].required = False
        self.fields["section_179_amount"].required = False
        self.fields["receipt"].required = False
        self.fields["disposed_date"].required = False
        self.fields["disposal_price"].required = False
        self.fields["notes"].required = False
