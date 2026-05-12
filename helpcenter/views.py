from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Sum
from django.utils import timezone
from django.views.generic import TemplateView

from accounts.models import CompanyProfile
from ledger.models import Category, Contact, Job, Transaction

try:
    from vehicles.models import Vehicle, VehicleMiles  # type: ignore
except Exception:  # pragma: no cover
    Vehicle = None  # type: ignore
    VehicleMiles = None  # type: ignore


@dataclass
class CheckItem:
    key: str
    title: str
    detail: str
    is_done: bool
    url_name: str | None = None
    url_label: str | None = None


def _bool_icon(is_done: bool) -> dict[str, str]:
    # Template uses these for consistent icons/colors.
    return {
        "icon": "fa-circle-check" if is_done else "fa-triangle-exclamation",
        "cls": "text-success" if is_done else "text-warning",
        "badge": "Complete" if is_done else "Needs attention",
        "badge_cls": "text-bg-success" if is_done else "text-bg-warning",
    }


class HelpHomeView(LoginRequiredMixin, TemplateView):
    template_name = "helpcenter/home.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx["cards"] = [
            {
                "title": "Setup",
                "subtitle": "Get your business ready for tracking and reporting",
                "icon": "fa-screwdriver-wrench",
                "url_name": "helpcenter:setup",
            },
            {
                "title": "Financials",
                "subtitle": "A guided workflow from Job → Transactions → Invoice → Review → Reports",
                "icon": "fa-diagram-project",
                "url_name": "helpcenter:financials",
            },
        ]
        return ctx


class SetupHelpView(LoginRequiredMixin, TemplateView):
    template_name = "helpcenter/setup.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        business = getattr(self.request, "business", None)

        # Business profile
        profile = None
        if business is not None:
            profile = CompanyProfile.objects.filter(business=business).first()
        profile_complete = bool(profile and getattr(profile, "is_complete", False))

        # Defaults/categories
        has_categories = Category.objects.filter(business=business).exists() if business else False

        # Contacts/clients
        contacts_qs = Contact.objects.filter(business=business) if business else Contact.objects.none()
        has_contacts = contacts_qs.exists()
        # If client_code exists (some branches), encourage at least one code.
        has_any_client_code = False
        if has_contacts:
            if hasattr(Contact, "client_code"):
                has_any_client_code = contacts_qs.exclude(client_code__isnull=True).exclude(client_code__exact="").exists()
            else:
                has_any_client_code = True  # Can't verify; treat as satisfied.

        # Vehicles / mileage (optional but useful)
        has_vehicle = False
        has_mileage = False
        if Vehicle is not None and VehicleMiles is not None and business is not None:
            try:
                has_vehicle = Vehicle.objects.filter(business=business).exists()
                has_mileage = VehicleMiles.objects.filter(business=business).exists()
            except Exception:
                has_vehicle = False
                has_mileage = False

        # Jobs / Transactions / Invoices
        has_jobs = Job.objects.filter(business=business).exists() if business else False
        has_transactions = Transaction.objects.filter(business=business).exists() if business else False

        # invoices app may not be installed in some configs; be defensive.
        has_invoices = False
        try:
            from invoices.models import Invoice  # type: ignore
            has_invoices = Invoice.objects.filter(business=business).exists() if business else False
        except Exception:
            has_invoices = False

        items: list[CheckItem] = [
            CheckItem(
                key="company_profile",
                title="Complete business profile",
                detail="Add your business details used on invoices and PDFs.",
                is_done=profile_complete,
                url_name="accounts:settings",
                url_label="Open Settings",
            ),
            CheckItem(
                key="seed_defaults",
                title="Seed default categories",
                detail="Create the baseline categories/subcategories for reporting.",
                is_done=has_categories,
                url_name="accounts:settings",
                url_label="Seed defaults",
            ),
            CheckItem(
                key="contacts",
                title="Add clients / contacts",
                detail="Create at least one client so you can create jobs and invoices.",
                is_done=has_contacts and has_any_client_code,
                url_name="ledger:contact_list",
                url_label="Go to Contacts",
            ),
            CheckItem(
                key="vehicles",
                title="Add a vehicle (optional)",
                detail="Add a vehicle if you plan to track mileage or vehicle expenses.",
                is_done=has_vehicle,
                url_name="vehicles:vehicle_list",
                url_label="Go to Vehicles",
            ),
            CheckItem(
                key="mileage",
                title="Log mileage (optional)",
                detail="Create mileage entries linked to a job/invoice to support deductions and reporting.",
                is_done=has_mileage,
                url_name="vehicles:vehicle_miles_list",
                url_label="Open Mileage Log",
            ),
            CheckItem(
                key="jobs",
                title="Create your first job",
                detail="Jobs are the backbone of your workflow and tie together transactions, invoices, and mileage.",
                is_done=has_jobs,
                url_name="ledger:job_list",
                url_label="Go to Jobs",
            ),
            CheckItem(
                key="transactions",
                title="Add your first transactions",
                detail="Record income and expenses and link them to jobs when possible.",
                is_done=has_transactions,
                url_name="ledger:transaction_list",
                url_label="Go to Transactions",
            ),
            CheckItem(
                key="invoices",
                title="Create your first invoice",
                detail="Create an invoice from a job and verify totals and status.",
                is_done=has_invoices,
                url_name="invoices:invoice_list",
                url_label="Go to Invoices",
            ),
        ]

        done_count = sum(1 for i in items if i.is_done)
        ctx["items"] = [
            {**i.__dict__, **_bool_icon(i.is_done)} for i in items
        ]
        ctx["done_count"] = done_count
        ctx["total_count"] = len(items)
        ctx["progress_pct"] = int((done_count / max(1, len(items))) * 100)

        # Next recommended action
        next_item = next((i for i in items if not i.is_done), None)
        ctx["next_item"] = {**next_item.__dict__, **_bool_icon(next_item.is_done)} if next_item else None

        return ctx


