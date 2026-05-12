from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from core.models import Business, BusinessOwnedModelMixin
from assets.models import Asset
from vehicles.models import Vehicle
from django.db import transaction
from django.db.models import Max


def current_year() -> int:
    return timezone.now().year






class Category(BusinessOwnedModelMixin):
    class CategoryType(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    class ScheduleCLine(models.TextChoices):
        # -------------------------
        # Part I — Income
        # -------------------------
        GROSS_RECEIPTS = "gross_receipts", "1"
        RETURNS_ALLOWANCES = "returns_allowances", "2"

        # -------------------------
        # Part II — Expenses
        # -------------------------
        ADVERTISING = "advertising", "8"
        CAR_TRUCK = "car_truck", "9"
        COMMISSIONS_FEES = "commissions_fees", "10"
        CONTRACT_LABOR = "contract_labor", "11"
        DEPLETION = "depletion", "12"
        DEPRECIATION = "depreciation", "13"
        EMPLOYEE_BENEFITS = "employee_benefits", "14"
        INSURANCE = "insurance", "15"
        INTEREST_MORTGAGE = "interest_mortgage", "16a"
        INTEREST_OTHER = "interest_other", "16b"
        LEGAL_PRO = "legal_pro", "17"
        OFFICE = "office", "18"
        PENSION_PROFIT = "pension_profit_sharing", "19"
        RENT_LEASE_VEHICLES = "rent_lease_vehicles", "20a"
        RENT_LEASE_OTHER = "rent_lease_other", "20b"
        REPAIRS = "repairs", "21"
        SUPPLIES = "supplies", "22"
        TAXES_LICENSES = "taxes_licenses", "23"
        TRAVEL = "travel", "24a"
        MEALS = "meals", "24b"
        UTILITIES = "utilities", "25"
        WAGES = "wages", "26"
        ENERGY_EFFICIENT = "energy_efficient_buildings", "27a"


        # -------------------------
        # Part V — Other expenses
        # -------------------------
        OTHER_EXPENSES_V = "other_expenses_part_v", "27b"

    schedule_c_line = models.CharField(max_length=30, choices=ScheduleCLine.choices, blank=True, default="",)
    name              = models.CharField(max_length=80)
    slug              = models.SlugField(max_length=120, blank=True)
    category_type     = models.CharField(max_length=10, choices=CategoryType.choices)
    is_active         = models.BooleanField(default=True)
    sort_order        = models.PositiveIntegerField(default=0)
    book_reports      = models.BooleanField(default=True)
    tax_reports       = models.BooleanField(default=True)

    report_group      = models.CharField(max_length=60, blank=True, default="")

    class Meta:
        ordering = ["category_type", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "name", "category_type"],
                name="uniq_category_business_name_type",
            ),
            models.UniqueConstraint(
                fields=["business", "category_type", "slug"],
                name="uniq_category_business_type_slug",
            ),
        ]

    def clean(self):
        super().clean()
        if self.slug:
            self.slug = slugify(self.slug)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.get_category_type_display()}: {self.name}"










