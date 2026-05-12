from __future__ import annotations

from django import forms
from django.utils import timezone

from invoices.models import Invoice
from ledger.models import Job
from vehicles.models import Vehicle, VehicleLoan, VehicleMiles, VehicleYear


class VehicleForm(forms.ModelForm):
    create_current_year_record = forms.BooleanField(
        required=False,
        initial=True,
        label="Create current-year annual record after save",
    )

    class Meta:
        model = Vehicle
        fields = [
            "label",
            "year",
            "make",
            "model",
            "vin_last6",
            "plate",
            "in_service_date",
            "sold_date",
            "is_business",
            "is_active",
            "sort_order",
        ]
        widgets = {
            "in_service_date": forms.DateInput(attrs={"type": "date"}),
            "sold_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "form-check-input")
            else:
                field.widget.attrs.setdefault("class", "form-control")
        if self.instance and self.instance.pk:
            self.fields["create_current_year_record"].initial = False


class VehicleYearForm(forms.ModelForm):
    loan_purchase_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))
    loan_first_payment_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))
    loan_amount = forms.DecimalField(required=False, decimal_places=2, max_digits=12, min_value=0, widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}))
    loan_interest_rate = forms.DecimalField(required=False, decimal_places=4, max_digits=7, min_value=0, widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.0001", "placeholder": "e.g. 7.2500"}))
    loan_number_of_payments = forms.IntegerField(required=False, min_value=1, widget=forms.NumberInput(attrs={"class": "form-control", "step": "1"}))
    regenerate_amortization = forms.BooleanField(required=False, initial=True, label="Generate or refresh amortization schedule")

    class Meta:
        model = VehicleYear
        fields = [
            "vehicle",
            "year",
            "odometer_start",
            "odometer_end",
            "standard_mileage_rate",
            "annual_interest_paid",
            "deduction_method",
            "is_locked",
        ]
        widgets = {
            "year": forms.NumberInput(attrs={"class": "form-control"}),
            "odometer_start": forms.NumberInput(attrs={"class": "form-control", "step": "0.1"}),
            "odometer_end": forms.NumberInput(attrs={"class": "form-control", "step": "0.1"}),
            "standard_mileage_rate": forms.NumberInput(attrs={"class": "form-control", "step": "0.001", "placeholder": "e.g. 0.670"}),
            "annual_interest_paid": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "Optional manual yearly interest total"}),
            "deduction_method": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        self.business = kwargs.pop("business", None)
        super().__init__(*args, **kwargs)

        if self.business and not self.instance.business_id:
            self.instance.business = self.business

        if self.business:
            self.fields["vehicle"].queryset = Vehicle.objects.filter(business=self.business).order_by("sort_order", "label")
        self.fields["vehicle"].widget.attrs.update({"class": "form-select"})
        self.fields["is_locked"].widget.attrs.update({"class": "form-check-input"})
        self.fields["regenerate_amortization"].widget.attrs.update({"class": "form-check-input"})

        vehicle_id = self.instance.vehicle_id or self.initial.get("vehicle") or self.data.get("vehicle")
        if not self.instance.pk:
            initial_year = self.initial.get("year") or self.data.get("year") or timezone.localdate().year
            try:
                initial_vehicle = int(vehicle_id) if vehicle_id else None
                initial_year = int(initial_year)
            except (TypeError, ValueError):
                initial_vehicle = None
                initial_year = timezone.localdate().year

            if self.business and initial_vehicle:
                prior = (
                    VehicleYear.objects.filter(business=self.business, vehicle_id=initial_vehicle, year__lt=initial_year)
                    .order_by("-year")
                    .first()
                )
                if prior:
                    self.fields["odometer_start"].initial = prior.odometer_end
                    if prior.standard_mileage_rate is not None:
                        self.fields["standard_mileage_rate"].initial = prior.standard_mileage_rate

        loan = None
        try:
            if self.instance and self.instance.vehicle_id:
                loan = self.instance.vehicle.loan
            elif vehicle_id:
                loan = VehicleLoan.objects.filter(vehicle_id=vehicle_id).first()
        except VehicleLoan.DoesNotExist:
            loan = None

        if loan:
            self.fields["loan_purchase_date"].initial = loan.purchase_date
            self.fields["loan_first_payment_date"].initial = loan.first_payment_date
            self.fields["loan_amount"].initial = loan.original_loan_amount
            self.fields["loan_interest_rate"].initial = loan.annual_interest_rate
            self.fields["loan_number_of_payments"].initial = loan.number_of_payments
            self.fields["regenerate_amortization"].initial = False

    def clean(self):
        cleaned = super().clean()

        vehicle = cleaned.get("vehicle")
        year = cleaned.get("year")
        if self.business and vehicle and year:
            duplicate_qs = VehicleYear.objects.filter(
                business=self.business,
                vehicle=vehicle,
                year=year,
            )
            if self.instance.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)
            if duplicate_qs.exists():
                self.add_error("vehicle", "An annual record for this vehicle and year already exists.")
                self.add_error("year", "Choose a different year or edit the existing annual record.")

        loan_amount = cleaned.get("loan_amount")
        loan_purchase_date = cleaned.get("loan_purchase_date")
        loan_interest_rate = cleaned.get("loan_interest_rate")
        loan_number_of_payments = cleaned.get("loan_number_of_payments")

        any_loan_terms = any(value not in (None, "") for value in [
            loan_amount,
            loan_purchase_date,
            loan_interest_rate,
            loan_number_of_payments,
            cleaned.get("loan_first_payment_date"),
        ])
        if any_loan_terms:
            required_missing = []
            if loan_purchase_date in (None, ""):
                required_missing.append("purchase date")
            if loan_amount in (None, ""):
                required_missing.append("loan amount")
            if loan_interest_rate in (None, ""):
                required_missing.append("interest rate")
            if loan_number_of_payments in (None, ""):
                required_missing.append("number of payments")
            if required_missing:
                raise forms.ValidationError(
                    "To generate amortization, complete: " + ", ".join(required_missing) + "."
                )
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.business and not instance.business_id:
            instance.business = self.business

        if commit:
            instance.save()
            self.save_m2m()
            self._save_loan(instance)
        return instance

    def _save_loan(self, instance: VehicleYear):
        loan_amount = self.cleaned_data.get("loan_amount")
        loan_purchase_date = self.cleaned_data.get("loan_purchase_date")
        loan_interest_rate = self.cleaned_data.get("loan_interest_rate")
        loan_number_of_payments = self.cleaned_data.get("loan_number_of_payments")
        loan_first_payment_date = self.cleaned_data.get("loan_first_payment_date")

        any_loan_terms = any(value not in (None, "") for value in [
            loan_amount,
            loan_purchase_date,
            loan_interest_rate,
            loan_number_of_payments,
            loan_first_payment_date,
        ])
        if not any_loan_terms:
            return

        loan, _ = VehicleLoan.objects.get_or_create(
            business=instance.business,
            vehicle=instance.vehicle,
            defaults={
                "purchase_date": loan_purchase_date,
                "first_payment_date": loan_first_payment_date,
                "original_loan_amount": loan_amount,
                "annual_interest_rate": loan_interest_rate,
                "number_of_payments": loan_number_of_payments,
            },
        )
        changed = False
        for attr, value in {
            "purchase_date": loan_purchase_date,
            "first_payment_date": loan_first_payment_date,
            "original_loan_amount": loan_amount,
            "annual_interest_rate": loan_interest_rate,
            "number_of_payments": loan_number_of_payments,
        }.items():
            if getattr(loan, attr) != value:
                setattr(loan, attr, value)
                changed = True
        if changed:
            loan.save()
        if self.cleaned_data.get("regenerate_amortization") or changed or not loan.payments.exists():
            loan.regenerate_schedule()


