from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.models import BusinessOwnedModelMixin
from ledger.models import Contact, Job, SubCategory, Transaction


class InvoiceCounter(BusinessOwnedModelMixin):
    """Per-business, per-year invoice counter for numeric YY#### sequences."""

    year = models.PositiveIntegerField()
    last_seq = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("business", "year"), name="uniq_invoice_counter_business_year"),
        ]

    def __str__(self) -> str:
        return f"{self.business_id}:{self.year} -> {self.last_seq}"


def _parse_numeric_invoice_number(invoice_number: str) -> tuple[int, int] | None:
    """Parse YY#### and return (yy, seq)."""
    s = (invoice_number or "").strip()
    if len(s) != 6 or not s.isdigit():
        return None
    return int(s[:2]), int(s[2:])

def _sequence_from_job(job: Job | None, *, year: int) -> int | None:
    """Return a job's YY#### sequence when it is an invoice-aligned job."""
    if not job:
        return None
    if getattr(job, "is_general_job", False):
        return None
    seq = int(getattr(job, "job_seq", 0) or 0)
    if seq <= 0 or seq >= 9000:
        return None
    if int(getattr(job, "job_year", year) or year) != int(year):
        return None
    return seq


def _max_existing_invoice_seq(*, business, year: int) -> int:
    """Max YY#### invoice sequence already used for this business/year."""
    yy = year % 100
    max_seq = 0
    invoice_numbers = (
        Invoice.objects.filter(
            business=business,
            issue_date__year=year,
            invoice_number__regex=r"^[0-9]{6}$",
        )
        .values_list("invoice_number", flat=True)
    )
    for invoice_number in invoice_numbers:
        parsed = _parse_numeric_invoice_number(invoice_number)
        if parsed and parsed[0] == yy:
            max_seq = max(max_seq, parsed[1])
    return max_seq


def _max_invoiceable_job_seq(*, business, year: int) -> int:
    """Max job sequence that should participate in invoice numbering."""
    try:
        return int(
            Job.invoice_sequence_queryset(business=business, year=year)
            .aggregate(m=models.Max("job_seq"))
            .get("m")
            or 0
        )
    except AttributeError:
        # Backward-compatible fallback if an older Job model is loaded.
        return int(
            Job.objects.filter(business=business, job_year=year)
            .exclude(job_number__istartswith="GENERAL")
            .exclude(label__istartswith="GENERAL")
            .exclude(job_seq__gte=9000)
            .aggregate(m=models.Max("job_seq"))
            .get("m")
            or 0
        )


def _number_is_available(*, business, invoice_number: str) -> bool:
    return not Invoice.objects.filter(business=business, invoice_number=invoice_number).exists()



def allocate_next_invoice_number(*, business, issue_date, job: Job | None = None) -> str:
    """Allocate the next numeric invoice number for the given business + issue_date.

    Format: YY#### (e.g., 260005).

    If an invoice-aligned job is supplied, the invoice uses the job sequence first
    so job NHRA-260005 creates invoice 260005. Otherwise, the next sequence is
    based on the highest of: InvoiceCounter, existing invoices, and invoiceable
    jobs. General/internal jobs do not participate.
    """
    issue_date = issue_date or timezone.localdate()
    year = int(issue_date.year)
    yy = year % 100

    with transaction.atomic():
        counter, _ = (
            InvoiceCounter.objects.select_for_update()
            .get_or_create(business=business, year=year, defaults={"last_seq": 0})
        )

        job_seq = _sequence_from_job(job, year=year)
        if job_seq:
            job_number = f"{yy:02d}{job_seq:04d}"
            if _number_is_available(business=business, invoice_number=job_number):
                counter.last_seq = max(counter.last_seq, job_seq)
                counter.full_clean()
                counter.save(update_fields=["last_seq"])
                return job_number

        floor = max(
            int(counter.last_seq or 0),
            _max_existing_invoice_seq(business=business, year=year),
            _max_invoiceable_job_seq(business=business, year=year),
        )
        counter.last_seq = floor + 1
        counter.full_clean()
        counter.save(update_fields=["last_seq"])
        return f"{yy:02d}{counter.last_seq:04d}"


