from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Case, DecimalField, F, Sum, Value, When

from ledger.models import Category, SubCategory, Transaction


# Human-friendly labels for the Schedule C lines we report here.
# (These match the headings you expect in the PDF.)
SCHEDULE_C_LINE_LABELS: dict[str, str] = {
    "8": "Advertising",
    "9": "Car & Truck Expenses",
    "10": "Commissions & Fees",
    "11": "Contract Labor",
    "12": "Depletion",
    "13": "Depreciation",
    "14": "Employee Benefit Programs",
    "15": "Insurance",
    "16a": "Interest: Mortgage",
    "16b": "Interest: Other",
    "17": "Legal & Professional",
    "18": "Office Expense",
    "19": "Pension & Profit Sharing",
    "20a": "Rent or Lease: Vehicles, Machinery, Equipment",
    "20b": "Rent or Lease: Other Business Property",
    "21": "Repairs & Maintenance",
    "22": "Supplies",
    "23": "Taxes & Licenses",
    "24a": "Travel & Meals: Travel",
    "24b": "Travel and Meals: Deductible Meals",
    "25": "Utilities",
    "26": "Wages",
    "27a": "Energy Efficient Buildings",
    "27b": "Other Expenses",
}


def _line_label_from_value(value: str | None) -> str:
    if not value:
        return ""
    # If stored value is already the label ('24b'), keep it.
    if value[:1].isdigit():
        return value.strip()
    try:
        return Category.ScheduleCLine(value).label
    except Exception:
        return str(value).strip()


def _category_label_for_line(line_label: str, schedule_c_value: str | None) -> str:
    if line_label in SCHEDULE_C_LINE_LABELS:
        return SCHEDULE_C_LINE_LABELS[line_label]

    # Fallback: pretty-print the enum member name, if possible.
    if schedule_c_value:
        try:
            member = Category.ScheduleCLine(schedule_c_value)
            return member.name.replace("_", " ").title()
        except Exception:
            pass
    return "Other"


@dataclass(frozen=True)
class ScheduleCBreakdownRow:
    sub_cat: str
    sub_cat_slug: str
    deductible_total: Decimal


@dataclass(frozen=True)
class ScheduleCLineRow:
    line: str
    category_label: str
    total: Decimal
    breakdown: list[ScheduleCBreakdownRow]


def build_schedule_c_lines(*, business, date_from: date, date_to: date) -> tuple[list[ScheduleCLineRow], Decimal]:
    """Return (lines, grand_total) for deductible operating expenses."""

    # Signed amount (refunds reduce totals)
    signed_amount = Case(
        When(is_refund=True, then=F("amount") * Value(-1)),
        default=F("amount"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    # Deductible amount based on SubCategory.deduction_rule
    deductible_amount = Case(
        When(subcategory__deduction_rule=SubCategory.DeductionRule.NONDEDUCTIBLE, then=Value(Decimal("0.00"))),
        # meals_50: 50% deductible
        When(subcategory__deduction_rule=SubCategory.DeductionRule.MEALS_50, then=signed_amount * Value(Decimal("0.5"))),
        default=signed_amount,
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    qs = (
        Transaction.objects.filter(
            business=business,
            trans_type=Transaction.TransactionType.EXPENSE,
            date__gte=date_from,
            date__lte=date_to,
            subcategory__isnull=False,
            subcategory__tax_enabled=True,
        )
        .values(
            "subcategory_id",
            "subcategory__name",
            "subcategory__slug",
            "subcategory__schedule_c_line",
            "category__schedule_c_line",
        )
        .annotate(deductible_total=Sum(deductible_amount))
    )

    # Assemble in Python so we can:
    # - pick schedule_c_line (SubCategory override, else Category)
    # - map to label + category_label
    lines_map: dict[str, dict] = {}
    for row in qs:
        sc_value = row["subcategory__schedule_c_line"] or row["category__schedule_c_line"] or ""
        line_label = _line_label_from_value(sc_value)
        cat_label = _category_label_for_line(line_label, sc_value)

        dtotal = row["deductible_total"] or Decimal("0.00")
        if line_label not in lines_map:
            lines_map[line_label] = {
                "line": line_label,
                "category_label": cat_label,
                "total": Decimal("0.00"),
                "breakdown": [],
            }

        lines_map[line_label]["total"] += dtotal
        lines_map[line_label]["breakdown"].append(
            ScheduleCBreakdownRow(
                sub_cat=row["subcategory__name"] or "",
                sub_cat_slug=row["subcategory__slug"] or "",
                deductible_total=dtotal,
            )
        )

    # Sort lines in IRS order using the existing helper
    from ledger.reporting_utils import schedule_c_sort_key

    lines: list[ScheduleCLineRow] = []
    for key in sorted(lines_map.keys(), key=schedule_c_sort_key):
        data = lines_map[key]
        # Sort breakdown rows by subcat name
        b = sorted(data["breakdown"], key=lambda r: (r.sub_cat or "").lower())
        lines.append(
            ScheduleCLineRow(
                line=data["line"],
                category_label=data["category_label"],
                total=data["total"],
                breakdown=b,
            )
        )

    grand_total = sum((l.total for l in lines), Decimal("0.00"))
    return lines, grand_total