class SubCategory(BusinessOwnedModelMixin):
    class DeductionRule(models.TextChoices):
        FULL          = "full", "100% deductible"
        MEALS_50      = "meals_50", "Meals (50%)"
        NONDEDUCTIBLE = "nondeductible", "Not deductible"

    class ContactRole(models.TextChoices):
        ANY           = "any", "Any"
        VENDOR        = "vendor", "Vendor"
        CONTRACTOR    = "contractor", "Contractor"
        CUSTOMER      = "customer", "Customer"


    class AccountType(models.TextChoices):
        EXPENSE       = "expense", "Expense"
        INCOME        = "income", "Income"
        ASSET         = "asset", "Asset"
        LIABILITY     = "liability", "Liability"
        JOURNAL       = "journal", "Journal Only"

    category                   = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="subcategories")
    name                       = models.CharField(max_length=80)
    slug                       = models.SlugField(max_length=140, blank=True, null=True)
    is_active                  = models.BooleanField(default=True)
    sort_order                 = models.PositiveIntegerField(default=0)
    book_enabled               = models.BooleanField(default=True)
    tax_enabled                = models.BooleanField(default=True)
    schedule_c_line            = models.CharField(max_length=30, choices=Category.ScheduleCLine.choices, blank=True, default="", help_text="Optional override. If blank, reports may use Category.schedule_c_line.",)
    deduction_rule             = models.CharField(max_length=20, choices=DeductionRule.choices, default=DeductionRule.FULL,)  
    is_1099_reportable_default = models.BooleanField(default=False)
    is_capitalizable           = models.BooleanField(default=False)
    account_type               = models.CharField(max_length=20, choices=AccountType.choices, default=AccountType.EXPENSE)
    requires_asset             = models.BooleanField(default=False)
    requires_receipt           = models.BooleanField(default=False)
    requires_team              = models.BooleanField(default=False)
    requires_job               = models.BooleanField(default=False)
    requires_invoice_number    = models.BooleanField(default=False)
    requires_contact           = models.BooleanField(default=False)
    contact_role               = models.CharField(max_length=15, choices=ContactRole.choices, default=ContactRole.ANY)   
    requires_transport         = models.BooleanField(default=False)
    requires_vehicle           = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "category", "name"],
                name="uniq_subcategory_business_cat_name",
            ),
            models.UniqueConstraint(
                fields=["business", "slug"],
                condition=Q(slug__isnull=False) & ~Q(slug=""),
                name="uniq_subcategory_business_slug_nonblank",
            ),
        ]

    def clean(self):
        super().clean()

        if self.category_id and self.business_id and self.category.business_id != self.business_id:
            raise ValidationError({"category": "Category does not belong to this business."})

        if self.slug:
            self.slug = slugify(self.slug)
            
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        self.full_clean()
        return super().save(*args, **kwargs)

    def effective_schedule_c_line(self) -> str:
        return self.schedule_c_line or (
            self.category.schedule_c_line if self.category_id else ""
        )

    def __str__(self) -> str:
        return f"{self.name}"

    def is_book_visible(self) -> bool:
        return self.book_enabled and self.category.book_reports

    def is_tax_visible(self) -> bool:
        return self.tax_enabled and self.category.tax_reports