class FinancialsHelpView(LoginRequiredMixin, TemplateView):
    template_name = "helpcenter/financials.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        business = getattr(self.request, "business", None)

        has_jobs = Job.objects.filter(business=business).exists() if business else False
        has_transactions = Transaction.objects.filter(business=business).exists() if business else False

        has_invoices = False
        unpaid_invoices = 0
        try:
            from invoices.models import Invoice  # type: ignore
            if business:
                inv_qs = Invoice.objects.filter(business=business)
                has_invoices = inv_qs.exists()
                if hasattr(Invoice, "status"):
                    unpaid_invoices = inv_qs.exclude(status="paid").count()
        except Exception:
            has_invoices = False
            unpaid_invoices = 0

        # Determine recommended next step
        if not has_jobs:
            next_step = "Create a job"
        elif not has_transactions:
            next_step = "Add transactions"
        elif not has_invoices:
            next_step = "Create an invoice"
        else:
            next_step = "Review invoices and run reports"

        steps = [
            {
                "num": 1,
                "title": "Create a job",
                "why": "Jobs are your workflow container — link transactions, invoices, and mileage to the same job.",
                "check": has_jobs,
                "links": [
                    {"label": "Jobs", "url_name": "ledger:job_list", "icon": "fa-briefcase"},
                ],
                "tips": [
                    "Pick the correct Client and job year.",
                    "Use a clear job label (ex: “Phoenix 2026 – Drone Coverage”).",
                ],
            },
            {
                "num": 2,
                "title": "Create transactions",
                "why": "Record income and expenses as they happen. Link to a job whenever possible for clean reporting.",
                "check": has_transactions,
                "links": [
                    {"label": "Add Transaction", "url_name": "ledger:transaction_create", "icon": "fa-plus"},
                    {"label": "Transactions", "url_name": "ledger:transaction_list", "icon": "fa-list"},
                ],
                "tips": [
                    "Mark refunds using the refund toggle so totals stay accurate.",
                    "Use vehicle + transport type for vehicle expenses and mileage context.",
                ],
            },
            {
                "num": 3,
                "title": "Create an invoice",
                "why": "Invoices are billing documents tied to a job. You can create multiple invoices for the same job.",
                "check": has_invoices,
                "links": [
                    {"label": "Create Invoice", "url_name": "invoices:invoice_create", "icon": "fa-file-invoice-dollar"},
                    {"label": "Invoices", "url_name": "invoices:invoice_list", "icon": "fa-file-invoice"},
                ],
                "tips": [
                    "Confirm job + client details are correct before sending.",
                    "Set status (Draft/Sent/Paid) to keep AR accurate.",
                ],
            },
            {
                "num": 4,
                "title": "Invoice review",
                "why": "Review ensures totals, mileage links, and status are correct before reporting.",
                "check": has_invoices,
                "links": [
                    {"label": "Invoice Review", "url_name": "invoices:invoice_list", "icon": "fa-magnifying-glass-chart"},
                ],
                "tips": [
                    "Attach mileage logs to the invoice when mileage is billable or reimbursable.",
                    "Verify invoice totals match expected job revenue.",
                ],
            },
            {
                "num": 5,
                "title": "Run reports",
                "why": "Reports summarize your performance and tax categories by year and mode (Tax vs Books).",
                "check": True,
                "links": [
                    {"label": "Reports", "url_name": "reports:home", "icon": "fa-chart-line"},
                    {"label": "Profit & Loss", "url_name": "reports:profit_loss", "icon": "fa-receipt"},
                    {"label": "Operating Expenses", "url_name": "reports:schedule_c", "icon": "fa-file-invoice"},
                ],
                "tips": [
                    "Select the correct year and mode (Tax vs Books).",
                    "Use PDFs for sharing/archiving.",
                ],
            },
        ]

        ctx["next_step"] = next_step
        ctx["unpaid_invoices"] = unpaid_invoices
        ctx["steps"] = [{**s, **_bool_icon(s["check"])} for s in steps]
        return ctx
