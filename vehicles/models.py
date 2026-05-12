from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from dateutil.relativedelta import relativedelta
from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from core.models import BusinessOwnedModelMixin

ZERO_TENTH = Decimal("0.0")
ZERO_CENTS = Decimal("0.00")
ONE_TENTH = Decimal("0.1")
ONE_HUNDREDTH = Decimal("0.01")


class Vehicle(BusinessOwnedModelMixin):
    label = models.CharField(max_length=120, help_text="What you want to see in dropdowns (e.g., 2018 Ford F-150).")
    year = models.PositiveIntegerField(blank=True, null=True)
    make = models.CharField(max_length=60, blank=True)
    model = models.CharField(max_length=60, blank=True)
    vin_last6 = models.CharField(max_length=6, blank=True)
    plate = models.CharField(max_length=20, blank=True)

    in_service_date = models.DateField(blank=True, null=True, default=None)
    in_service_mileage = models.DecimalField(
        max_digits=10,
        decimal_places=1,
        validators=[MinValueValidator(0)],
        null=True,
        blank=True,
    )
    sold_date = models.DateField(blank=True, null=True, default=None)
    is_business = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "label"]
        constraints = [
            models.UniqueConstraint(fields=["business", "label"], name="uniq_vehicle_business_label"),
        ]

    def __str__(self) -> str:
        return self.label


class VehicleLoan(BusinessOwnedModelMixin):
    vehicle = models.OneToOneField(Vehicle, on_delete=models.CASCADE, related_name="loan")
    purchase_date = models.DateField(help_text="Date the vehicle was purchased.")
    first_payment_date = models.DateField(blank=True, null=True, help_text="Optional first loan payment date.")
    original_loan_amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    annual_interest_rate = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        validators=[MinValueValidator(0)],
        help_text="Annual interest rate as a percent, e.g. 7.2500",
    )
    number_of_payments = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["vehicle__label"]

    def __str__(self) -> str:
        return f"{self.vehicle.label} loan"

    def clean(self):
        super().clean()
        if self.vehicle_id and self.business_id and self.vehicle.business_id != self.business_id:
            raise ValidationError({"vehicle": "Vehicle does not belong to this business."})
        if self.first_payment_date and self.purchase_date and self.first_payment_date < self.purchase_date:
            raise ValidationError({"first_payment_date": "First payment date cannot be earlier than the purchase date."})

    @property
    def payment_start_date(self):
        return self.first_payment_date or self.purchase_date

    @property
    def monthly_interest_rate(self) -> Decimal:
        return (Decimal(str(self.annual_interest_rate)) / Decimal("100") / Decimal("12")).quantize(
            Decimal("0.0000001"),
            rounding=ROUND_HALF_UP,
        )

    @property
    def payment_amount(self) -> Decimal:
        principal = Decimal(str(self.original_loan_amount or ZERO_CENTS))
        periods = int(self.number_of_payments or 0)
        if principal <= ZERO_CENTS or periods <= 0:
            return ZERO_CENTS
        rate = self.monthly_interest_rate
        if rate == 0:
            return (principal / Decimal(periods)).quantize(ONE_HUNDREDTH, rounding=ROUND_HALF_UP)
        numerator = principal * rate
        denominator = Decimal("1") - ((Decimal("1") + rate) ** Decimal(-periods))
        return (numerator / denominator).quantize(ONE_HUNDREDTH, rounding=ROUND_HALF_UP)

    def regenerate_schedule(self):
        self.full_clean()
        self.payments.all().delete()

        balance = Decimal(str(self.original_loan_amount)).quantize(ONE_HUNDREDTH, rounding=ROUND_HALF_UP)
        payment_amount = self.payment_amount
        rate = self.monthly_interest_rate
        payment_date = self.payment_start_date

        rows: list[VehicleLoanPayment] = []
        for payment_no in range(1, int(self.number_of_payments) + 1):
            beginning_balance = balance
            interest_amount = (beginning_balance * rate).quantize(ONE_HUNDREDTH, rounding=ROUND_HALF_UP)
            principal_amount = (payment_amount - interest_amount).quantize(ONE_HUNDREDTH, rounding=ROUND_HALF_UP)
            if payment_no == int(self.number_of_payments) or principal_amount > balance:
                principal_amount = balance
                payment_amount_effective = (principal_amount + interest_amount).quantize(ONE_HUNDREDTH, rounding=ROUND_HALF_UP)
            else:
                payment_amount_effective = payment_amount
            ending_balance = (beginning_balance - principal_amount).quantize(ONE_HUNDREDTH, rounding=ROUND_HALF_UP)
            if ending_balance < ZERO_CENTS:
                ending_balance = ZERO_CENTS

            rows.append(
                VehicleLoanPayment(
                    business_id=self.business_id,
                    loan=self,
                    payment_number=payment_no,
                    payment_date=payment_date,
                    beginning_balance=beginning_balance,
                    payment_amount=payment_amount_effective,
                    principal_amount=principal_amount,
                    interest_amount=interest_amount,
                    ending_balance=ending_balance,
                )
            )

            balance = ending_balance
            payment_date = payment_date + relativedelta(months=1)
            if balance <= ZERO_CENTS:
                break

        VehicleLoanPayment.objects.bulk_create(rows)
        return rows


