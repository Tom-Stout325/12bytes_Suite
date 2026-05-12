from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from django.db.models import Q

from ledger.models import Category, SubCategory, Transaction


# Key -> pretty label (PDF/UI)
SCHEDULE_C_LABELS: dict[str, str] = {
    Category.ScheduleCLine.ADVERTISING: "Advertising",
    Category.ScheduleCLine.CAR_TRUCK: "Car and Truck Expenses",
    Category.ScheduleCLine.COMMISSIONS_FEES: "Commissions and Fees",
    Category.ScheduleCLine.CONTRACT_LABOR: "Contract Labor",
    Category.ScheduleCLine.DEPLETION: "Depletion",
    Category.ScheduleCLine.DEPRECIATION: "Depreciation",
    Category.ScheduleCLine.EMPLOYEE_BENEFITS: "Employee Benefit Programs",
    Category.ScheduleCLine.INSURANCE: "Insurance",
    Category.ScheduleCLine.INTEREST_MORTGAGE: "Interest: Mortgage",
    Category.ScheduleCLine.INTEREST_OTHER: "Interest: Other",
    Category.ScheduleCLine.LEGAL_PRO: "Legal and Professional Services",
    Category.ScheduleCLine.OFFICE: "Office Expense",
    Category.ScheduleCLine.PENSION_PROFIT: "Pension and Profit-Sharing Plans",
    Category.ScheduleCLine.RENT_LEASE_VEHICLES: "Rent or Lease: Vehicles, Machinery, Equipment",
    Category.ScheduleCLine.RENT_LEASE_OTHER: "Rent or Lease: Other Business Property",
    Category.ScheduleCLine.REPAIRS: "Repairs and Maintenance",
    Category.ScheduleCLine.SUPPLIES: "Supplies",
    Category.ScheduleCLine.TAXES_LICENSES: "Taxes and Licenses",
    Category.ScheduleCLine.TRAVEL: "Travel & Meals: Travel",
    Category.ScheduleCLine.MEALS: "Travel and Meals: Deductible Meals",
    Category.ScheduleCLine.UTILITIES: "Utilities",
    Category.ScheduleCLine.WAGES: "Wages",
    Category.ScheduleCLine.ENERGY_EFFICIENT: "Other Expenses",
    Category.ScheduleCLine.OTHER_EXPENSES_V: "Other Expenses",
}


def _line_number(key: str) -> str:
    # choices are (value, label) where label is the printed Schedule C line number
    return dict(Category.ScheduleCLine.choices).get(key, "")


def _line_label(key: str) -> str:
    return SCHEDULE_C_LABELS.get(key, "Other")


def _is_gas_like(sc: SubCategory) -> bool:
    slug = (sc.slug or "").lower()
    name = (sc.name or "").lower()
    return ("gas" in slug) or ("fuel" in slug) or ("gas" in name) or ("fuel" in name)


def _is_meals_like(sc: SubCategory) -> bool:
    slug = (sc.slug or "").lower()
    return slug == "meals" or slug.endswith("-meals")


def _transport_is_rental(t: Transaction) -> bool:
    """Best-effort rental-car detection across enum/string variants."""

    val = (t.transport_type or "").strip()
    if not val:
        return False

    if val in {"rental", "rental_car", "rental-car", "rentalcar"}:
        return True

    try:
        return val == str(Transaction.TransportType.RENTAL_CAR)
    except Exception:
        return False


@dataclass(frozen=True)
class BreakdownRow:
    sub_cat: str
    sub_cat_slug: str
    amount_total: Decimal

    # Backward-compat for templates that still reference deductible_total
    @property
    def deductible_total(self) -> Decimal:  # pragma: no cover
        return self.amount_total


@dataclass(frozen=True)
class LineRow:
    line: str  # printed line number (e.g., 24a)
    category_label: str
    total: Decimal
    breakdown: list[BreakdownRow]

    # Backward-compat for templates that still reference line_label
    @property
    def line_label(self) -> str:  # pragma: no cover
        return self.line


@dataclass(frozen=True)
class YoYLineRow:
    line: str
    category_label: str
    totals: dict[int, Decimal]


