from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Business
from invoices.models import Invoice, InvoiceItem, bump_counter_if_needed
from ledger.models import Contact, Job, SubCategory, Team, Transaction
from vehicles.models import Vehicle


@dataclass
class ImportStats:
    jobs_created: int = 0
    invoices_created: int = 0
    invoices_updated: int = 0
    invoice_items_created: int = 0
    transactions_created: int = 0
    transactions_updated: int = 0
    transactions_skipped: int = 0


DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%-m/%-d/%Y",
    "%-m/%-d/%y",
)


def _clean(s: Any) -> str:
    return str(s or "").strip()


def _norm(s: Any) -> str:
    return _clean(s).casefold()


def _norm_space(s: Any) -> str:
    return " ".join(_clean(s).split()).casefold()


def _parse_date(value: Any, field_name: str):
    raw = _clean(value)
    if not raw:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise CommandError(f"Invalid date for {field_name}: {raw!r}")


def _parse_decimal(value: Any, field_name: str, *, allow_negative: bool = True) -> Decimal:
    raw = _clean(value)
    if not raw:
        return Decimal("0.00")
    normalized = raw.replace(",", "").replace("$", "")
    if normalized.startswith("(") and normalized.endswith(")"):
        normalized = f"-{normalized[1:-1]}"
    try:
        dec = Decimal(normalized)
    except (InvalidOperation, ValueError) as exc:
        raise CommandError(f"Invalid decimal for {field_name}: {raw!r}") from exc
    if not allow_negative and dec < 0:
        raise CommandError(f"Negative amount not allowed for {field_name}: {raw!r}")
    return dec.quantize(Decimal("0.01"))


