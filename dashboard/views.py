from __future__ import annotations

import json
from datetime import date
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Case, DecimalField, F, Sum, Value, When
from django.db.models.functions import TruncMonth
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.core.management import call_command
from core.models import BackupLog, BusinessMembership
from core.business_backup_exports import (
    business_backup_table_specs,
    csv_response_for_table,
    get_backup_table_spec_or_404,
    queryset_for_business,
    workbook_response_for_business,
)
from core.business_backup_storage import backup_download_url, cleanup_old_business_backups, create_business_backup
from ledger.models import Transaction, Category, SubCategory
from invoices.models import Invoice
from ledger.services import seed_schedule_c_defaults
from django.db import transaction as db_transaction
from dataclasses import dataclass

from django.db import transaction
from django.utils.text import slugify




def _signed_amount_expr():
    """ORM expression: refunds become negative amounts."""
    return Case(
        When(is_refund=True, then=-F("amount")),
        default=F("amount"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )


def _first_day_n_months_ago(anchor: date, months_back: int) -> date:
    """Return the first day of the month, `months_back` months before `anchor`'s month."""
    y, m = anchor.year, anchor.month
    for _ in range(months_back):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return date(y, m, 1)


def _period_from_params(*, today: date, mode: str, year: int | None) -> tuple[date, date, str, str]:
    """Return (start_date, end_date, label, selected_year_value)."""
    mode = (mode or "rolling").strip().lower()

    if mode == "month":
        start_date = date(today.year, today.month, 1)
        end_date = today
        return start_date, end_date, today.strftime("%b %Y"), "month"

    if mode == "year" and year:
        start_date = date(year, 1, 1)
        end_date = date(year, 12, 31)
        return start_date, end_date, str(year), str(year)

    # default rolling 12 months inclusive of current month
    start_date = _first_day_n_months_ago(date(today.year, today.month, 1), 11)
    end_date = today
    return start_date, end_date, "Rolling 12 months", "rolling"


def _dashboard_payload(*, business, start_date: date, end_date: date) -> dict:
    signed_amount = _signed_amount_expr()

    base_qs = Transaction.objects.filter(business=business, date__gte=start_date, date__lte=end_date)

    income_total = (
        base_qs.filter(trans_type=Transaction.TransactionType.INCOME)
        .aggregate(total=Sum(signed_amount))["total"]
        or 0
    )
    expense_total = (
        base_qs.filter(trans_type=Transaction.TransactionType.EXPENSE)
        .aggregate(total=Sum(signed_amount))["total"]
        or 0
    )
    net_total = income_total - expense_total

    chart_qs = (
        base_qs.annotate(month=TruncMonth("date"))
        .values("month")
        .annotate(
            income=Sum(
                Case(
                    When(trans_type=Transaction.TransactionType.INCOME, then=signed_amount),
                    default=Value(0),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            ),
            expenses=Sum(
                Case(
                    When(trans_type=Transaction.TransactionType.EXPENSE, then=signed_amount),
                    default=Value(0),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            ),
        )
        .order_by("month")
    )

    labels: list[str] = []
    income_series: list[float] = []
    expense_series: list[float] = []

    for row in chart_qs:
        m = row["month"]
        if not m:
            continue
        labels.append(m.strftime("%b %Y"))
        income_series.append(float(row["income"] or 0))
        expense_series.append(float(row["expenses"] or 0))

    
    # Breakdown (top subcategories + Other) for the selected period
    def _breakdown(trans_type: str, top_n: int = 6) -> tuple[list[str], list[float]]:
        qs = (
            base_qs.filter(trans_type=trans_type)
            .values("subcategory__name")
            .annotate(total=Sum(signed_amount))
            .order_by("-total")
        )
        labels_b: list[str] = []
        values_b: list[float] = []
        other_total = 0.0

        for i, row in enumerate(qs):
            name = row.get("subcategory__name") or "Uncategorized"
            val = float(row.get("total") or 0)
            if i < top_n:
                labels_b.append(name)
                values_b.append(val)
            else:
                other_total += val

        if other_total > 0:
            labels_b.append("Other")
            values_b.append(other_total)

        return labels_b, values_b

    income_breakdown_labels, income_breakdown_values = _breakdown(Transaction.TransactionType.INCOME)
    expense_breakdown_labels, expense_breakdown_values = _breakdown(Transaction.TransactionType.EXPENSE)

    return {
        "income_total": float(income_total),
        "expense_total": float(expense_total),
        "net_total": float(net_total),
        "labels": labels,
        "income": income_series,
        "expenses": expense_series,
        "income_breakdown_labels": income_breakdown_labels,
        "income_breakdown_values": income_breakdown_values,
        "expense_breakdown_labels": expense_breakdown_labels,
        "expense_breakdown_values": expense_breakdown_values,
    }



def _onboarding_gate_or_redirect(request):
    business = getattr(request, "business", None)
    if business is None:
        return redirect("accounts:onboarding")

    profile = getattr(business, "company_profile", None)
    if not profile or not profile.is_complete:
        return redirect("accounts:onboarding")

    return None




@login_required
def home(request):
    print("DASHBOARD HIT", request.path, "business:", getattr(request, "business", None))

    return redirect("dashboard:dashboard_home")


@login_required
def moneypro_home(request):
    gate = _onboarding_gate_or_redirect(request)
    if gate:
        return gate

    business = getattr(request, "business", None)
    if business is None:
        messages.error(request, "No active business is set for your account.")
        return render(request, "dashboard/moneypro_home.html", {})

    today = timezone.localdate()

    # Default view is rolling; year dropdown still exists for switching via AJAX.
    start_date, end_date, period_label, selected_year_value = _period_from_params(
        today=today, mode="rolling", year=None
    )

    payload = _dashboard_payload(business=business, start_date=start_date, end_date=end_date)

    # Year dropdown options (from existing transactions)
    year_dates = Transaction.objects.filter(business=business).dates("date", "year", order="DESC")
    years = [d.year for d in year_dates]

    # Recent transactions (always full business scope; independent of chart period)
    recent_transactions = (
        Transaction.objects.filter(business=business)
        .select_related("subcategory")
        .order_by("-date", "-id")[:15]
    )

    recent_invoices = (
        Invoice.objects.filter(business=business)
        .select_related("job")
        .order_by("-issue_date", "-id")[:10]
    )

    context = {
        "period_label": period_label,
        "selected_year_value": selected_year_value,
        "years": years,
        "has_seeded": Category.objects.filter(business=business).exists(),
        "can_rebuild": not Transaction.objects.filter(business=business).exists(),
        "income_total": payload["income_total"],
        "expense_total": payload["expense_total"],
        "net_total": payload["net_total"],
        "chart_labels_json": json.dumps(payload["labels"]),
        "chart_income_json": json.dumps(payload["income"]),
        "chart_expense_json": json.dumps(payload["expenses"]),
        "income_breakdown_labels_json": json.dumps(payload.get("income_breakdown_labels", [])),
        "income_breakdown_values_json": json.dumps(payload.get("income_breakdown_values", [])),
        "expense_breakdown_labels_json": json.dumps(payload.get("expense_breakdown_labels", [])),
        "expense_breakdown_values_json": json.dumps(payload.get("expense_breakdown_values", [])),
        "recent_transactions": recent_transactions,
        "recent_invoices": recent_invoices,
    }
    return render(request, "dashboard/moneypro_home.html", context)


@login_required
def dashboard_home(request):
    gate = _onboarding_gate_or_redirect(request)
    if gate:
        return gate

    business = getattr(request, "business", None)
    if business is None:
        messages.error(request, "No active business is set for your account.")
        return redirect("accounts:onboarding")

    from flightlogs.models import FlightLog
    from operations.models import OpsPlan
    from assets.models import Asset

    recent_transactions = (
        Transaction.objects.filter(business=business)
        .order_by("-date", "-id")[:3]
    )
    recent_invoices = (
        Invoice.objects.filter(business=business)
        .order_by("-issue_date", "-id")[:3]
    )

    today = timezone.localdate()
    mtd_income = (
        Transaction.objects.filter(
            business=business,
            trans_type=Transaction.TransactionType.INCOME,
            date__year=today.year,
            date__month=today.month,
        ).aggregate(total=Sum(_signed_amount_expr()))["total"]
        or 0
    )

    context = {
        "mtd_income": float(mtd_income),
        "flights_mtd": FlightLog.objects.filter(
            business=business,
            flight_date__year=today.year,
            flight_date__month=today.month,
        ).count(),
        "active_jobs": OpsPlan.objects.filter(business=business).exclude(status=OpsPlan.ARCHIVED).count(),
        "open_invoices": Invoice.objects.filter(business=business).exclude(status__iexact="paid").count(),
        "active_assets": Asset.objects.filter(business=business, is_active=True).count(),
        "recent_transactions": recent_transactions,
        "recent_invoices": recent_invoices,
    }
    return render(request, "dashboard/suite_home.html", context)


@login_required
@require_GET
def business_backup_admin(request):
    gate = _onboarding_gate_or_redirect(request)
    if gate:
        return gate

    business = getattr(request, "business", None)
    if business is None:
        messages.error(request, "No active business is set for your account.")
        return redirect("accounts:onboarding")

    table_rows = []
    for spec in business_backup_table_specs():
        try:
            row_count = queryset_for_business(spec, business).count()
        except Exception:
            row_count = None
        table_rows.append({"spec": spec, "row_count": row_count})

    backup_logs = BackupLog.objects.filter(business=business).order_by("-created_at")[:25]
    backup_history = []
    for log in backup_logs:
        backup_history.append({"log": log, "download_url": backup_download_url(log) if log.status == BackupLog.Status.SUCCESS else ""})

    return render(
        request,
        "dashboard/business_backup_admin.html",
        {
            "business": business,
            "table_rows": table_rows,
            "backup_history": backup_history,
        },
    )


@login_required
@require_GET
def business_backup_table_csv(request, table_slug: str):
    gate = _onboarding_gate_or_redirect(request)
    if gate:
        return gate

    business = getattr(request, "business", None)
    if business is None:
        raise Http404("No active business is set for your account.")

    spec = get_backup_table_spec_or_404(table_slug)
    return csv_response_for_table(spec=spec, business=business)


@login_required
@require_GET
def business_backup_all_xlsx(request):
    gate = _onboarding_gate_or_redirect(request)
    if gate:
        return gate

    business = getattr(request, "business", None)
    if business is None:
        raise Http404("No active business is set for your account.")

    try:
        return workbook_response_for_business(business=business)
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect("dashboard:business_backup_admin")


@login_required
@require_POST
def business_backup_send_to_s3(request):
    gate = _onboarding_gate_or_redirect(request)
    if gate:
        return gate

    business = getattr(request, "business", None)
    if business is None:
        messages.error(request, "No active business is set for your account.")
        return redirect("accounts:onboarding")

    try:
        result = create_business_backup(business=business, created_by=request.user)
    except Exception as exc:
        messages.error(request, f"Backup failed: {exc}")
    else:
        messages.success(
            request,
            f"Backup saved. {result.log.row_count} rows across {result.log.table_count} tables were exported. "
            f"Old backups deleted: {result.deleted_count}.",
        )
    return redirect("dashboard:business_backup_admin")


@login_required
@require_POST
def business_backup_cleanup(request):
    gate = _onboarding_gate_or_redirect(request)
    if gate:
        return gate

    business = getattr(request, "business", None)
    if business is None:
        messages.error(request, "No active business is set for your account.")
        return redirect("accounts:onboarding")

    deleted_count = cleanup_old_business_backups(business=business)
    messages.success(request, f"Backup cleanup complete. Old backups deleted: {deleted_count}.")
    return redirect("dashboard:business_backup_admin")


@login_required
def dashboard_chart_data(request):
    """Return chart series + KPI totals for rolling 12 months or a selected year (AJAX)."""
    gate = _onboarding_gate_or_redirect(request)
    if gate:
        return JsonResponse({"error": "onboarding_required"}, status=403)

    business = getattr(request, "business", None)
    if business is None:
        return JsonResponse({"error": "no_business"}, status=400)

    today = timezone.localdate()
    mode = (request.GET.get("mode") or "rolling").strip().lower()

    year = None
    if mode == "year":
        try:
            year = int((request.GET.get("year") or "").strip())
        except ValueError:
            year = today.year

    start_date, end_date, period_label, selected_year_value = _period_from_params(
        today=today, mode=mode, year=year
    )
    payload = _dashboard_payload(business=business, start_date=start_date, end_date=end_date)
    payload.update(
        {
            "period_label": period_label,
            "selected_year_value": selected_year_value,
        }
    )
    return JsonResponse(payload)



def seed_and_apply_rules(business):
    seed_schedule_c_defaults(business)
    call_command("apply_subcategory_rules", business_id=business.id)


@login_required
@require_POST
def reseed_defaults(request):
    gate = _onboarding_gate_or_redirect(request)
    if gate:
        return gate

    business = getattr(request, "business", None)
    if business is None:
        messages.error(request, "No active business is set for your account.")
        return redirect("accounts:settings")

    try:
        seed_and_apply_rules(business)
    except Exception as exc:
        messages.error(request, f"Unable to seed defaults: {exc}")
    else:
        messages.success(request, "Defaults were seeded successfully.")

    return redirect("accounts:settings")


@login_required
@require_POST
def rebuild_defaults(request):
    gate = _onboarding_gate_or_redirect(request)
    if gate:
        return gate

    business = getattr(request, "business", None)
    if business is None:
        messages.error(request, "No active business is set for your account.")
        return redirect("accounts:settings")

    if Transaction.objects.filter(business=business).exists():
        messages.error(request, "Rebuild defaults is only allowed before any transactions exist.")
        return redirect("accounts:settings")

    try:
        with db_transaction.atomic():
            SubCategory.objects.filter(business=business).delete()
            Category.objects.filter(business=business).delete()
            seed_and_apply_rules(business)
    except Exception as exc:
        messages.error(request, f"Unable to rebuild defaults: {exc}")
    else:
        messages.success(request, "Defaults were rebuilt successfully.")

    return redirect("accounts:settings")


def _field_max_length(model, field_name: str, fallback: int) -> int:
    try:
        f = model._meta.get_field(field_name)
        return int(getattr(f, "max_length", None) or fallback)
    except Exception:
        return fallback


def _unique_slug(base: str, used: set[str], max_len: int) -> str:
    """
    Return a unique slug within the 'used' set.
    """
    base = slugify(base) or "item"
    base = base[:max_len]

    slug = base
    i = 2
    while slug in used:
        suffix = f"-{i}"
        available = max(1, max_len - len(suffix))
        slug = base[:available] + suffix
        i += 1

    used.add(slug)
    return slug


# ------------------------------------------------------------------------------
# Schedule C seeding (spreadsheet-friendly specs + conversion to enum keys)
# ------------------------------------------------------------------------------

@dataclass(frozen=True)
class CategorySpec:
    name: str
    schedule_c_line: str  # spreadsheet code like "1", "16a", "27b"
    category_type: str    # "income" | "expense"
    book_reports: bool
    tax_reports: bool
    report_group: str     # "Part I" | "Part II" | "Part V"


SCHEDULE_C_LINE_TO_CHOICE: dict[str, str] = {
    # Part I
    "1": Category.ScheduleCLine.GROSS_RECEIPTS,
    "2": Category.ScheduleCLine.RETURNS_ALLOWANCES,

    # Part II
    "8": Category.ScheduleCLine.ADVERTISING,
    "9": Category.ScheduleCLine.CAR_TRUCK,
    "10": Category.ScheduleCLine.COMMISSIONS_FEES,
    "11": Category.ScheduleCLine.CONTRACT_LABOR,
    "12": Category.ScheduleCLine.DEPLETION,
    "13": Category.ScheduleCLine.DEPRECIATION,
    "14": Category.ScheduleCLine.EMPLOYEE_BENEFITS,
    "15": Category.ScheduleCLine.INSURANCE,
    "16a": Category.ScheduleCLine.INTEREST_MORTGAGE,
    "16b": Category.ScheduleCLine.INTEREST_OTHER,
    "17": Category.ScheduleCLine.LEGAL_PRO,
    "18": Category.ScheduleCLine.OFFICE,
    "19": Category.ScheduleCLine.PENSION_PROFIT,
    "20a": Category.ScheduleCLine.RENT_LEASE_VEHICLES,
    "20b": Category.ScheduleCLine.RENT_LEASE_OTHER,
    "21": Category.ScheduleCLine.REPAIRS,
    "22": Category.ScheduleCLine.SUPPLIES,
    "23": Category.ScheduleCLine.TAXES_LICENSES,
    "24a": Category.ScheduleCLine.TRAVEL,
    "24b": Category.ScheduleCLine.MEALS,
    "25": Category.ScheduleCLine.UTILITIES,
    "26": Category.ScheduleCLine.WAGES,
    "27a": Category.ScheduleCLine.ENERGY_EFFICIENT,
    # Part V
    "27b": Category.ScheduleCLine.OTHER_EXPENSES_V,
}


def _schedule_c_choice(line_code: str) -> str:
    if not (line_code or "").strip():
        return ""

    k = (line_code or "").strip().lower()
    try:
        return SCHEDULE_C_LINE_TO_CHOICE[k]
    except KeyError as e:
        raise ValueError(f"Unknown Schedule C line code: {line_code!r}") from e


CATEGORY_SPECS: list[CategorySpec] = [
    # Part I
    CategorySpec("Gross Receipts", "1", "income", True, True, "Part I"),
    CategorySpec("Returns & Allowances", "2", "income", True, True, "Part I"),

    # Part II
    CategorySpec("Advertising", "8", "expense", True, True, "Part II"),
    CategorySpec("Car & Truck Expenses", "9", "expense", True, True, "Part II"),
    CategorySpec("Commissions & Fees", "10", "expense", True, True, "Part II"),
    CategorySpec("Contract Labor", "11", "expense", True, True, "Part II"),
    CategorySpec("Depletion", "12", "expense", True, True, "Part II"),
    CategorySpec("Depreciation & Section 179", "13", "expense", True, True, "Part II"),
    CategorySpec("Employee Benefits", "14", "expense", True, True, "Part II"),
    CategorySpec("Insurance", "15", "expense", True, True, "Part II"),
    CategorySpec("Interest: Mortgage", "16a", "expense", True, True, "Part II"),
    CategorySpec("Interest: Other", "16b", "expense", True, True, "Part II"),
    CategorySpec("Legal & Professional", "17", "expense", True, True, "Part II"),
    CategorySpec("Office Expenses", "18", "expense", True, True, "Part II"),
    CategorySpec("Pension & Profit Sharing", "19", "expense", True, True, "Part II"),
    CategorySpec("Rent or Lease: Vehicles & Machinery", "20a", "expense", True, True, "Part II"),
    CategorySpec("Rent or Lease: Other Business Property", "20b", "expense", True, True, "Part II"),
    CategorySpec("Repairs & Maintenance", "21", "expense", True, True, "Part II"),
    CategorySpec("Supplies", "22", "expense", True, True, "Part II"),
    CategorySpec("Taxes & Licenses", "23", "expense", True, True, "Part II"),
    CategorySpec("Travel & Meals: Travel", "24a", "expense", True, True, "Part II"),
    CategorySpec("Travel & Meals: Meals", "24b", "expense", True, True, "Part II"),
    CategorySpec("Utilities", "25", "expense", True, True, "Part II"),
    CategorySpec("Wages", "26", "expense", True, True, "Part II"),
    CategorySpec("Energy Efficient Buildings", "27a", "expense", True, True, "Part II"),

    # Part V / bookkeeping support
    CategorySpec("Other Expenses", "27b", "expense", True, True, "Part V"),
    CategorySpec("Sale of Property", "", "expense", True, False, ""),
]


# ------------------------------------------------------------------------------
# Subcategory seed list (name -> parent category name)
# ------------------------------------------------------------------------------

SUBCATEGORY_SPECS: list[tuple[str, str]] = [
    ("Accounting Services", "Legal & Professional"),
    ("Advertising", "Advertising"),
    ("Bank Fees", "Other Expenses"),
    ("Business Meals", "Other Expenses"),
    ("Cellular Service", "Utilities"),
    ("Cloud Services", "Other Expenses"),
    ("Commissions & Fees", "Commissions & Fees"),
    ("Contractors", "Contract Labor"),
    ("Depletion", "Depletion"),
    ("Depreciation", "Depreciation & Section 179"),
    ("Drone Services", "Gross Receipts"),
    ("Education", "Other Expenses"),
    ("Employee Retirement Contributions", "Pension & Profit Sharing"),
    ("Energy Efficient Buildings", "Energy Efficient Buildings"),
    ("Equipment: Computer", "Other Expenses"),
    ("Equipment: Drones", "Other Expenses"),
    ("Equipment: Office", "Other Expenses"),
    ("Equipment: Photography", "Other Expenses"),
    ("Insurance: Aviation", "Insurance"),
    ("Insurance: Business", "Insurance"),
    ("Insurance: Health", "Employee Benefits"),
    ("Insurance: Liability", "Insurance"),
    ("Internet", "Utilities"),
    ("Legal Services", "Legal & Professional"),
    ("Licensing & Fees", "Other Expenses"),
    ("Materials & Supplies", "Supplies"),
    ("Mortgage Interest", "Interest: Mortgage"),
    ("Office Supplies", "Office Expenses"),
    ("Other Interest", "Interest: Other"),
    ("Photography Services", "Gross Receipts"),
    ("Postage & Shipping", "Office Expenses"),
    ("Rental: Drone Equipment", "Rent or Lease: Other Business Property"),
    ("Rental: Machinery", "Rent or Lease: Vehicles & Machinery"),
    ("Rental: Photography Equipment", "Rent or Lease: Other Business Property"),
    ("Repairs: Drone Equipment", "Repairs & Maintenance"),
    ("Returns & Allowances", "Returns & Allowances"),
    ("Sale of Property", "Sale of Property"),
    ("Sales Tax Collected", "Gross Receipts"),
    ("Sales Tax Paid", "Taxes & Licenses"),
    ("Sales", "Gross Receipts"),
    ("Section 179", "Depreciation & Section 179"),
    ("Software", "Other Expenses"),
    ("Supplies: Photography", "Supplies"),
    ("Travel: Airfare", "Travel & Meals: Travel"),
    ("Travel: Car Rental", "Travel & Meals: Travel"),
    ("Travel: Gas", "Travel & Meals: Travel"),
    ("Travel: Hotels", "Travel & Meals: Travel"),
    ("Travel: Meals", "Travel & Meals: Meals"),
    ("Travel: Other", "Travel & Meals: Travel"),
    ("Travel: Parking & Tolls", "Travel & Meals: Travel"),
    ("Vehicle: Equipment Purchases", "Car & Truck Expenses"),
    ("Vehicle: Gas", "Car & Truck Expenses"),
    ("Vehicle: Loan Interest", "Car & Truck Expenses"),
    ("Vehicle: Loan Payments", "Car & Truck Expenses"),
    ("Vehicle: Maintenance", "Car & Truck Expenses"),
    ("Vehicle: Other Expenses", "Car & Truck Expenses"),
    ("Vehicle: Repairs", "Car & Truck Expenses"),
    ("Wages", "Wages"),
    ("Web Hosting", "Other Expenses"),
]


# ------------------------------------------------------------------------------
# Main seeding function
# ------------------------------------------------------------------------------

@transaction.atomic
def seed_schedule_c_defaults(business) -> None:
    cat_slug_max = _field_max_length(Category, "slug", 120)
    sub_slug_max = _field_max_length(SubCategory, "slug", 140)

    used_cat_slugs = set(
        Category.objects.filter(business=business)
        .exclude(slug__isnull=True)
        .exclude(slug="")
        .values_list("slug", flat=True)
    )
    used_sub_slugs = set(
        SubCategory.objects.filter(business=business)
        .exclude(slug__in=[None, ""])
        .values_list("slug", flat=True)
    )

    # ---- Create / update Categories ----
    categories_by_name: dict[str, Category] = {}

    for idx, spec in enumerate(CATEGORY_SPECS, start=1):
        desired_line = _schedule_c_choice(spec.schedule_c_line)

        cat, _created = Category.objects.get_or_create(
            business=business,
            name=spec.name,
            defaults={
                "slug": _unique_slug(spec.name, used_cat_slugs, cat_slug_max),
                "category_type": spec.category_type,
                "book_reports": spec.book_reports,
                "tax_reports": spec.tax_reports,
                "schedule_c_line": desired_line,
                "report_group": spec.report_group,
                "is_active": True,
                "sort_order": idx,
            },
        )

        updates: dict[str, object] = {}

        if cat.sort_order != idx:
            updates["sort_order"] = idx

        if not cat.slug:
            updates["slug"] = _unique_slug(spec.name, used_cat_slugs, cat_slug_max)

        if cat.category_type != spec.category_type:
            updates["category_type"] = spec.category_type

        if cat.book_reports != spec.book_reports:
            updates["book_reports"] = spec.book_reports

        if cat.tax_reports != spec.tax_reports:
            updates["tax_reports"] = spec.tax_reports

        if (cat.schedule_c_line or "") != (desired_line or ""):
            updates["schedule_c_line"] = desired_line

        if (cat.report_group or "") != (spec.report_group or ""):
            updates["report_group"] = spec.report_group

        if cat.is_active is not True:
            updates["is_active"] = True

        if updates:
            Category.objects.filter(pk=cat.pk).update(**updates)
            for k, v in updates.items():
                setattr(cat, k, v)

        categories_by_name[spec.name] = cat

    # ---- Seed list sanity: ensure SubCategory names are unique within the seed list ----
    seen: set[str] = set()
    dupes: set[str] = set()
    for name, _parent in SUBCATEGORY_SPECS:
        if name in seen:
            dupes.add(name)
        seen.add(name)

    if dupes:
        raise ValueError(f"Duplicate SubCategory names in SUBCATEGORY_SPECS: {sorted(dupes)}")

    # ---- Create / update SubCategories ----
    existing_subs = {
        s.name: s
        for s in SubCategory.objects.filter(business=business).select_related("category")
    }

    to_create: list[SubCategory] = []

    for sub_name, parent_cat_name in SUBCATEGORY_SPECS:
        parent = categories_by_name.get(parent_cat_name)
        if not parent:
            raise ValueError(
                f"Missing parent Category '{parent_cat_name}' for SubCategory '{sub_name}'"
            )

        existing = existing_subs.get(sub_name)

        if existing:
            updates: dict[str, object] = {}

            if existing.category_id != parent.id:
                updates["category_id"] = parent.id

            if not existing.slug:
                updates["slug"] = _unique_slug(sub_name, used_sub_slugs, sub_slug_max)

            if getattr(existing, "is_active", True) is not True:
                updates["is_active"] = True

            if updates:
                SubCategory.objects.filter(pk=existing.pk).update(**updates)

            continue

        to_create.append(
            SubCategory(
                business=business,
                category=parent,
                name=sub_name,
                slug=_unique_slug(sub_name, used_sub_slugs, sub_slug_max),
                is_active=True,
            )
        )

    if to_create:
        SubCategory.objects.bulk_create(to_create)



def _period_from_params(*, today: date, mode: str, year: int | None) -> tuple[date, date, str, str]:
    """Return (start_date, end_date, label, selected_year_value)."""

    mode = (mode or "rolling").strip().lower()

    if mode == "year" and year:
        start_date = date(year, 1, 1)
        end_date = date(year, 12, 31)
        return start_date, end_date, str(year), str(year)

    if mode == "month":
        # Current month-to-date
        start_date = date(today.year, today.month, 1)
        end_date = today
        # Label can be "Current month" or "Feb 2026" — your call.
        return start_date, end_date, "Current month", "month"

    # default rolling 12 months inclusive of current month
    start_date = _first_day_n_months_ago(date(today.year, today.month, 1), 11)
    end_date = today
    return start_date, end_date, "Rolling 12 months", "rolling"