def bump_counter_if_needed(*, business, issue_date, invoice_number: str) -> None:
    """Ensure InvoiceCounter.last_seq is >= the numeric portion of invoice_number.

    Useful after manual entry/import so the next allocated number doesn't collide.
    """
    issue_date = issue_date or timezone.localdate()
    year = int(issue_date.year)
    parsed = _parse_numeric_invoice_number(invoice_number)
    if not parsed:
        return

    yy, seq = parsed
    if yy != (year % 100):
        return

    with transaction.atomic():
        counter, _ = InvoiceCounter.objects.select_for_update().get_or_create(
            business=business, year=year, defaults={"last_seq": 0}
        )
        floor = max(
            seq,
            _max_existing_invoice_seq(business=business, year=year),
            _max_invoiceable_job_seq(business=business, year=year),
        )
        if counter.last_seq < floor:
            counter.last_seq = floor
            counter.full_clean()
            counter.save(update_fields=["last_seq"])


def validate_manual_invoice_number(
    *,
    business,
    issue_date,
    invoice_number: str,
    exclude_pk: int | None = None,
) -> str:
    """Validate a manually entered invoice number.

    Expected format: YY#### (6 digits), e.g. 250001.
    - YY must match issue_date.year % 100
    - invoice_number must be unique within the business

    Returns the normalized invoice_number.
    """
    num = (invoice_number or "").strip()
    if not num:
        raise ValidationError("Invoice number is required.")
    if not (len(num) == 6 and num.isdigit()):
        raise ValidationError("Invoice number must be 6 digits in the format YY#### (e.g., 250001).")

    yy = int((issue_date or timezone.localdate()).year) % 100
    if num[:2] != f"{yy:02d}":
        raise ValidationError("Invoice number year prefix does not match the issue date.")

    qs = Invoice.objects.filter(business=business, invoice_number=num)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    if qs.exists():
        raise ValidationError("Invoice number already exists for this business.")
    return num


def _increment_alpha_suffix(s: str) -> str:
    """Increment A..Z, AA..ZZ, etc."""
    s = (s or "").strip().upper()
    if not s:
        return "A"

    letters = [ord(c) - 65 for c in s]
    i = len(letters) - 1
    carry = 1
    while i >= 0 and carry:
        letters[i] += carry
        if letters[i] >= 26:
            letters[i] = 0
            carry = 1
        else:
            carry = 0
        i -= 1
    if carry:
        letters = [0] + letters
    return "".join(chr(n + 65) for n in letters)


def next_revision_suffix(*, business, base_number: str) -> str:
    """Return next alpha suffix for revisions of a numeric base invoice number.

    Example: base_number="250001" -> returns "A" for first revision, then "B", ... "AA".

    Looks at existing invoice_number values in this business that start with base_number.
    """
    base = (base_number or "").strip()
    if len(base) != 6 or not base.isdigit():
        raise ValueError("base_number must be a 6-digit YY#### string")

    existing = (
        Invoice.objects.filter(business=business, invoice_number__startswith=base)
        .exclude(invoice_number=base)
        .values_list("invoice_number", flat=True)
    )

    def _suffix_val(sfx: str) -> int:
        val = 0
        for c in sfx:
            if not ("A" <= c <= "Z"):
                return -1
            val = val * 26 + (ord(c) - 65) + 1
        return val

    max_sfx = ""
    max_val = 0
    for full in existing:
        sfx = (full or "")[6:].strip().upper()
        if not sfx:
            continue
        v = _suffix_val(sfx)
        if v > max_val:
            max_val = v
            max_sfx = sfx

    return _increment_alpha_suffix(max_sfx)


