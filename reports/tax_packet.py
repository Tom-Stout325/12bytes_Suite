from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Q

from assets.models import Asset
from ledger.models import Transaction
from vehicles.models import VehicleMiles, VehicleYear

from .profit_loss import build_profit_loss_single
from .schedule_c import build_schedule_c_lines


@dataclass(frozen=True)
class TaxPacketOptions:
    include_transaction_detail: bool = False
    include_mileage_detail: bool = False


def is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def selected_year(request) -> int:
    today = date.today()
    try:
        return int(request.GET.get("year") or today.year)
    except (TypeError, ValueError):
        return today.year


def year_choices() -> list[int]:
    today = date.today()
    return list(range(2023, today.year + 1))


def _company_context(request) -> dict:
    business = getattr(request, "business", None)
    company_profile = getattr(business, "company_profile", None) if business else None

    company_name = ""
    if company_profile:
        company_name = getattr(company_profile, "company_name", "") or getattr(company_profile, "name", "") or ""
    if not company_name and business:
        company_name = getattr(business, "name", "") or ""

    logo_src = None
    if company_profile and getattr(company_profile, "logo", None):
        try:
            url = company_profile.logo.url
            logo_src = request.build_absolute_uri(url) if url.startswith("/") else url
        except Exception:
            logo_src = None

    return {
        "business": business,
        "company_profile": company_profile,
        "company_name": company_name,
        "logo_src": logo_src,
    }


def _vehicle_year_rows(*, business, year: int):
    rows = (
        VehicleYear.objects.filter(business=business, year=year)
        .select_related("vehicle")
        .order_by("vehicle__label")
    )

    summary_rows: list[dict] = []
    total_business_miles = Decimal("0.0")
    total_standard_deduction = Decimal("0.00")
    total_actual_expenses = Decimal("0.00")
    total_selected_deduction = Decimal("0.00")

    for row in rows:
        business_miles = row.business_miles or Decimal("0.0")
        standard_deduction = row.standard_mileage_deduction or Decimal("0.00")
        actual_expenses = row.actual_expenses_total or Decimal("0.00")
        deduction_amount = row.deduction_amount or Decimal("0.00")

        summary_rows.append(
            {
                "vehicle_year": row,
                "vehicle": row.vehicle,
                "warnings": list(row.missing_data_flags),
                "business_miles": business_miles,
                "business_use_pct": row.business_use_pct,
                "standard_deduction": standard_deduction,
                "actual_expenses": actual_expenses,
                "deduction_amount": deduction_amount,
            }
        )

        total_business_miles += business_miles
        total_standard_deduction += standard_deduction
        total_actual_expenses += actual_expenses
        total_selected_deduction += deduction_amount

    return {
        "rows": summary_rows,
        "total_business_miles": total_business_miles,
        "total_standard_deduction": total_standard_deduction,
        "total_actual_expenses": total_actual_expenses,
        "total_selected_deduction": total_selected_deduction,
    }


def _section_179_rows(*, business, year: int):
    qs = (
        Asset.objects.filter(business=business)
        .filter(
            Q(placed_in_service_date__year=year) | Q(purchase_date__year=year)
        )
        .filter(
            Q(depreciation_method=Asset.DepreciationMethod.SECTION_179)
            | Q(section_179_amount__gt=0)
        )
        .order_by("placed_in_service_date", "purchase_date", "name")
    )

    rows = []
    total_section_179 = Decimal("0.00")
    for asset in qs:
        amount = asset.section_179_amount or Decimal("0.00")
        rows.append(
            {
                "asset": asset,
                "placed_in_service_date": asset.placed_in_service_date or asset.purchase_date,
                "cost_basis": asset.purchase_price or Decimal("0.00"),
                "section_179_amount": amount,
            }
        )
        total_section_179 += amount

    return {"rows": rows, "total_section_179": total_section_179}


def _transaction_detail_rows(*, business, year: int):
    qs = (
        Transaction.objects.filter(
            business=business,
            date__year=year,
            category__book_reports=True,
            subcategory__book_enabled=True,
        )
        .select_related("category", "subcategory", "vehicle", "job", "contact")
        .order_by("date", "id")
    )

    rows = []
    for tx in qs:
        amount = tx.effective_amount if hasattr(tx, "effective_amount") else (tx.amount or Decimal("0.00"))
        rows.append(
            {
                "date": tx.date,
                "description": tx.description,
                "category": getattr(tx.category, "name", ""),
                "subcategory": getattr(tx.subcategory, "name", ""),
                "vehicle": getattr(getattr(tx, "vehicle", None), "label", ""),
                "job": getattr(getattr(tx, "job", None), "label", "") or getattr(getattr(tx, "job", None), "job_number", ""),
                "contact": getattr(getattr(tx, "contact", None), "name", ""),
                "amount": amount,
                "is_refund": getattr(tx, "is_refund", False),
                "notes": getattr(tx, "notes", ""),
            }
        )
    return rows


def _mileage_detail_rows(*, business, year: int):
    qs = (
        VehicleMiles.objects.filter(business=business, date__year=year)
        .select_related("vehicle", "job", "invoice")
        .order_by("date", "id")
    )

    rows = []
    total_miles = Decimal("0.0")
    for entry in qs:
        miles = entry.total or Decimal("0.0")
        total_miles += miles
        rows.append(
            {
                "date": entry.date,
                "vehicle": getattr(entry.vehicle, "label", ""),
                "mileage_type": entry.get_mileage_type_display(),
                "begin": entry.begin,
                "end": entry.end,
                "total": miles,
                "job": getattr(getattr(entry, "job", None), "label", "") or getattr(getattr(entry, "job", None), "job_number", ""),
                "invoice": getattr(getattr(entry, "invoice", None), "invoice_number", ""),
                "notes": entry.notes,
            }
        )
    return {"rows": rows, "total_miles": total_miles}


def build_tax_packet_context(request, *, year: int, options: TaxPacketOptions) -> dict:
    business = getattr(request, "business", None)
    pl = build_profit_loss_single(business=business, year=year)
    schedule_lines, schedule_total = build_schedule_c_lines(business=business, year=year, mode="tax")
    vehicle_summary = _vehicle_year_rows(business=business, year=year)
    section_179 = _section_179_rows(business=business, year=year)

    today = date.today()
    as_of_date = today if year == today.year else date(year, 12, 31)

    transaction_detail_rows = []
    mileage_detail = {"rows": [], "total_miles": Decimal("0.0")}
    if options.include_transaction_detail:
        transaction_detail_rows = _transaction_detail_rows(business=business, year=year)
    if options.include_mileage_detail:
        mileage_detail = _mileage_detail_rows(business=business, year=year)

    estimated_taxable_business_income = (
        pl.net_profit
        - vehicle_summary["total_selected_deduction"]
        - section_179["total_section_179"]
    )

    ctx = {
        "selected_year": year,
        "year_choices": year_choices(),
        "as_of_date": as_of_date,
        "pl": pl,
        "schedule_lines": schedule_lines,
        "schedule_total": schedule_total,
        "vehicle_summary": vehicle_summary,
        "section_179": section_179,
        "estimated_taxable_business_income": estimated_taxable_business_income,
        "include_transaction_detail": options.include_transaction_detail,
        "include_mileage_detail": options.include_mileage_detail,
        "transaction_detail_rows": transaction_detail_rows,
        "mileage_detail": mileage_detail,
        **_company_context(request),
    }
    return ctx