class Command(BaseCommand):
    help = "Import MoneyPro invoices, invoice items, and transactions from cleaned CSV files."

    def add_arguments(self, parser):
        parser.add_argument("--business-id", type=int, required=True)
        parser.add_argument("--invoices-csv", type=str, required=True)
        parser.add_argument("--items-csv", type=str, required=True)
        parser.add_argument("--transactions-csv", type=str, required=True)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--create-missing-contacts",
            action="store_true",
            help="Create minimal contacts when they are missing instead of using the Unknown placeholder.",
        )

    def handle(self, *args, **options):
        business = self._get_business(options["business_id"])
        invoices_csv = self._require_path(options["invoices_csv"], "Invoices CSV")
        items_csv = self._require_path(options["items_csv"], "Invoice items CSV")
        transactions_csv = self._require_path(options["transactions_csv"], "Transactions CSV")
        dry_run = bool(options["dry_run"])
        create_missing_contacts = bool(options["create_missing_contacts"])

        stats = ImportStats()

        with transaction.atomic():
            invoices_rows = self._read_csv(invoices_csv, required_columns={
                "invoice_number", "status", "issue_date", "contact_name", "job_name", "subtotal", "total"
            })
            item_rows = self._read_csv(items_csv, required_columns={
                "invoice_number", "sort_order", "description", "quantity", "unit_price", "line_total", "subcategory_name"
            })
            transaction_rows = self._read_csv(transactions_csv, required_columns={
                "Date", "Amount", "Description", "SubCategory"
            })

            items_by_invoice = self._group_items(item_rows)
            refs = self._build_reference_maps(business)

            invoice_map = self._import_invoices(
                business=business,
                rows=invoices_rows,
                items_by_invoice=items_by_invoice,
                refs=refs,
                stats=stats,
                create_missing_contacts=create_missing_contacts,
            )
            self._import_transactions(
                business=business,
                rows=transaction_rows,
                refs=refs,
                invoice_map=invoice_map,
                stats=stats,
                create_missing_contacts=create_missing_contacts,
            )

            if dry_run:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("Dry run complete. Database changes rolled back."))

        self.stdout.write(self.style.SUCCESS("MoneyPro financial import complete."))
        self.stdout.write(f"Jobs created: {stats.jobs_created}")
        self.stdout.write(f"Invoices created: {stats.invoices_created}")
        self.stdout.write(f"Invoices updated: {stats.invoices_updated}")
        self.stdout.write(f"Invoice items created: {stats.invoice_items_created}")
        self.stdout.write(f"Transactions created: {stats.transactions_created}")
        self.stdout.write(f"Transactions updated: {stats.transactions_updated}")
        self.stdout.write(f"Transactions skipped (already matched): {stats.transactions_skipped}")

    def _get_business(self, business_id: int) -> Business:
        try:
            return Business.objects.get(pk=business_id)
        except Business.DoesNotExist as exc:
            raise CommandError(f"Business not found: id={business_id}") from exc

    def _require_path(self, value: str, label: str) -> Path:
        path = Path(value).expanduser().resolve()
        if not path.exists():
            raise CommandError(f"{label} not found: {path}")
        return path

    def _read_csv(self, path: Path, *, required_columns: set[str]) -> list[dict[str, str]]:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fieldnames = set(reader.fieldnames or [])
            missing = required_columns - fieldnames
            if missing:
                raise CommandError(f"{path.name} is missing columns: {sorted(missing)}")
            return list(reader)

    def _group_items(self, rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
        grouped: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            invoice_number = _clean(row.get("invoice_number"))
            if not invoice_number:
                raise CommandError("invoice_items.csv contains a row with blank invoice_number")
            grouped.setdefault(invoice_number, []).append(row)
        return grouped

    def _build_reference_maps(self, business: Business) -> dict[str, Any]:
        subcategories = {
            _norm_space(sc.name): sc
            for sc in SubCategory.objects.filter(business=business).select_related("category")
        }
        teams = {_norm_space(t.name): t for t in Team.objects.filter(business=business)}
        vehicles = {_norm_space(v.label): v for v in Vehicle.objects.filter(business=business)}
        jobs = {_norm(j.label): j for j in Job.objects.filter(business=business).select_related("client")}
        contacts = {}
        for c in Contact.objects.filter(business=business):
            for candidate in {c.display_name, c.business_name, c.legal_name, c.email}:
                key = _norm_space(candidate)
                if key:
                    contacts.setdefault(key, c)

        return {
            "subcategories": subcategories,
            "teams": teams,
            "vehicles": vehicles,
            "jobs": jobs,
            "contacts": contacts,
            "unknown_contact": Contact.get_unknown(business=business),
        }

    def _resolve_contact(
        self,
        *,
        business: Business,
        refs: dict[str, Any],
        name: str = "",
        email: str = "",
        create_missing: bool,
    ) -> Contact:
        for candidate in (email, name):
            key = _norm_space(candidate)
            if key and key in refs["contacts"]:
                return refs["contacts"][key]

        if create_missing and (_clean(name) or _clean(email)):
            contact = Contact.objects.create(
                business=business,
                display_name=_clean(name) or _clean(email),
                business_name=_clean(name),
                email=_clean(email),
                is_vendor=True,
                is_customer=True,
                is_contractor=False,
            )
            for candidate in {contact.display_name, contact.business_name, contact.legal_name, contact.email}:
                key = _norm_space(candidate)
                if key:
                    refs["contacts"][key] = contact
            return contact

        return refs["unknown_contact"]

    def _resolve_subcategory(self, refs: dict[str, Any], name: str) -> SubCategory:
        sc = refs["subcategories"].get(_norm_space(name))
        if not sc:
            raise CommandError(f"SubCategory not found for this business: {name!r}")
        return sc

    def _resolve_team(self, refs: dict[str, Any], name: str) -> Team | None:
        raw = _clean(name)
        if not raw:
            return None
        team = refs["teams"].get(_norm_space(raw))
        if not team:
            raise CommandError(f"Team not found for this business: {raw!r}")
        return team

    def _resolve_vehicle(self, refs: dict[str, Any], label: str, *, transport_type: str) -> Vehicle | None:
        raw = _clean(label)
        if not raw:
            return None
        if _norm(raw) in {"rental_car", "personal_vehicle", "business_vehicle"}:
            return None
        if transport_type and transport_type != "business_vehicle":
            return None
        vehicle = refs["vehicles"].get(_norm_space(raw))
        if not vehicle:
            raise CommandError(f"Vehicle not found for this business: {raw!r}")
        return vehicle

    def _job_label(self, job_name: str, year: int) -> str:
        return f"{_clean(job_name)}-{year}"

    def _get_or_create_job(
        self,
        *,
        business: Business,
        refs: dict[str, Any],
        label: str,
        year: int,
        client: Contact | None = None,
        location: str = "",
        stats: ImportStats,
    ) -> Job:
        key = _norm(label)
        existing = refs["jobs"].get(key)
        if existing:
            updated_fields: list[str] = []
            if not existing.client_id and client:
                existing.client = client
                updated_fields.append("client")
            if not existing.city and location:
                existing.city = _clean(location)[:120]
                updated_fields.append("city")
            if updated_fields:
                existing.full_clean()
                existing.save(update_fields=updated_fields)
            return existing

        job = Job(
            business=business,
            label=label,
            job_year=year,
            client=client,
            city=_clean(location)[:120],
        )
        job.full_clean()
        job.save()
        refs["jobs"][key] = job
        stats.jobs_created += 1
        return job

    def _import_invoices(
        self,
        *,
        business: Business,
        rows: list[dict[str, str]],
        items_by_invoice: dict[str, list[dict[str, str]]],
        refs: dict[str, Any],
        stats: ImportStats,
        create_missing_contacts: bool,
    ) -> dict[str, Invoice]:
        invoice_map: dict[str, Invoice] = {}

        for row in rows:
            invoice_number = _clean(row.get("invoice_number"))
            if not invoice_number:
                raise CommandError("invoices.final.csv contains a row with blank invoice_number")

            issue_date = _parse_date(row.get("issue_date"), "issue_date")
            if not issue_date:
                raise CommandError(f"Invoice {invoice_number} is missing issue_date")

            contact = self._resolve_contact(
                business=business,
                refs=refs,
                name=_clean(row.get("contact_name") or row.get("bill_to_name")),
                email=_clean(row.get("contact_email") or row.get("bill_to_email")),
                create_missing=create_missing_contacts,
            )

            job_name = _clean(row.get("job_name"))
            job = None
            if job_name:
                job = self._get_or_create_job(
                    business=business,
                    refs=refs,
                    label=self._job_label(job_name, issue_date.year),
                    year=issue_date.year,
                    client=contact,
                    location=_clean(row.get("location")),
                    stats=stats,
                )

            defaults = {
                "status": (_clean(row.get("status")) or Invoice.Status.DRAFT).lower(),
                "issue_date": issue_date,
                "due_date": _parse_date(row.get("due_date"), "due_date"),
                "sent_date": _parse_date(row.get("sent_date"), "sent_date"),
                "paid_date": _parse_date(row.get("paid_date"), "paid_date"),
                "location": _clean(row.get("location")),
                "bill_to_name": _clean(row.get("bill_to_name")),
                "bill_to_email": _clean(row.get("bill_to_email")),
                "bill_to_address1": _clean(row.get("bill_to_address1")),
                "bill_to_address2": _clean(row.get("bill_to_address2")),
                "bill_to_city": _clean(row.get("bill_to_city")),
                "bill_to_state": _clean(row.get("bill_to_state")),
                "bill_to_postal_code": _clean(row.get("bill_to_postal_code")),
                "bill_to_country": _clean(row.get("bill_to_country")) or "US",
                "memo": _clean(row.get("memo")),
                "subtotal": _parse_decimal(row.get("subtotal"), "subtotal", allow_negative=False),
                "total": _parse_decimal(row.get("total"), "total", allow_negative=False),
                "contact": contact,
                "job": job,
            }

            invoice, created = Invoice.objects.get_or_create(
                business=business,
                invoice_number=invoice_number,
                defaults=defaults,
            )
            if created:
                stats.invoices_created += 1
            else:
                for field, value in defaults.items():
                    setattr(invoice, field, value)
                invoice.full_clean()
                invoice.save()
                stats.invoices_updated += 1

            bump_counter_if_needed(
                business=business,
                issue_date=invoice.issue_date,
                invoice_number=invoice.invoice_number,
            )

            invoice.items.all().delete()
            for item_row in sorted(items_by_invoice.get(invoice_number, []), key=lambda r: int(_clean(r.get("sort_order")) or 0)):
                subcategory_name = _clean(item_row.get("subcategory_name"))
                subcategory = self._resolve_subcategory(refs, subcategory_name) if subcategory_name else None
                item = InvoiceItem(
                    business=business,
                    invoice=invoice,
                    description=_clean(item_row.get("description")),
                    subcategory=subcategory,
                    qty=_parse_decimal(item_row.get("quantity"), "quantity", allow_negative=False),
                    unit_price=_parse_decimal(item_row.get("unit_price"), "unit_price", allow_negative=False),
                    line_total=_parse_decimal(item_row.get("line_total"), "line_total", allow_negative=False),
                    sort_order=int(_clean(item_row.get("sort_order")) or 0),
                )
                item.full_clean()
                item.save()
                stats.invoice_items_created += 1

            invoice_map[invoice_number] = invoice

        return invoice_map

    def _import_transactions(
        self,
        *,
        business: Business,
        rows: list[dict[str, str]],
        refs: dict[str, Any],
        invoice_map: dict[str, Invoice],
        stats: ImportStats,
        create_missing_contacts: bool,
    ) -> None:
        for row in rows:
            tx_date = _parse_date(row.get("Date"), "Date")
            if not tx_date:
                raise CommandError("transactions-all.cleaned.csv contains a row with blank Date")

            amount_raw = _parse_decimal(row.get("Amount"), "Amount", allow_negative=True)
            is_refund = amount_raw < 0
            amount = abs(amount_raw)

            invoice_number = _clean(row.get("Invoice Number"))
            invoice = invoice_map.get(invoice_number) if invoice_number else None

            contact = self._resolve_contact(
                business=business,
                refs=refs,
                name=_clean(row.get("Contact")),
                email="",
                create_missing=create_missing_contacts,
            )
            if contact == refs["unknown_contact"] and invoice and invoice.contact_id:
                contact = invoice.contact

            team = self._resolve_team(refs, row.get("Team"))
            subcategory = self._resolve_subcategory(refs, _clean(row.get("SubCategory")))
            transport_type = _clean(row.get("Transport"))
            vehicle = self._resolve_vehicle(refs, row.get("Vehicle"), transport_type=transport_type)

            job = None
            if invoice and invoice.job_id:
                job = invoice.job
            else:
                job_name = _clean(row.get("Job"))
                if job_name:
                    job = self._get_or_create_job(
                        business=business,
                        refs=refs,
                        label=self._job_label(job_name, tx_date.year),
                        year=tx_date.year,
                        client=invoice.contact if invoice else (contact if contact != refs["unknown_contact"] else None),
                        stats=stats,
                    )
                else:
                    job = self._get_or_create_job(
                        business=business,
                        refs=refs,
                        label=f"General-{tx_date.year}",
                        year=tx_date.year,
                        client=invoice.contact if invoice else (contact if contact != refs["unknown_contact"] else None),
                        stats=stats,
                    )

            payload = {
                "business": business,
                "date": tx_date,
                "amount": amount,
                "description": _clean(row.get("Description")),
                "subcategory": subcategory,
                "contact": contact if contact != refs["unknown_contact"] or subcategory.requires_contact else contact,
                "team": team,
                "job": job,
                "invoice_number": invoice_number,
                "vehicle": vehicle,
                "transport_type": transport_type,
                "notes": _clean(row.get("Notes")),
                "is_refund": is_refund,
            }

            existing = self._find_existing_transaction(business=business, payload=payload)
            if existing:
                changed = False
                for field, value in payload.items():
                    if field == "business":
                        continue
                    if getattr(existing, field) != value:
                        setattr(existing, field, value)
                        changed = True
                if changed:
                    try:
                        existing.full_clean()
                        existing.save()
                    except ValidationError as exc:
                        if self._can_bypass_tx_validation(exc):
                            self._update_transaction_raw(existing, payload)
                        else:
                            raise CommandError(f"Transaction update failed for description={payload['description']!r}, date={tx_date}, invoice={invoice_number or '—'}: {exc}") from exc
                    stats.transactions_updated += 1
                else:
                    stats.transactions_skipped += 1
                continue

            transaction_obj = Transaction(**payload)
            try:
                transaction_obj.full_clean()
                transaction_obj.save()
            except ValidationError as exc:
                if self._can_bypass_tx_validation(exc):
                    self._create_transaction_raw(payload)
                else:
                    raise CommandError(f"Transaction import failed for description={payload['description']!r}, date={tx_date}, invoice={invoice_number or '—'}: {exc}") from exc
            stats.transactions_created += 1

    def _tx_model_fields(self, payload: dict[str, Any]) -> dict[str, Any]:
        subcategory = payload["subcategory"]
        account_type = (subcategory.account_type or Transaction.TransactionType.EXPENSE).lower()
        valid_types = {choice[0] for choice in Transaction.TransactionType.choices}
        if account_type not in valid_types:
            account_type = Transaction.TransactionType.EXPENSE

        return {
            "business": payload["business"],
            "date": payload["date"],
            "amount": payload["amount"],
            "description": payload["description"],
            "subcategory": subcategory,
            "category": subcategory.category,
            "trans_type": account_type,
            "is_refund": payload["is_refund"],
            "contact": payload["contact"],
            "team": payload["team"],
            "job": payload["job"],
            "invoice_number": payload["invoice_number"],
            "receipt": None,
            "asset": None,
            "transport_type": payload["transport_type"],
            "vehicle": payload["vehicle"],
            "notes": payload["notes"],
        }

    def _can_bypass_tx_validation(self, exc: ValidationError) -> bool:
        message_dict = getattr(exc, "message_dict", {}) or {}
        allowed = {"receipt"}
        return bool(message_dict) and set(message_dict.keys()).issubset(allowed)

    def _create_transaction_raw(self, payload: dict[str, Any]) -> None:
        fields = self._tx_model_fields(payload)
        now = timezone.now()
        tx = Transaction(**fields)
        tx.created_at = now
        tx.updated_at = now
        Transaction.objects.bulk_create([tx])

    def _update_transaction_raw(self, existing: Transaction, payload: dict[str, Any]) -> None:
        fields = self._tx_model_fields(payload)
        fields["updated_at"] = timezone.now()
        Transaction.objects.filter(pk=existing.pk).update(**fields)

    def _find_existing_transaction(self, *, business: Business, payload: dict[str, Any]) -> Transaction | None:
        qs = Transaction.objects.filter(
            business=business,
            date=payload["date"],
            amount=payload["amount"],
            description=payload["description"],
            subcategory=payload["subcategory"],
            invoice_number=payload["invoice_number"],
        )
        for obj in qs:
            if obj.contact_id != getattr(payload["contact"], "id", None):
                continue
            if obj.team_id != getattr(payload["team"], "id", None):
                continue
            if obj.job_id != getattr(payload["job"], "id", None):
                continue
            if obj.vehicle_id != getattr(payload["vehicle"], "id", None):
                continue
            if (obj.transport_type or "") != (payload["transport_type"] or ""):
                continue
            if (obj.notes or "") != (payload["notes"] or ""):
                continue
            if bool(obj.is_refund) != bool(payload["is_refund"]):
                continue
            return obj
        return None