class Job(BusinessOwnedModelMixin):
    class JobType(models.TextChoices):
        COMMERCIAL = "commercial", "Commercial"
        REAL_ESTATE = "real_estate", "Real Estate"
        INSPECTION = "inspection", "Inspection"
        CONSTRUCTION = "construction", "Construction"
        PHOTOGRAPHY = "photography", "Photography"
        MAPPING = "mapping", "Mapping"
        TRAINING = "training", "Training"
        INTERNAL = "internal", "Internal"
        OTHER = "other", "Other"

    # Stable job label shown throughout the UI (invoice lists, etc.).
    label = models.CharField(max_length=255)

    # Year + sequence support for sorting/reporting.
    job_year = models.PositiveIntegerField(default=current_year)
    job_seq = models.PositiveIntegerField(default=0, editable=False)

    # Generated identifier using client_code + year + seq.
    job_number = models.CharField(max_length=30, blank=True, editable=False)
    client = models.ForeignKey(
        "Contact",
        on_delete=models.PROTECT,
        related_name="client_jobs",
        null=True,
        blank=True,
        help_text="Optional. Select a Contact marked as a Customer.",
    )
    job_type = models.CharField(
        max_length=20,
        choices=JobType.choices,
        default=JobType.OTHER,
    )
    city = models.CharField(max_length=120, blank=True)
    address = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_active", "-job_year", "job_number", "label"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "job_number"],
                name="uniq_job_business_job_number",
            ),
            models.UniqueConstraint(
                fields=["business", "job_year", "job_seq"],
                name="uniq_job_business_year_seq",
            ),
        ]

    def clean(self):
        super().clean()

        if self.client_id and self.business_id and self.client.business_id != self.business_id:
            raise ValidationError({"client": "Client does not belong to this business."})

        if self.job_year:
            try:
                self.job_year = int(self.job_year)
            except (TypeError, ValueError):
                raise ValidationError({"job_year": "Year must be a valid number."})

    @staticmethod
    def is_general_job_value(*, job_number: str = "", label: str = "", job_type: str = "") -> bool:
        """Return True for non-invoice/general jobs that should not consume a sequence.

        General jobs are useful as a bucket for non-invoiced work, but they should
        not affect the invoice-aligned YY#### sequence used by customer jobs.
        """
        number = (job_number or "").strip().upper()
        label_value = (label or "").strip().upper()
        type_value = (job_type or "").strip().lower()
        return (
            number.startswith("GENERAL")
            or label_value.startswith("GENERAL")
            or type_value == Job.JobType.INTERNAL
        )

    @property
    def is_general_job(self) -> bool:
        return self.is_general_job_value(
            job_number=self.job_number,
            label=self.label,
            job_type=self.job_type,
        )

    @staticmethod
    def invoice_sequence_queryset(*, business, year):
        """Jobs that participate in the invoice/job YY#### sequence."""
        return (
            Job.objects.filter(business=business, job_year=year)
            .exclude(job_number__istartswith="GENERAL")
            .exclude(label__istartswith="GENERAL")
            .exclude(job_type=Job.JobType.INTERNAL)
            .exclude(job_seq__gte=9000)
        )

    def _allocate_job_number(self) -> None:
        """Allocate job_seq + job_number.

        Format: <PREFIX>-<YY><NNNN>
        Example: NHRA-260001

        The numeric sequence is global per Business + Year and is shared with
        invoices. General/internal jobs are intentionally excluded so a bucket
        such as GENERAL-2026 does not consume an invoice number.
        """
        year = int(self.job_year or timezone.now().year)
        yy = str(year)[-2:]

        # General/internal jobs are allowed, but they do not reserve YY####.
        if self.is_general_job_value(label=self.label, job_type=self.job_type):
            self.job_year = year
            self.job_seq = 0
            self.job_number = f"GENERAL-{year}"
            return

        prefix = "JOB"
        if self.client_id and (self.client.client_code or "").strip():
            prefix = self.client.client_code.strip().upper()

        with transaction.atomic():
            max_job_seq = (
                Job.invoice_sequence_queryset(business=self.business, year=year)
                .aggregate(m=Max("job_seq"))
                .get("m")
            ) or 0

            max_invoice_seq = 0
            try:
                # Local import avoids a module-level circular import.
                from invoices.models import Invoice, _parse_numeric_invoice_number

                invoice_numbers = (
                    Invoice.objects.filter(
                        business=self.business,
                        issue_date__year=year,
                        invoice_number__regex=r"^[0-9]{6}$",
                    )
                    .values_list("invoice_number", flat=True)
                )
                for invoice_number in invoice_numbers:
                    parsed = _parse_numeric_invoice_number(invoice_number)
                    if parsed and parsed[0] == int(yy):
                        max_invoice_seq = max(max_invoice_seq, parsed[1])
            except Exception:
                # Job creation should not fail if the invoices app is unavailable
                # during migrations/tests. The database uniqueness constraints still
                # protect against collisions.
                max_invoice_seq = 0

            next_seq = max(int(max_job_seq or 0), int(max_invoice_seq or 0)) + 1

            self.job_year = year
            self.job_seq = next_seq
            self.job_number = f"{prefix}-{yy}{next_seq:04d}"

    def __str__(self) -> str:
        return f"{self.job_number} • {self.label}"

    def save(self, *args, **kwargs):
        if not self.job_number:
            if not self.business_id:
                raise ValidationError(
                    {"business": "Business is required before generating a Job Number."}
                )
            self._allocate_job_number()

        self.full_clean()
        return super().save(*args, **kwargs)






class Team(models.Model):
    business         = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="teams")
    name             = models.CharField(max_length=120)
    is_active        = models.BooleanField(default=True)
    sort_order       = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business", "name"], name="uniq_team_per_business_name"),
        ]
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name