class VehicleLoanPayment(BusinessOwnedModelMixin):
    loan = models.ForeignKey(VehicleLoan, on_delete=models.CASCADE, related_name="payments")
    payment_number = models.PositiveIntegerField()
    payment_date = models.DateField()
    beginning_balance = models.DecimalField(max_digits=12, decimal_places=2)
    payment_amount = models.DecimalField(max_digits=12, decimal_places=2)
    principal_amount = models.DecimalField(max_digits=12, decimal_places=2)
    interest_amount = models.DecimalField(max_digits=12, decimal_places=2)
    ending_balance = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["payment_date", "payment_number"]
        constraints = [
            models.UniqueConstraint(fields=["loan", "payment_number"], name="uniq_vehicle_loan_payment_number"),
        ]
        indexes = [
            models.Index(fields=["business", "payment_date"]),
        ]

    def clean(self):
        super().clean()
        if self.loan_id and self.business_id and self.loan.business_id != self.business_id:
            raise ValidationError({"loan": "Loan does not belong to this business."})

    def __str__(self) -> str:
        return f"{self.loan.vehicle.label} payment #{self.payment_number}"


class VehicleYear(BusinessOwnedModelMixin):
    class DeductionMethod(models.TextChoices):
        STANDARD_MILEAGE = "standard_mileage", "Standard mileage"
        ACTUAL_EXPENSES = "actual_expenses", "Actual expenses"

    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="years")
    year = models.PositiveIntegerField(default=timezone.now().year)

    odometer_start = models.DecimalField(
        max_digits=10,
        decimal_places=1,
        validators=[MinValueValidator(0)],
        help_text="Odometer reading on Jan 1 (or start of year).",
    )
    odometer_end = models.DecimalField(
        max_digits=10,
        decimal_places=1,
        validators=[MinValueValidator(0)],
        null=True,
        blank=True,
        help_text="Odometer reading on Dec 31 (or end of year).",
    )
    standard_mileage_rate = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text="Optional standard mileage rate for this year.",
    )
    annual_interest_paid = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text="Optional manual entry for total vehicle-loan interest paid during this calendar year.",
    )

    deduction_method = models.CharField(
        max_length=20,
        choices=DeductionMethod.choices,
        default=DeductionMethod.STANDARD_MILEAGE,
    )

    is_locked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-year", "vehicle__label"]
        constraints = [
            models.UniqueConstraint(fields=["business", "vehicle", "year"], name="uniq_vehicle_year_per_business"),
        ]

    def clean(self):
        super().clean()

        if self.vehicle_id and self.business_id and self.vehicle.business_id != self.business_id:
            raise ValidationError({"vehicle": "Vehicle does not belong to this business."})

        if self.odometer_end is not None and self.odometer_end < self.odometer_start:
            raise ValidationError({"odometer_end": "End odometer must be ≥ start odometer."})

        if self.vehicle_id and self.business_id and self.year:
            prior = (
                VehicleYear.objects.filter(
                    business_id=self.business_id,
                    vehicle_id=self.vehicle_id,
                    year__lt=self.year,
                )
                .exclude(pk=self.pk)
                .order_by("-year")
                .first()
            )
            if prior and prior.odometer_end is not None and self.odometer_start < prior.odometer_end:
                raise ValidationError({
                    "odometer_start": f"Start odometer cannot be lower than the prior year ending odometer ({prior.odometer_end})."
                })

            nxt = (
                VehicleYear.objects.filter(
                    business_id=self.business_id,
                    vehicle_id=self.vehicle_id,
                    year__gt=self.year,
                )
                .exclude(pk=self.pk)
                .order_by("year")
                .first()
            )
            if nxt and nxt.odometer_start is not None and self.odometer_end is not None and self.odometer_end > nxt.odometer_start:
                raise ValidationError({
                    "odometer_end": f"End odometer cannot exceed the next year starting odometer ({nxt.odometer_start})."
                })

        if self.total_miles is not None and self.business_miles > self.total_miles:
            raise ValidationError({
                "odometer_end": "Business miles cannot exceed total annual miles. Check odometer readings or mileage logs."
            })

    @property
    def total_miles(self) -> Decimal | None:
        if self.odometer_end is None:
            return None
        return (self.odometer_end - self.odometer_start).quantize(ONE_TENTH, rounding=ROUND_HALF_UP)

    def _effective_business_id(self) -> int | None:
        if self.business_id:
            return self.business_id
        if self.vehicle_id:
            return self.vehicle.business_id
        return None

    @property
    def logged_miles_total(self) -> Decimal:
        business_id = self._effective_business_id()
        if not self.vehicle_id or not business_id:
            return ZERO_TENTH
        total = self.vehicle.miles_entries.filter(
            business_id=business_id,
            date__year=self.year,
        ).aggregate(total=Sum("total"))["total"] or ZERO_TENTH
        return Decimal(str(total)).quantize(ONE_TENTH, rounding=ROUND_HALF_UP)

    @property
    def business_miles(self) -> Decimal:
        business_id = self._effective_business_id()
        if not self.vehicle_id or not business_id:
            return ZERO_TENTH
        total = self.vehicle.miles_entries.filter(
            business_id=business_id,
            date__year=self.year,
            mileage_type=VehicleMiles.MileageType.BUSINESS,
        ).aggregate(total=Sum("total"))["total"] or ZERO_TENTH
        return Decimal(str(total)).quantize(ONE_TENTH, rounding=ROUND_HALF_UP)

    @property
    def reimbursed_miles(self) -> Decimal:
        business_id = self._effective_business_id()
        if not self.vehicle_id or not business_id:
            return ZERO_TENTH
        total = self.vehicle.miles_entries.filter(
            business_id=business_id,
            date__year=self.year,
            mileage_type=VehicleMiles.MileageType.REIMBURSED,
        ).aggregate(total=Sum("total"))["total"] or ZERO_TENTH
        return Decimal(str(total)).quantize(ONE_TENTH, rounding=ROUND_HALF_UP)

    @property
    def other_miles(self) -> Decimal | None:
        total = self.total_miles
        if total is None:
            return None
        other = total - self.business_miles
        if other < ZERO_TENTH:
            other = ZERO_TENTH
        return other.quantize(ONE_TENTH, rounding=ROUND_HALF_UP)

    @property
    def business_use_pct(self) -> Decimal | None:
        total = self.total_miles
        if total in (None, ZERO_TENTH):
            return None
        pct = (self.business_miles / total) * Decimal("100")
        return pct.quantize(ONE_HUNDREDTH, rounding=ROUND_HALF_UP)

    @property
    def actual_expenses_total(self) -> Decimal:
        Transaction = None
        for app_label in ("ledger", "money"):
            try:
                Transaction = apps.get_model(app_label, "Transaction")
                break
            except LookupError:
                continue
        if Transaction is None:
            return ZERO_CENTS

        business_id = self._effective_business_id()
        if not business_id or not self.vehicle_id:
            return ZERO_CENTS

        qs = Transaction.objects.filter(
            business_id=business_id,
            vehicle_id=self.vehicle_id,
            date__year=self.year,
        )
        if hasattr(Transaction, "TransactionType"):
            qs = qs.filter(trans_type=Transaction.TransactionType.EXPENSE)

        total = ZERO_CENTS
        for tx in qs:
            amount = Decimal(str(getattr(tx, "amount", ZERO_CENTS) or ZERO_CENTS))
            if getattr(tx, "is_refund", False):
                total -= amount
            else:
                total += amount
        if total < ZERO_CENTS:
            total = ZERO_CENTS
        return total.quantize(ONE_HUNDREDTH, rounding=ROUND_HALF_UP)

    @property
    def generated_interest_paid(self) -> Decimal:
        business_id = self._effective_business_id()
        if not business_id or not self.vehicle_id:
            return ZERO_CENTS
        total = VehicleLoanPayment.objects.filter(
            business_id=business_id,
            loan__vehicle_id=self.vehicle_id,
            payment_date__year=self.year,
        ).aggregate(total=Sum("interest_amount"))["total"] or ZERO_CENTS
        return Decimal(str(total)).quantize(ONE_HUNDREDTH, rounding=ROUND_HALF_UP)

    @property
    def effective_annual_interest_paid(self) -> Decimal:
        if self.annual_interest_paid is not None:
            return Decimal(str(self.annual_interest_paid)).quantize(ONE_HUNDREDTH, rounding=ROUND_HALF_UP)
        return self.generated_interest_paid

    @property
    def interest_source_label(self) -> str:
        if self.annual_interest_paid is not None:
            return "Manual"
        if self.generated_interest_paid > ZERO_CENTS:
            return "Amortization"
        return "—"

    @property
    def business_interest_amount(self) -> Decimal:
        pct = self.business_use_pct
        if pct is None:
            return ZERO_CENTS
        value = self.effective_annual_interest_paid * (pct / Decimal("100"))
        return value.quantize(ONE_HUNDREDTH, rounding=ROUND_HALF_UP)

    @property
    def actual_expenses_with_interest_total(self) -> Decimal:
        total = self.actual_expenses_total + self.business_interest_amount
        return total.quantize(ONE_HUNDREDTH, rounding=ROUND_HALF_UP)

    @property
    def standard_mileage_deduction(self) -> Decimal | None:
        if self.standard_mileage_rate is None:
            return None
        value = self.business_miles * Decimal(str(self.standard_mileage_rate))
        return value.quantize(ONE_HUNDREDTH, rounding=ROUND_HALF_UP)

    @property
    def deduction_amount(self) -> Decimal | None:
        if self.deduction_method == self.DeductionMethod.ACTUAL_EXPENSES:
            return self.actual_expenses_with_interest_total
        return self.standard_mileage_deduction

    @property
    def missing_data_flags(self) -> list[str]:
        flags: list[str] = []
        if self.odometer_end is None:
            flags.append("Missing ending odometer")
        if self.total_miles is not None and self.business_miles > self.total_miles:
            flags.append("Business miles exceed annual miles")
        pct = self.business_use_pct
        if pct is not None and pct > Decimal("95"):
            flags.append("Business use over 95%")
        if self.business_miles == ZERO_TENTH:
            flags.append("No business miles logged")
        if self.deduction_method == self.DeductionMethod.STANDARD_MILEAGE and self.standard_mileage_rate is None:
            flags.append("Standard mileage rate missing")
        if self.deduction_method == self.DeductionMethod.ACTUAL_EXPENSES and self.effective_annual_interest_paid == ZERO_CENTS:
            flags.append("No annual interest entered or generated")
        return flags

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class VehicleMiles(BusinessOwnedModelMixin):
    class MileageType(models.TextChoices):
        BUSINESS = "business", "Business"
        COMMUTING = "commuting", "Commuting"
        OTHER = "other", "Other"
        REIMBURSED = "reimbursed", "Reimbursed"

    date = models.DateField(default=timezone.now)
    begin = models.DecimalField(max_digits=10, decimal_places=1, null=True, blank=True, validators=[MinValueValidator(0)])
    end = models.DecimalField(max_digits=10, decimal_places=1, null=True, blank=True, validators=[MinValueValidator(0)])
    total = models.DecimalField(max_digits=10, decimal_places=1, null=True, blank=True, editable=False)

    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="miles_entries")
    job = models.ForeignKey("ledger.Job", on_delete=models.PROTECT, related_name="mileage_entries", null=True, blank=True)
    invoice = models.ForeignKey("invoices.Invoice", on_delete=models.PROTECT, related_name="mileage_entries", null=True, blank=True)
    mileage_type = models.CharField(max_length=20, choices=MileageType.choices, default=MileageType.BUSINESS)
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["business", "date"]),
            models.Index(fields=["business", "vehicle"]),
            models.Index(fields=["business", "mileage_type"]),
        ]

    def clean(self):
        super().clean()

        if self.vehicle_id and self.business_id and self.vehicle.business_id != self.business_id:
            raise ValidationError({"vehicle": "Vehicle does not belong to this business."})
        if self.job_id and self.business_id and self.job.business_id != self.business_id:
            raise ValidationError({"job": "Job does not belong to this business."})
        if self.invoice_id and self.business_id and self.invoice.business_id != self.business_id:
            raise ValidationError({"invoice": "Invoice does not belong to this business."})
        if self.invoice_id and self.job_id and self.invoice.job_id and self.invoice.job_id != self.job_id:
            raise ValidationError({"invoice": "Selected invoice does not belong to the selected job."})

        if self.begin is not None and self.end is not None:
            if self.end < self.begin:
                raise ValidationError({"end": "End mileage must be ≥ begin mileage."})
            self.total = (self.end - self.begin).quantize(ONE_TENTH, rounding=ROUND_HALF_UP)
        else:
            self.total = None

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