class Invoice(BusinessOwnedModelMixin):
    class Status(models.TextChoices):
        DRAFT = "draft", "Invoice created"
        SENT = "sent", "Invoice sent"
        PAID = "paid", "Invoice paid"
        VOID = "void", "Voided"

    status              = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    issue_date          = models.DateField(default=timezone.localdate)
    due_date            = models.DateField(null=True, blank=True)
    sent_date           = models.DateField(null=True, blank=True)
    paid_date           = models.DateField(null=True, blank=True)
    location            = models.CharField(max_length=255, blank=True)
    invoice_number      = models.CharField(max_length=12, blank=True)
    bill_to_name        = models.CharField(max_length=255, blank=True)
    bill_to_email       = models.EmailField(blank=True)
    bill_to_address1    = models.CharField(max_length=255, blank=True)
    bill_to_address2    = models.CharField(max_length=255, blank=True)
    bill_to_city        = models.CharField(max_length=120, blank=True)
    bill_to_state       = models.CharField(max_length=50, blank=True)
    bill_to_postal_code = models.CharField(max_length=20, blank=True)
    bill_to_country     = models.CharField(max_length=50, default="US", blank=True)
    memo                = models.TextField(blank=True)
    subtotal            = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total               = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    pdf_file            = models.FileField(upload_to="invoices/final/", null=True, blank=True)
    created_at          = models.DateTimeField(auto_now_add=True)
    updated_at          = models.DateTimeField(auto_now=True)
    contact             = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="invoices")
    job                 = models.ForeignKey(Job, on_delete=models.PROTECT, related_name="invoices", null=True, blank=True)
    team                = models.ForeignKey("ledger.Team", on_delete=models.PROTECT, related_name="invoices", null=True, blank=True)
    revises             = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="revisions", help_text="If set, this invoice is a revision of another invoice.",)
    income_transaction  = models.OneToOneField(Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoice_income_for",)

    class Meta:
        ordering = ["-issue_date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=("business", "invoice_number"), name="uniq_invoice_number_per_business"),
        ]

    def __str__(self) -> str:
        return self.invoice_number or f"Invoice #{self.pk}"

    def clean(self):
        super().clean()

        # Tenant guards
        if self.contact_id and self.business_id and getattr(self.contact, "business_id", None) != self.business_id:
            raise ValidationError({"contact": "Contact does not belong to this business."})
        if self.job_id and self.business_id and getattr(self.job, "business_id", None) != self.business_id:
            raise ValidationError({"job": "Job does not belong to this business."})
        if self.revises_id and self.business_id and getattr(self.revises, "business_id", None) != self.business_id:
            raise ValidationError({"revises": "Revised invoice does not belong to this business."})

        # Requested tenant guard
        if self.income_transaction_id and self.business_id and getattr(self.income_transaction, "business_id", None) != self.business_id:
            raise ValidationError({"income_transaction": "Income transaction does not belong to this business."})

        # Validate manual numeric invoice numbers (revisions like 250001A are allowed)
        if self.invoice_number:
            num = self.invoice_number.strip()
            if len(num) == 6 and num.isdigit():
                validate_manual_invoice_number(
                    business=self.business,
                    issue_date=self.issue_date,
                    invoice_number=num,
                    exclude_pk=self.pk,
                )

    def save(self, *args, **kwargs):
        creating = self._state.adding
        with transaction.atomic():
            if creating:
                # Force assignment at creation: never persist blank/empty invoice_number.
                if not (self.invoice_number or "").strip():
                    self.invoice_number = allocate_next_invoice_number(
                        business=self.business,
                        issue_date=self.issue_date,
                        job=self.job,
                    )

            # Ensure tenant guards and validators always run.
            self.full_clean()
            return super().save(*args, **kwargs)

    @property
    def subtotal_amount(self) -> Decimal:
        """Option A: compute live totals from items."""
        agg = self.items.aggregate(s=Coalesce(Sum("line_total"), Decimal("0.00")))
        return agg["s"]

    @property
    def total_amount(self) -> Decimal:
        # Taxes/discounts can be represented as line items.
        return self.subtotal_amount


class InvoiceItem(BusinessOwnedModelMixin):
    invoice         = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    description     = models.CharField(max_length=255)
    subcategory     = models.ForeignKey(SubCategory, on_delete=models.PROTECT, related_name="invoice_items", null=True, blank=True)
    qty             = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1.00"), validators=[MinValueValidator(Decimal("0.00"))])
    unit_price      = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(Decimal("0.00"))])
    line_total      = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    sort_order      = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def clean(self):
        super().clean()
        if self.invoice_id and self.business_id and getattr(self.invoice, "business_id", None) != self.business_id:
            raise ValidationError({"invoice": "Invoice does not belong to this business."})
        if self.subcategory_id and self.business_id and getattr(self.subcategory, "business_id", None) != self.business_id:
            raise ValidationError({"subcategory": "Sub Category does not belong to this business."})

    def save(self, *args, **kwargs):
        qty = self.qty or Decimal("0.00")
        price = self.unit_price or Decimal("0.00")
        self.line_total = (qty * price).quantize(Decimal("0.01"))
        self.full_clean()
        return super().save(*args, **kwargs)


class InvoicePayment(BusinessOwnedModelMixin):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["date", "id"]

    def clean(self):
        super().clean()
        if self.invoice_id and self.business_id and getattr(self.invoice, "business_id", None) != self.business_id:
            raise ValidationError({"invoice": "Invoice does not belong to this business."})