class Transaction(BusinessOwnedModelMixin):
    TRANSPORT_CHOICES = [
        ("", "—"),
        ("personal_vehicle", "Personal vehicle"),
        ("rental_car", "Rental car"),
        ("business_vehicle", "Business vehicle"),
    ]

    class TransactionType(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"
        ASSET = "asset", "Asset"
        LIABILITY = "liability", "Liability"
        JOURNAL = "journal", "Journal Only"


    date              = models.DateField(default=timezone.now)
    amount            = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    description       = models.CharField(max_length=255)
    subcategory       = models.ForeignKey(SubCategory, on_delete=models.PROTECT, related_name="transactions")
    category          = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="transactions", editable=False)
    trans_type        = models.CharField(max_length=10, choices=TransactionType.choices, editable=False)
    is_refund         = models.BooleanField(default=False)
    contact           = models.ForeignKey('Contact', on_delete=models.PROTECT, related_name="transactions", null=True, blank=True)
    team              = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="transactions", null=True, blank=True)
    job               = models.ForeignKey(Job, on_delete=models.PROTECT, related_name="transactions", null=True, blank=True)
    invoice_number    = models.CharField(max_length=25, blank=True)
    receipt           = models.FileField(upload_to="receipts/transactions/", blank=True, null=True)
    asset             = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="transactions", null=True, blank=True)
    transport_type    = models.CharField(max_length=20, choices=TRANSPORT_CHOICES, blank=True, default="")
    vehicle           = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="transactions", null=True, blank=True,)
    notes             = models.TextField(blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-id"]

    def clean(self):
        super().clean()

        # -------------------------
        # Tenant consistency checks
        # -------------------------
        if self.subcategory_id and self.business_id and self.subcategory.business_id != self.business_id:
            raise ValidationError({"subcategory": "Subcategory does not belong to this business."})

        if self.category_id and self.business_id and self.category.business_id != self.business_id:
            raise ValidationError({"category": "Category does not belong to this business."})

        if self.contact_id and self.business_id and self.contact.business_id != self.business_id:
            raise ValidationError({"contact": "Contact does not belong to this business."})

        if self.job_id and self.business_id and self.job.business_id != self.business_id:
            raise ValidationError({"job": "Job does not belong to this business."})

        if self.vehicle_id and self.business_id and self.vehicle.business_id != self.business_id:
            raise ValidationError({"vehicle": "Vehicle does not belong to this business."})

        # -------------------------
        # Auto category consistency
        # -------------------------
        if self.subcategory_id:
            expected = self.subcategory.category_id
            if self.category_id and self.category_id != expected:
                raise ValidationError({"category": "Category must match the selected subcategory."})


        # -------------------------
        # Amount validation
        # -------------------------
        if self.amount is not None and self.amount < 0:
            raise ValidationError({"amount": "Amount must be positive."})

        if not self.subcategory_id:
            return
        # -------------------------
        # Auto type consistency (driven by SubCategory.account_type)
        # -------------------------
        sc = self.subcategory
        expected_type = (sc.account_type or "expense").lower()
        valid_types = {c[0] for c in Transaction.TransactionType.choices}
        if expected_type not in valid_types:
            expected_type = Transaction.TransactionType.EXPENSE

        if self.trans_type and self.trans_type != expected_type:
            raise ValidationError({"trans_type": "Transaction type must match the selected subcategory."})

        # contact rules (only required for certain subcategories)
        if sc.requires_contact and not self.contact_id:
            raise ValidationError({"contact": "Select a contact."})

        # receipt / team / job / invoice rules
        if sc.requires_receipt and not self.receipt:
            raise ValidationError({"receipt": "Upload a receipt."})

        if sc.requires_team and not self.team_id:
            raise ValidationError({"team": "Select a team."})

        if sc.requires_job and not self.job_id:
            raise ValidationError({"job": "Select a job."})

        if sc.requires_invoice_number and not (self.invoice_number or "").strip():
            raise ValidationError({"invoice_number": "Enter an invoice number."})

        # asset rule (for depreciation / 179 / capitalizable items)
        if sc.requires_asset and not self.asset_id:
            raise ValidationError({"asset": "Select an asset."})

        role = (sc.contact_role or "any").lower()
        if self.contact_id and role != "any":
            if role == "contractor" and not self.contact.is_contractor:
                raise ValidationError({"contact": "Select a contact marked as a contractor."})
            if role == "vendor" and not self.contact.is_vendor:
                raise ValidationError({"contact": "Select a contact marked as a vendor."})
            if role == "customer" and not self.contact.is_customer:
                raise ValidationError({"contact": "Select a contact marked as a customer."})


        # Transport + Vehicle rules
        if sc.requires_transport:
            if not self.transport_type:
                raise ValidationError({"transport_type": "Select a transport type."})

            valid = {"personal_vehicle", "rental_car", "business_vehicle"}
            if self.transport_type not in valid:
                raise ValidationError({"transport_type": "Invalid transport type."})

            if self.transport_type == "business_vehicle":
                if not self.vehicle_id:
                    raise ValidationError({"vehicle": "Select a business vehicle."})
            else:
                if self.vehicle_id:
                    raise ValidationError({"vehicle": "Remove vehicle; only used for business vehicles."})
        else:
            if self.transport_type:
                raise ValidationError({"transport_type": "Remove transport type; it is not needed for this subcategory."})
            # Allow vehicle only when this subcategory explicitly requires a vehicle.
            if self.vehicle_id and not sc.requires_vehicle:
                raise ValidationError({"vehicle": "Remove vehicle; it is not needed for this subcategory."})

        if sc.requires_vehicle and not self.vehicle_id:
            raise ValidationError({"vehicle": "Select a vehicle for this subcategory."})

    def save(self, *args, **kwargs):
        if self.subcategory_id:
            sc = self.subcategory
            self.category = sc.category

            # Drive posting type from the subcategory's accounting nature
            self.trans_type = (sc.account_type or Transaction.TransactionType.EXPENSE).lower()

            # Auto-mark returns & allowances as refunds (keeps reporting clean)
            if sc.effective_schedule_c_line() == Category.ScheduleCLine.RETURNS_ALLOWANCES:
                self.is_refund = True
                self.trans_type = Transaction.TransactionType.INCOME

            # Optional: auto-create an Asset record for capitalizable purchases
            if sc.account_type == SubCategory.AccountType.ASSET and sc.is_capitalizable and not self.asset_id:
                self.asset = Asset.objects.create(
                    business=self.business,
                    name=(self.description or "Asset")[:200],
                    placed_in_service=self.date,
                    cost_basis=self.amount or Decimal("0.00"),
                )

        self.full_clean()
        return super().save(*args, **kwargs)
    
    def is_meals_50(self) -> bool:
        sc = getattr(self, "subcategory", None)
        if not sc:
            return False

        rule = (getattr(sc, "deduction_rule", "") or "").strip().lower()
        if rule == SubCategory.DeductionRule.MEALS_50:
            return True

        slug = (getattr(sc, "slug", "") or "").strip().lower()
        if slug == "meals" or slug.endswith("-meals") or slug.endswith("_meals"):
            return True

        line = (sc.effective_schedule_c_line() or "").strip().lower()
        if line == Category.ScheduleCLine.MEALS:
            return True

        name = (getattr(sc, "name", "") or "").strip().lower()
        return "meal" in name

    def is_travel_gas(self) -> bool:
        sc = getattr(self, "subcategory", None)
        if not sc:
            return False

        slug = (getattr(sc, "slug", "") or "").strip().lower()
        if slug in {"fuel", "gas", "travel-gas", "travel_fuel"}:
            return True
        if slug.endswith("-fuel") or slug.endswith("-gas") or slug.endswith("_fuel") or slug.endswith("_gas"):
            return True

        name = (getattr(sc, "name", "") or "").strip().lower()
        if name == "travel: gas" or name == "travel gas":
            return True

        return "gas" in name or "fuel" in name

    def deductible_amount(self) -> Decimal:
        """
        Return the tax-deductible portion of this transaction.

        Books/invoice-review totals should continue to use ``amount``.
        Taxable / Schedule C totals should use this method.
        """
        amt = Decimal(self.amount or Decimal("0.00"))
        sc = getattr(self, "subcategory", None)

        if not sc:
            return amt

        rule = (getattr(sc, "deduction_rule", "") or "").strip().lower()

        if rule == SubCategory.DeductionRule.NONDEDUCTIBLE:
            return Decimal("0.00")

        if self.is_meals_50():
            return (amt * Decimal("0.50")).quantize(Decimal("0.01"))

        if self.is_travel_gas():
            transport = (self.transport_type or "").strip().lower()

            if transport == "personal_vehicle":
                return Decimal("0.00")

            if transport == "rental_car":
                return amt

            # For business vehicles or any future valid transport type,
            # keep the actual amount unless business rules change.
            return amt

        return amt

    @property
    def effective_amount(self) -> Decimal:
        """Amount with refund/reversal applied (refunds reduce totals)."""
        if self.amount is None:
            return Decimal("0.00")
        return -self.amount if self.is_refund else self.amount








class Contact(BusinessOwnedModelMixin):
    display_name = models.CharField(max_length=255)
    # Locked identifier used for Job numbering (e.g., "NHRA", "ESPN").
    # Intentionally separate from display_name so renames don't affect historical numbers.
    client_code = models.CharField(
        max_length=25,
        blank=True,
        help_text='Short code used for Job Numbers (locked once set). Example: "NHRA", "ESPN"',
    )
    legal_name           = models.CharField(max_length=255, blank=True)
    business_name        = models.CharField(max_length=255, blank=True)
    email                = models.EmailField(blank=True)
    phone                = models.CharField(max_length=50, blank=True)
    address1             = models.CharField(max_length=255, blank=True)
    address2             = models.CharField(max_length=255, blank=True)
    city                 = models.CharField(max_length=120, blank=True)
    state                = models.CharField(max_length=50, blank=True)
    zip_code             = models.CharField(max_length=20, blank=True)
    country              = models.CharField(max_length=50, blank=True, default="US")
    is_vendor            = models.BooleanField(default=True)
    is_customer          = models.BooleanField(default=False)
    is_contractor        = models.BooleanField(default=False)

    # ------------------------------------------------------------------
    # Contractor / Tax classification (only meaningful when is_contractor=True)
    # ------------------------------------------------------------------
    contractor_number    = models.CharField(
        max_length=20,
        blank=True,
        help_text="Optional human-friendly ID, e.g. C-00023",
    )

    ENTITY_CHOICES = [
        ("individual", "Individual / Sole Proprietor"),
        ("llc", "LLC"),
        ("partnership", "Partnership"),
        ("c_corp", "C Corporation"),
        ("s_corp", "S Corporation"),
        ("other", "Other"),
    ]
    entity_type          = models.CharField(max_length=25, choices=ENTITY_CHOICES, blank=True)

    TIN_CHOICES          = [("ssn", "SSN"), ("ein", "EIN")]
    tin_type             = models.CharField(max_length=10, choices=TIN_CHOICES, blank=True)
    tin_last4            = models.CharField(
        max_length=4,
        blank=True,
        validators=[RegexValidator(r"^\d{4}$", "Enter last 4 digits.")],
        help_text="Last 4 digits only. Do not store full TIN.",
    )

    is_1099_eligible     = models.BooleanField(
        default=True,
        help_text="Whether this contractor should receive a 1099 (default True; you can override).",
    )

    W9_STATUS = [
        ("missing", "Missing"),
        ("requested", "Requested"),
        ("received", "Received"),
        ("verified", "Verified"),
    ]
    w9_status            = models.CharField(max_length=15, choices=W9_STATUS, default="missing")
    w9_sent_date         = models.DateField(null=True, blank=True)
    w9_received_date     = models.DateField(null=True, blank=True)
    w9_document          = models.FileField(upload_to="w9/", blank=True, null=True)

    edelivery_consent      = models.BooleanField(default=False)
    edelivery_consent_date = models.DateTimeField(null=True, blank=True)

    contractor_notes     = models.TextField(blank=True)

    # Housekeeping
    is_active            = models.BooleanField(default=True)

    class Meta:
        db_table = "ledger_contact"
        verbose_name = "Contact"
        verbose_name_plural = "Contacts"
        ordering = ["display_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "display_name"],
                name="uniq_contact_display_name_per_business",
            )
            ,
            models.UniqueConstraint(
                fields=["business", "client_code"],
                condition=Q(client_code__isnull=False) & ~Q(client_code=""),
                name="uniq_contact_client_code_per_business_nonblank",
            )
        ]

    def clean(self):
        super().clean()

        # Normalize client_code.
        if self.client_code:
            self.client_code = (self.client_code or "").strip().upper()

        # Lock client_code once created.
        if self.pk:
            prev = Contact.objects.filter(pk=self.pk).values_list("client_code", flat=True).first()
            if prev is not None and (prev or "") != (self.client_code or ""):
                raise ValidationError({
                    "client_code": "Client Code is locked once set. Create a new client to use a different code.",
                })

        # Contractor rules (only when is_contractor=True)
        if self.is_contractor:
            if not (self.entity_type or "").strip():
                raise ValidationError({"entity_type": "Tax classification is required for contractors."})

        # TIN last4 requires TIN type
        if (self.tin_last4 or "").strip() and not (self.tin_type or "").strip():
            raise ValidationError({"tin_type": "Select SSN or EIN when entering last-4 digits."})

        # Auto-stamp e-delivery consent date
        if self.edelivery_consent and not self.edelivery_consent_date:
            self.edelivery_consent_date = timezone.now()

    @classmethod
    def get_unknown(cls, *, business: Business) -> "Contact":
        """Return (and create if needed) the default placeholder contact for imports/review."""
        obj, _created = cls.objects.get_or_create(
            business=business,
            display_name="Unknown / Needs Review",
            defaults={
                "is_vendor": True,
                "is_customer": True,
                "is_contractor": False,
            },
        )
        return obj

    def __str__(self) -> str:
        return self.display_name



