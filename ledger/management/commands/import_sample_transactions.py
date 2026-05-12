from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction as db_transaction
from django.db.models import Max
from django.utils.dateparse import parse_date

from core.models import Business
from ledger.models import Contact, Job, SubCategory, Transaction, Team

try:
    from vehicles.models import Vehicle
except Exception:  # pragma: no cover
    Vehicle = None  # type: ignore


CSV_HEADERS = {
    "Business",
    "Date",
    "Amount",
    "Invoice Number",
    "Description",
    "SubCategory",
    "Contact",
    "Team",
    "Job",
    "Vehicle",
    "Transport",
    "Notes",
}


@dataclass
class RowError:
    row_num: int
    error: str


def _s(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _parse_date_flex(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None

    dt = parse_date(raw)
    if dt:
        return dt

    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(raw: str) -> tuple[Decimal, bool]:
    """
    Returns (amount_abs, is_refund).
    Negative amounts become refunds (abs stored + is_refund=True).
    """
    s = (raw or "").strip()
    if not s:
        raise InvalidOperation("blank amount")
    s = s.replace("$", "").replace(",", "").strip()
    amt = Decimal(s)
    if amt < 0:
        return (-amt, True)
    return (amt, False)


def _normalize_invoice_number(raw: str) -> str:
    """
    Normalize invoice numbers and treat null-like values as blank.
    """
    if raw is None:
        return ""

    s = str(raw).strip()

    # Treat pandas nulls and similar as blank
    if not s or s.lower() in {"<na>", "nan", "none"}:
        return ""

    # 250105.0 -> 250105
    if re.fullmatch(r"\d+\.0+", s):
        return s.split(".", 1)[0]

    return s



def _normalize_transport(raw: str) -> str:
    """
    Maps CSV values to Transaction.TRANSPORT_CHOICES keys.
    Allowed final values: "", "personal_vehicle", "rental_car", "business_vehicle"
    """
    s = _s(raw).lower().replace("-", "_").replace(" ", "_")
    if not s or s in {"—", "-", "none", "na", "n/a"}:
        return ""

    aliases = {
        "personal": "personal_vehicle",
        "personal_vehicle": "personal_vehicle",
        "personal_car": "personal_vehicle",
        "pv": "personal_vehicle",
        "rental": "rental_car",
        "rental_car": "rental_car",
        "rent_car": "rental_car",
        "company_vehicle": "business_vehicle",
        "business": "business_vehicle",
        "business_vehicle": "business_vehicle",
        "bv": "business_vehicle",
    }
    return aliases.get(s, s)


# -------------------------
# Contact matching
# -------------------------
_SUFFIX_RE = re.compile(r"[\s,]*(llc|inc|l\.l\.c\.|corp|co)\.?$", re.IGNORECASE)


def _normalize_contact_token(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    s = s.replace("&", "and")
    s = re.sub(r"\s+", " ", s)
    s = _SUFFIX_RE.sub("", s).strip()
    return s.lower()


def _find_contact(*, business: Business, raw_name: str) -> Contact | None:
    """
    Your Contact model uses:
      - display_name (required, unique per business)
      - business_name (optional)
      - legal_name (optional)

    We match case-insensitively and also try a normalized form that drops suffixes like ", LLC".
    """
    raw_name = (raw_name or "").strip()
    if not raw_name:
        return None

    qs = Contact.objects.filter(business=business)

    # direct iexact on display_name
    obj = qs.filter(display_name__iexact=raw_name).first()
    if obj:
        return obj

    # iexact on business_name/legal_name
    obj = qs.filter(business_name__iexact=raw_name).first()
    if obj:
        return obj
    obj = qs.filter(legal_name__iexact=raw_name).first()
    if obj:
        return obj

    # normalized match (strip LLC/Inc/etc.)
    token = _normalize_contact_token(raw_name)
    if not token:
        return None

    # Try pulling candidates and comparing normalized values
    # (keeps it simple + DB-agnostic)
    for c in qs.only("id", "display_name", "business_name", "legal_name"):
        if token == _normalize_contact_token(c.display_name):
            return c
        if c.business_name and token == _normalize_contact_token(c.business_name):
            return c
        if c.legal_name and token == _normalize_contact_token(c.legal_name):
            return c

    return None


def _create_contact_minimal(*, business: Business, display_name: str) -> Contact:
    """
    Minimal create compatible with your model.
    """
    return Contact.objects.create(
        business=business,
        display_name=display_name,
        # Defaults are safe; can be edited later.
        is_vendor=True,
        is_customer=False,
        is_contractor=False,
    )


# -------------------------
# Job creation
# -------------------------
def _get_or_create_job_by_label(*, business: Business, label: str) -> Job:
    """Create/find a Job by label.

    Job numbers are auto-generated by the Job model (JOB-YYNNN when no client_code).
    """
    label = (label or "").strip()
    if not label:
        raise ValidationError("blank job label")

    obj = Job.objects.filter(business=business, label__iexact=label).first()
    if obj:
        return obj

    return Job.objects.create(
        business=business,
        label=label,
    )


def _get_or_create_team(*, business: Business, name: str) -> Team | None:
    name = (name or "").strip()
    if not name:
        return None
    obj, _ = Team.objects.get_or_create(business=business, name=name)
    return obj


def _find_vehicle(*, business: Business, raw: str):
    """
    Vehicle model uses 'label' (unique per business).
    """
    if Vehicle is None:
        return None
    label = _s(raw)
    if not label:
        return None
    return Vehicle.objects.filter(business=business, label__iexact=label).first()


class Command(BaseCommand):
    help = "Import transactions from the business-scoped CSV format."

    def add_arguments(self, parser):
        parser.add_argument("--business-id", type=int, required=True, help="Business ID to import into.")
        parser.add_argument(
            "--csv",
            dest="csv_path",
            type=str,
            default="transactions-all.cleaned.csv",
            help="Path to CSV file (default: transactions-all.cleaned.csv).",
        )
        parser.add_argument("--dry-run", action="store_true", help="Validate only; do not write to the database.")
        parser.add_argument(
            "--errors-out",
            type=str,
            default="",
            help="Optional path for an errors CSV (defaults to <csv>.errors.csv).",
        )
        parser.add_argument(
            "--skip-existing",
            action="store_true",
            help="Skip rows that appear to already exist (conservative fingerprint).",
        )
        parser.add_argument(
            "--create-missing-contacts",
            action="store_true",
            help="Create Contact records if missing (minimal fields only).",
        )

    def handle(self, *args, **options):
        business_id: int = options["business_id"]
        csv_path = Path(options["csv_path"]).expanduser().resolve()
        dry_run: bool = bool(options["dry_run"])
        skip_existing: bool = bool(options["skip_existing"])
        create_missing_contacts: bool = bool(options["create_missing_contacts"])
        errors_out_opt: str = options["errors_out"] or ""

        if not csv_path.exists():
            raise CommandError(f"CSV not found: {csv_path}")

        business = Business.objects.filter(pk=business_id).first()
        if not business:
            raise CommandError(f"Business not found: id={business_id}")

        errors_out = (
            Path(errors_out_opt).expanduser().resolve()
            if errors_out_opt
            else csv_path.with_suffix(csv_path.suffix + ".errors.csv")
        )

        created = 0
        skipped = 0
        errors: list[RowError] = []

        ctx = db_transaction.atomic() if not dry_run else _noop_ctx()

        with csv_path.open("r", newline="", encoding="utf-8-sig") as f, ctx:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise CommandError("CSV has no header row.")

            missing_headers = CSV_HEADERS - set(reader.fieldnames)
            if missing_headers:
                raise CommandError(f"Missing CSV columns: {', '.join(sorted(missing_headers))}")

            for idx, row in enumerate(reader, start=2):  # start=2 (row 1 is header)
                try:
                    did_create = self._import_row(
                        business=business,
                        row=row,
                        row_num=idx,
                        dry_run=dry_run,
                        skip_existing=skip_existing,
                        create_missing_contacts=create_missing_contacts,
                    )
                    if did_create:
                        created += 1
                    else:
                        skipped += 1
                except Exception as exc:
                    errors.append(RowError(row_num=idx, error=str(exc)))

        if errors:
            self._write_errors(errors_out, errors)
            self.stdout.write(self.style.WARNING(f"{len(errors)} rows had errors. See: {errors_out}"))
        self.stdout.write(self.style.SUCCESS(f"Done. created={created}, skipped={skipped}, errors={len(errors)}"))

    def _import_row(
        self,
        *,
        business: Business,
        row: dict[str, str],
        row_num: int,
        dry_run: bool,
        skip_existing: bool,
        create_missing_contacts: bool,
    ) -> bool:
        date_raw = _s(row.get("Date"))
        amount_raw = _s(row.get("Amount"))
        desc = _s(row.get("Description"))
        subcat_name = _s(row.get("SubCategory"))

        if not date_raw or not amount_raw or not desc or not subcat_name:
            raise CommandError(f"Row {row_num}: missing required fields (Date, Amount, Description, SubCategory).")

        dt = _parse_date_flex(date_raw)
        if not dt:
            raise CommandError(f"Row {row_num}: invalid date '{date_raw}'.")

        amt, is_refund = _parse_amount(amount_raw)

        subcat = SubCategory.objects.filter(business=business, name=subcat_name).first()
        if not subcat:
            raise CommandError(f"Row {row_num}: SubCategory not found for business: '{subcat_name}'")

        invoice_number = _normalize_invoice_number(row.get("Invoice Number", ""))

        contact_raw = _s(row.get("Contact"))
        team_name = _s(row.get("Team"))
        job_label = _s(row.get("Job"))
        vehicle_raw = _s(row.get("Vehicle"))
        transport_raw = _s(row.get("Transport"))
        notes = _s(row.get("Notes"))

        transport_type = _normalize_transport(transport_raw)

        # Contact
        contact = None
        if contact_raw:
            contact = _find_contact(business=business, raw_name=contact_raw)
            if not contact and create_missing_contacts:
                contact = _create_contact_minimal(business=business, display_name=contact_raw)
            if not contact:
                raise CommandError(
                    f"Row {row_num}: Contact '{contact_raw}' not found. "
                    f"Create it first or rerun with --create-missing-contacts."
                )

        team = _get_or_create_team(business=business, name=team_name) if team_name else None

        # Job: create/find by label; job_number is generated by the model.
        job = _get_or_create_job_by_label(business=business, label=job_label) if job_label else None

        vehicle = _find_vehicle(business=business, raw=vehicle_raw) if vehicle_raw else None

        # If vehicle provided but no transport, assume business_vehicle.
        if vehicle and not transport_type:
            transport_type = "business_vehicle"

        # Conservative duplicate skipping (use abs amount + is_refund)
        if skip_existing:
            exists = Transaction.objects.filter(
                business=business,
                date=dt,
                amount=amt,
                is_refund=is_refund,
                description=desc,
                subcategory=subcat,
                invoice_number=invoice_number,
            ).exists()
            if exists:
                return False

        txn = Transaction(
            business=business,
            date=dt,
            amount=amt,
            is_refund=is_refund,
            description=desc,
            subcategory=subcat,
            invoice_number=invoice_number,
            notes=notes,
            contact=contact,
            team=team,
            job=job,
            transport_type=transport_type,
            vehicle=vehicle,
        )

        if dry_run:
            txn.full_clean()
            return True

        txn.save()
        return True

    def _write_errors(self, path: Path, errors: list[RowError]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["row_num", "error"])
            for e in errors:
                w.writerow([e.row_num, e.error])


class _noop_ctx:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False
