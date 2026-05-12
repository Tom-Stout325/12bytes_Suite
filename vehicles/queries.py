from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from vehicles.models import VehicleYear

ZERO = Decimal("0.0")
ONE_TENTH = Decimal("0.1")
ONE_HUNDREDTH = Decimal("0.01")


@dataclass(frozen=True)
class MileageYearSummary:
    year: int
    vehicle_id: int
    vehicle_year_id: int
    vehicle_label: str
    deduction_method: str
    deduction_method_label: str
    is_locked: bool
    odometer_start: Decimal
    odometer_end: Decimal | None
    total_miles: Decimal | None
    logged_miles_total: Decimal
    business_miles: Decimal
    other_miles: Decimal | None
    business_use_pct: Decimal | None
    standard_mileage_rate: Decimal | None
    standard_mileage_deduction: Decimal | None
    annual_interest_paid: Decimal | None
    generated_interest_paid: Decimal
    effective_annual_interest_paid: Decimal
    interest_source_label: str
    business_interest_amount: Decimal
    actual_expenses_total: Decimal
    actual_expenses_with_interest_total: Decimal
    deduction_amount: Decimal | None
    warnings: list[str]


def _q(value, quantum):
    if value is None:
        return None
    return Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)


def get_yearly_mileage_summary(*, business, vehicle_id: int, year: int) -> MileageYearSummary:
    vy = (
        VehicleYear.objects.filter(business=business, vehicle_id=vehicle_id, year=year)
        .select_related("vehicle")
        .first()
    )
    if not vy:
        raise VehicleYear.DoesNotExist("VehicleYear not found for business/year/vehicle.")

    warnings = list(vy.missing_data_flags)

    return MileageYearSummary(
        year=year,
        vehicle_id=vy.vehicle_id,
        vehicle_year_id=vy.id,
        vehicle_label=vy.vehicle.label,
        deduction_method=vy.deduction_method,
        deduction_method_label=vy.get_deduction_method_display(),
        is_locked=vy.is_locked,
        odometer_start=_q(vy.odometer_start, ONE_TENTH),
        odometer_end=_q(vy.odometer_end, ONE_TENTH),
        total_miles=_q(vy.total_miles, ONE_TENTH),
        logged_miles_total=_q(vy.logged_miles_total, ONE_TENTH) or ZERO,
        business_miles=_q(vy.business_miles, ONE_TENTH) or ZERO,
        other_miles=_q(vy.other_miles, ONE_TENTH),
        business_use_pct=_q(vy.business_use_pct, ONE_HUNDREDTH),
        standard_mileage_rate=_q(vy.standard_mileage_rate, Decimal("0.001")),
        standard_mileage_deduction=_q(vy.standard_mileage_deduction, ONE_HUNDREDTH),
        annual_interest_paid=_q(vy.annual_interest_paid, ONE_HUNDREDTH),
        generated_interest_paid=_q(vy.generated_interest_paid, ONE_HUNDREDTH) or Decimal("0.00"),
        effective_annual_interest_paid=_q(vy.effective_annual_interest_paid, ONE_HUNDREDTH) or Decimal("0.00"),
        interest_source_label=vy.interest_source_label,
        business_interest_amount=_q(vy.business_interest_amount, ONE_HUNDREDTH) or Decimal("0.00"),
        actual_expenses_total=_q(vy.actual_expenses_total, ONE_HUNDREDTH) or Decimal("0.00"),
        actual_expenses_with_interest_total=_q(vy.actual_expenses_with_interest_total, ONE_HUNDREDTH) or Decimal("0.00"),
        deduction_amount=_q(vy.deduction_amount, ONE_HUNDREDTH),
        warnings=warnings,
    )
