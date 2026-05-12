from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Case, DecimalField, F, Sum, When

from ledger.models import Category, Transaction


SIGNED_AMOUNT = Case(
    When(is_refund=True, then=-F("amount")),
    default=F("amount"),
    output_field=DecimalField(max_digits=12, decimal_places=2),
)


def _date_bounds(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


def _years_ending(end_year: int, *, count: int = 3) -> list[int]:
    return list(range(end_year - (count - 1), end_year + 1))


@dataclass(frozen=True)
class PLBreakdownRow:
    sub_cat: str
    sub_cat_slug: str
    amount: Decimal


@dataclass(frozen=True)
class PLCategoryRow:
    category: str
    category_slug: str
    category_type: str
    sort_order: int
    amount: Decimal
    breakdown: list[PLBreakdownRow]


@dataclass(frozen=True)
class ProfitLossSingle:
    year: int
    sales: Decimal
    returns_allowances: Decimal
    net_sales: Decimal
    total_expenses: Decimal
    net_profit: Decimal
    income_rows: list[PLCategoryRow]
    expense_rows: list[PLCategoryRow]


@dataclass(frozen=True)
class PLYoYRow:
    label: str
    totals: dict[int, Decimal]
    totals_list: list[Decimal]


@dataclass(frozen=True)
class ProfitLossYoY:
    years: list[int]
    sales: PLYoYRow
    returns_allowances: PLYoYRow
    net_sales: PLYoYRow
    total_expenses: PLYoYRow
    net_profit: PLYoYRow
    income_rows: list[PLYoYRow]
    expense_rows: list[PLYoYRow]


def _is_returns_category(*, schedule_c_line: str, slug: str, name: str) -> bool:
    if (schedule_c_line or "") == Category.ScheduleCLine.RETURNS_ALLOWANCES:
        return True
    s = (slug or "").lower()
    n = (name or "").lower()
    return ("returns" in s and "allow" in s) or ("returns" in n and "allow" in n)


def build_profit_loss_single(*, business, year: int) -> ProfitLossSingle:
    """Books (actual) Profit & Loss.

    - Uses signed amounts (refunds reduce totals).
    - Includes only book-visible categories/subcategories.
    - Returns & Allowances are shown under Revenue as a "Less" line.
    """

    date_from, date_to = _date_bounds(year)

    base_qs = Transaction.objects.filter(
        business=business,
        date__gte=date_from,
        date__lte=date_to,
        subcategory__book_enabled=True,
        category__book_reports=True,
    )

    grouped = (
        base_qs.values(
            "category_id",
            "category__name",
            "category__slug",
            "category__category_type",
            "category__sort_order",
            "category__schedule_c_line",
            "subcategory__name",
            "subcategory__slug",
        )
        .annotate(total=Sum(SIGNED_AMOUNT))
        .order_by()
    )

    income_map: dict[int, dict[str, object]] = {}
    expense_map: dict[int, dict[str, object]] = {}

    sales_total = Decimal("0.00")
    returns_total = Decimal("0.00")

    for row in grouped.iterator(chunk_size=2000):
        total: Decimal = row.get("total") or Decimal("0.00")
        if total == 0:
            continue

        cat_id = int(row["category_id"])
        cat_name = str(row.get("category__name") or "")
        cat_slug = str(row.get("category__slug") or "")
        cat_type = str(row.get("category__category_type") or "")
        sort_order = int(row.get("category__sort_order") or 0)
        sched = str(row.get("category__schedule_c_line") or "")

        sub_name = str(row.get("subcategory__name") or "")
        sub_slug = str(row.get("subcategory__slug") or "")

        is_returns = _is_returns_category(schedule_c_line=sched, slug=cat_slug, name=cat_name)

        target = income_map if cat_type == Category.CategoryType.INCOME else expense_map
        cat_entry = target.get(cat_id)
        if cat_entry is None:
            target[cat_id] = {
                "name": cat_name,
                "slug": cat_slug,
                "type": cat_type,
                "sort_order": sort_order,
                "total": Decimal("0.00"),
                "breakdown": [],
            }
            cat_entry = target[cat_id]

        cat_entry["total"] = (cat_entry["total"] or Decimal("0.00")) + total
        cat_entry["breakdown"].append(PLBreakdownRow(sub_cat=sub_name, sub_cat_slug=sub_slug, amount=total))

        if cat_type == Category.CategoryType.INCOME:
            if is_returns:
                returns_total += abs(total)
            else:
                sales_total += total

    def _rows(m: dict[int, dict[str, object]]) -> list[PLCategoryRow]:
        rows: list[PLCategoryRow] = []
        for _cid, data in sorted(
            m.items(),
            key=lambda it: (int(it[1].get("sort_order") or 0), str(it[1].get("name") or "").lower()),
        ):
            breakdown = list(data.get("breakdown") or [])
            breakdown.sort(key=lambda r: (r.sub_cat or "").lower())
            rows.append(
                PLCategoryRow(
                    category=str(data.get("name") or ""),
                    category_slug=str(data.get("slug") or ""),
                    category_type=str(data.get("type") or ""),
                    sort_order=int(data.get("sort_order") or 0),
                    amount=(data.get("total") or Decimal("0.00")),
                    breakdown=breakdown,
                )
            )
        return rows

    income_rows = _rows(income_map)
    expense_rows = _rows(expense_map)

    net_sales = sales_total - returns_total
    total_expenses = sum((r.amount for r in expense_rows), Decimal("0.00"))
    net_profit = net_sales - total_expenses

    return ProfitLossSingle(
        year=year,
        sales=sales_total,
        returns_allowances=returns_total,
        net_sales=net_sales,
        total_expenses=total_expenses,
        net_profit=net_profit,
        income_rows=income_rows,
        expense_rows=expense_rows,
    )


def build_profit_loss_yoy(*, business, ending_year: int, years: int = 3) -> ProfitLossYoY:
    yrs = _years_ending(ending_year, count=years)

    income_totals: dict[str, dict[int, Decimal]] = {}
    expense_totals: dict[str, dict[int, Decimal]] = {}

    sales_by_year: dict[int, Decimal] = {y: Decimal("0.00") for y in yrs}
    returns_by_year: dict[int, Decimal] = {y: Decimal("0.00") for y in yrs}
    expenses_by_year: dict[int, Decimal] = {y: Decimal("0.00") for y in yrs}

    for y in yrs:
        date_from, date_to = _date_bounds(y)
        qs = Transaction.objects.filter(
            business=business,
            date__gte=date_from,
            date__lte=date_to,
            subcategory__book_enabled=True,
            category__book_reports=True,
        )

        grouped = (
            qs.values(
                "category__name",
                "category__slug",
                "category__category_type",
                "category__schedule_c_line",
            )
            .annotate(total=Sum(SIGNED_AMOUNT))
            .order_by()
        )

        for row in grouped:
            total: Decimal = row.get("total") or Decimal("0.00")
            if total == 0:
                continue

            cat_name = str(row.get("category__name") or "")
            cat_slug = str(row.get("category__slug") or "")
            cat_type = str(row.get("category__category_type") or "")
            sched = str(row.get("category__schedule_c_line") or "")
            is_returns = _is_returns_category(schedule_c_line=sched, slug=cat_slug, name=cat_name)

            if cat_type == Category.CategoryType.INCOME:
                if is_returns:
                    returns_by_year[y] += abs(total)
                else:
                    sales_by_year[y] += total
                m = income_totals.setdefault(cat_name, {yy: Decimal("0.00") for yy in yrs})
                m[y] += total
            else:
                m = expense_totals.setdefault(cat_name, {yy: Decimal("0.00") for yy in yrs})
                m[y] += total
                expenses_by_year[y] += total

    net_sales_by_year = {y: sales_by_year[y] - returns_by_year[y] for y in yrs}
    net_profit_by_year = {y: net_sales_by_year[y] - expenses_by_year[y] for y in yrs}

    def mk(label: str, d: dict[int, Decimal]) -> PLYoYRow:
        return PLYoYRow(label=label, totals=d, totals_list=[d[yy] for yy in yrs])

    income_rows = [mk(label, totals) for label, totals in sorted(income_totals.items(), key=lambda it: it[0].lower())]
    expense_rows = [mk(label, totals) for label, totals in sorted(expense_totals.items(), key=lambda it: it[0].lower())]

    return ProfitLossYoY(
        years=yrs,
        sales=mk("Sales", sales_by_year),
        returns_allowances=mk("Less: Returns & Allowances", returns_by_year),
        net_sales=mk("Net Sales", net_sales_by_year),
        total_expenses=mk("Total Expenses", expenses_by_year),
        net_profit=mk("Net Profit", net_profit_by_year),
        income_rows=income_rows,
        expense_rows=expense_rows,
    )