class VehicleMilesForm(forms.ModelForm):
    class Meta:
        model = VehicleMiles
        fields = ["date", "vehicle", "mileage_type", "begin", "end", "job", "invoice", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "vehicle": forms.Select(attrs={"class": "form-select"}),
            "mileage_type": forms.Select(attrs={"class": "form-select"}),
            "begin": forms.NumberInput(attrs={"class": "form-control", "step": "0.1"}),
            "end": forms.NumberInput(attrs={"class": "form-control", "step": "0.1"}),
            "job": forms.Select(attrs={"class": "form-select"}),
            "invoice": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.TextInput(attrs={"class": "form-control", "placeholder": "Optional trip note"}),
        }

    def __init__(self, *args, **kwargs):
        business = kwargs.pop("business", None)
        self.business = business
        super().__init__(*args, **kwargs)

        if business:
            vehicle_qs = Vehicle.objects.filter(business=business).order_by("sort_order", "label")
            if not (self.instance and self.instance.pk):
                vehicle_qs = vehicle_qs.filter(is_active=True)
            self.fields["vehicle"].queryset = vehicle_qs
            self.fields["job"].queryset = Job.objects.filter(business=business).order_by("-is_active", "-job_year", "job_number", "label")
            self.fields["invoice"].queryset = Invoice.objects.filter(business=business).order_by("-issue_date", "-id")

        self.fields["vehicle"].required = True
        self.fields["vehicle"].empty_label = "Select a vehicle"
        self.fields["vehicle"].help_text = "Required. Mileage entries must be tied to a vehicle, not an asset."
        self.fields["job"].required = False
        self.fields["invoice"].required = False

        vehicle_id = self.initial.get("vehicle") or self.data.get("vehicle") or getattr(self.instance, "vehicle_id", None)
        if business and vehicle_id and not self.instance.pk:
            try:
                last_entry = VehicleMiles.objects.filter(business=business, vehicle_id=int(vehicle_id)).order_by("-date", "-id").first()
                if last_entry and last_entry.end is not None and self.fields["begin"].initial in (None, ""):
                    self.fields["begin"].initial = last_entry.end
            except (TypeError, ValueError):
                pass

    def clean_vehicle(self):
        vehicle = self.cleaned_data.get("vehicle")
        if vehicle is None:
            raise forms.ValidationError("Select a vehicle.")
        business = getattr(self, "business", None)
        if business and vehicle.business_id != business.id:
            raise forms.ValidationError("Selected vehicle does not belong to this business.")
        return vehicle


class QuickMileageForm(VehicleMilesForm):
    class Meta(VehicleMilesForm.Meta):
        fields = ["date", "vehicle", "begin", "end", "job", "invoice", "notes"]
