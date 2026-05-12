# ledger/services.py
from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils.text import slugify

from .models import Category, SubCategory


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