def build_schedule_c_yoy(
    *,
    business,
    ending_year: int,
    meals_rate: Decimal = Decimal("0.50"),
    mode: Literal["tax", "books"] = "tax",
) -> tuple[list[int], list[YoYLineRow], dict[int, Decimal], Decimal]:
    """3-year YoY totals by Schedule C line (Operating Expenses)."""

    years = [ending_year - 2, ending_year - 1, ending_year]

    by_year: dict[int, dict[str, Decimal]] = {}
    line_meta: dict[str, tuple[str, str]] = {}
    year_totals: dict[int, Decimal] = {y: Decimal("0.00") for y in years}

    for y in years:
        lines, grand = build_schedule_c_lines(business=business, year=y, meals_rate=meals_rate, mode=mode)
        year_totals[y] = grand
        m: dict[str, Decimal] = {}
        for ln in lines:
            # ln.line is printed number; to align across years we use label+number as key.
            # Prefer category_label as stable key, but keep the printed line badge too.
            key = f"{ln.line}::{ln.category_label}".strip(":")
            m[key] = ln.total
            line_meta[key] = (ln.line, ln.category_label)
        by_year[y] = m

    all_keys = sorted(line_meta.keys(), key=lambda k: line_meta[k][0])
    rows: list[YoYLineRow] = []
    for key in all_keys:
        line_no, label = line_meta[key]
        totals = {y: (by_year.get(y, {}).get(key, Decimal("0.00"))) for y in years}
        rows.append(YoYLineRow(line=line_no, category_label=label, totals=totals))

    grand_total = year_totals[ending_year]
    return years, rows, year_totals, grand_total


def build_schedule_c_lines(
    *,
    business,
    year: int,
    meals_rate: Decimal = Decimal("0.50"),
    mode: Literal["tax", "books"] = "tax",
) -> tuple[list[LineRow], Decimal]:
    """Operating Expenses.

    Modes:
    - mode="tax": deductible Schedule C view
      - meals 50% (by deduction_rule=MEALS_50 *or* slug meals / *-meals)
      - nondeductible subcategories excluded
      - gas/fuel included only when transport_type indicates rental car
    - mode="books": actual expenses (no deduction rules)
      - meals 100%
      - gas/fuel included regardless of transport_type
      - nondeductible included (actual spending)
    """

    date_from = date(year, 1, 1)
    date_to = date(year, 12, 31)

    qs = (
        Transaction.objects.filter(
            Q(business=business),
            Q(trans_type=Transaction.TransactionType.EXPENSE),
            Q(date__gte=date_from, date__lte=date_to),
            Q(subcategory__tax_enabled=True),
            Q(category__tax_reports=True),
        )
        .select_related("subcategory", "subcategory__category")
        .only(
            "amount",
            "is_refund",
            "transport_type",
            "subcategory__id",
            "subcategory__name",
            "subcategory__slug",
            "subcategory__deduction_rule",
            "subcategory__schedule_c_line",
            "subcategory__category__schedule_c_line",
        )
    )

    # line_key -> subcat_id -> Decimal
    buckets: dict[str, dict[int, dict[str, object]]] = {}

    for t in qs.iterator(chunk_size=2000):
        sc: SubCategory = t.subcategory

        # Determine line key (subcat override > category)
        line_key = (sc.schedule_c_line or sc.category.schedule_c_line or "").strip()
        if not line_key:
            continue

        amt = t.effective_amount

        if mode == "tax":
            # Gas/Fuel: rental cars only
            if _is_gas_like(sc) and (not _transport_is_rental(t)):
                continue

            rule = (sc.deduction_rule or SubCategory.DeductionRule.FULL)
            if rule == SubCategory.DeductionRule.NONDEDUCTIBLE:
                continue

            if rule == SubCategory.DeductionRule.MEALS_50 or _is_meals_like(sc):
                amt = (amt * meals_rate)

        if amt == 0:
            continue

        per_line = buckets.setdefault(line_key, {})
        entry = per_line.get(sc.id)
        if entry is None:
            per_line[sc.id] = {
                "name": sc.name,
                "slug": (sc.slug or ""),
                "total": Decimal("0.00"),
            }
            entry = per_line[sc.id]

        entry["total"] = (entry["total"] or Decimal("0.00")) + amt

    # Build ordered LineRow list
    lines: list[LineRow] = []
    grand = Decimal("0.00")

    # Sort by printed line number using existing helper if present; otherwise by label
    def sort_key(k: str):
        try:
            from ledger.reporting_utils import schedule_c_sort_key

            return schedule_c_sort_key(k)
        except Exception:
            return _line_number(k)

    for line_key in sorted(buckets.keys(), key=sort_key):
        sub_map = buckets[line_key]
        breakdown: list[BreakdownRow] = []
        line_total = Decimal("0.00")

        # sort subcats alpha
        for _sid, data in sorted(sub_map.items(), key=lambda it: str(it[1].get("name", "")).lower()):
            total = data.get("total") or Decimal("0.00")
            if total == 0:
                continue
            breakdown.append(
                BreakdownRow(
                    sub_cat=str(data.get("name") or ""),
                    sub_cat_slug=str(data.get("slug") or ""),
                    amount_total=total,
                )
            )
            line_total += total

        if line_total == 0:
            continue

        grand += line_total
        lines.append(
            LineRow(
                line=_line_number(line_key),
                category_label=_line_label(line_key),
                total=line_total,
                breakdown=breakdown,
            )
        )

    return lines, grand
