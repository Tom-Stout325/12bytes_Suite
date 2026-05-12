# reports/queries.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from django.db.models import Case, DecimalField, ExpressionWrapper, F, Q, Sum, Value, When
from django.db.models.functions import Abs, Coalesce

from ledger.models import Category, SubCategory, Transaction
from ledger.reporting_utils import schedule_c_sort_key

ReportMode = Literal["book", "tax"]


@dataclass(frozen=True)
class SubCategoryTotal:
    subcategory_id: int
    subcategory_name: str
    total: Decimal


@dataclass(frozen=True)
class CategoryTotal:
    category_id: int
    category_name: str
    schedule_c_line: str
    report_group: str
    total: Decimal
    subcategories: list[SubCategoryTotal]


def _amount_expression_for_mode(mode: ReportMode):
    """ORM expression for report aggregation amounts.

    Rules:
    - Returns & Allowances (Schedule C line 2) ALWAYS reduces income:
        normalized to negative abs(amount) at calculation/report time.
    - Tax mode:
        any SubCategory with deduction_rule == MEALS_50 is 50%.
    - Book mode:
        Raw amounts (except Returns & Allowances normalization).
    """
    out = DecimalField(max_digits=12, decimal_places=2)
    base_amt = F("amount")

    returns_normalized = ExpressionWrapper(
        Value(Decimal("-1.00")) * Abs(base_amt),
        output_field=out,
    )

    is_returns = Q(subcategory__category__schedule_c_line=Category.ScheduleCLine.RETURNS_ALLOWANCES)
    meals_50 = Q(subcategory__deduction_rule=SubCategory.DeductionRule.MEALS_50)
    meals_slug = Q(subcategory__slug="meals") | Q(subcategory__slug__endswith="-meals")

    tax_expr = Case(
        When(is_returns, then=returns_normalized),
        When(meals_50 | meals_slug, then=ExpressionWrapper(base_amt * Value(Decimal("0.50")), output_field=out)),
        default=base_amt,
        output_field=out,
    )

    book_expr = Case(
        When(is_returns, then=returns_normalized),
        default=base_amt,
        output_field=out,
    )

    return tax_expr if mode == "tax" else book_expr


def aggregate_category_subcategory_totals(
    *,
    business,
    date_from: date | None = None,
    date_to: date | None = None,
    mode: ReportMode = "book",
) -> list[CategoryTotal]:
    """Aggregate totals grouped by Category -> SubCategory for the given business + date range."""
    filters = Q(business=business)

    if date_from:
        filters &= Q(date__gte=date_from)
    if date_to:
        filters &= Q(date__lte=date_to)

    amt_expr = _amount_expression_for_mode(mode)

    rows = (
        Transaction.objects.filter(filters)
        .select_related("subcategory", "subcategory__category")
        .values(
            "subcategory__category_id",
            "subcategory__category__name",
            "subcategory__category__schedule_c_line",
            "subcategory__category__report_group",
            "subcategory_id",
            "subcategory__name",
        )
        .annotate(total=Coalesce(Sum(amt_expr), Value(Decimal("0.00"))))
    )

    by_cat: dict[int, CategoryTotal] = {}

    for r in rows:
        total = r["total"] or Decimal("0.00")
        if total == 0:
            continue

        cat_id = int(r["subcategory__category_id"])
        cat_name = r["subcategory__category__name"] or ""
        line = r["subcategory__category__schedule_c_line"] or ""
        group = r["subcategory__category__report_group"] or ""

        sub_id = int(r["subcategory_id"])
        sub_name = r["subcategory__name"] or ""

        if cat_id not in by_cat:
            by_cat[cat_id] = CategoryTotal(
                category_id=cat_id,
                category_name=cat_name,
                schedule_c_line=line,
                report_group=group,
                total=Decimal("0.00"),
                subcategories=[],
            )

        by_cat[cat_id].subcategories.append(
            SubCategoryTotal(
                subcategory_id=sub_id,
                subcategory_name=sub_name,
                total=total,
            )
        )

    categories: list[CategoryTotal] = []
    for cat in by_cat.values():
        subcats_sorted = sorted(cat.subcategories, key=lambda s: s.subcategory_name.lower())
        cat_total = sum((s.total for s in subcats_sorted), Decimal("0.00"))
        if cat_total == 0:
            continue

        categories.append(
            CategoryTotal(
                category_id=cat.category_id,
                category_name=cat.category_name,
                schedule_c_line=cat.schedule_c_line,
                report_group=cat.report_group,
                total=cat_total,
                subcategories=subcats_sorted,
            )
        )

    categories.sort(key=lambda c: (schedule_c_sort_key(c.schedule_c_line), c.category_name.lower()))
    return categories
