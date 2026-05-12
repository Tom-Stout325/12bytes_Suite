from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_date

from core.models import Business
from invoices.models import Invoice, InvoiceItem
from ledger.models import Contact, Job, SubCategory


@dataclass
class RowError:
    kind: str
    identifier: str
    message: str


def _as_decimal(val: str | None, field: str, default: Decimal = Decimal("0.00")) -> Decimal:
    if val is None:
        return default
    s = str(val).strip()
    if s == "":
        return default
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError) as e:
        raise CommandError(f"Invalid decimal for {field}: {val!r}") from e


def _as_date(val: str | None, field: str):
    if val is None:
        return None
    s = str(val).strip()
    if s == "":
        return None
    d = parse_date(s)
    if not d:
        raise CommandError(f"Invalid date for {field}: {val!r} (expected YYYY-MM-DD)")
    return d


class Command(BaseCommand):
    help = "Import invoices + invoice items from cleaned CSV files."

    def add_arguments(self, parser):
        parser.add_argument("--business-id", type=int, required=True)
        parser.add_argument("--invoices-csv", type=str, required=True)
        parser.add_argument("--items-csv", type=str, required=True)

        parser.add_argument("--dry-run", action="store_true", help="Validate and simulate import without saving.")
        parser.add_argument(
            "--skip-existing",
            action="store_true",
            help="If an invoice_number already exists for this business, skip it (and its items).",
        )
        parser.add_argument(
            "--update-existing",
            action="store_true",
            help="If invoice exists, update header fields and replace items (delete + recreate).",
        )

        parser.add_argument(
            "--create-missing-contact",
            action="store_true",
            help="If contact cannot be found, create it (requires Contact supports at least name/email).",
        )
        parser.add_argument(
            "--create-missing-jobs",
            action="store_true",
            help="If job_name provided but Job not found, create it.",
        )
        parser.add_argument(
            "--missing-subcategory",
            choices=["error", "skip"],
            default="error",
            help="What to do if subcategory_name is not found for the business.",
        )

    def handle(self, *args, **opts):
        business_id: int = opts["business_id"]
        invoices_csv = Path(opts["invoices_csv"])
        items_csv = Path(opts["items_csv"])

        dry_run: bool = opts["dry_run"]
        skip_existing: bool = opts["skip_existing"]
        update_existing: bool = opts["update_existing"]

        if skip_existing and update_existing:
            raise CommandError("Choose only one: --skip-existing OR --update-existing")

        if not invoices_csv.exists():
            raise CommandError(f"Invoices CSV not found: {invoices_csv}")
        if not items_csv.exists():
            raise CommandError(f"Items CSV not found: {items_csv}")

        try:
            business = Business.objects.get(pk=business_id)
        except Business.DoesNotExist as e:
            raise CommandError(f"Business not found: id={business_id}") from e

        # -------- Load items first (grouped by invoice_number) --------
        items_by_number: dict[str, list[dict[str, Any]]] = {}
        item_errors: list[RowError] = []

        with items_csv.open("r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            required = {"invoice_number", "description", "quantity", "unit_price", "line_total", "subcategory_name", "sort_order"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise CommandError(f"Items CSV missing columns: {sorted(missing)}")

            for i, row in enumerate(reader, start=2):
                inv_no = (row.get("invoice_number") or "").strip()
                if not inv_no:
                    item_errors.append(RowError("items", f"line {i}", "Missing invoice_number"))
                    continue
                items_by_number.setdefault(inv_no, []).append(row)

        if item_errors:
            self._print_errors(item_errors)
            raise CommandError(f"Items CSV has {len(item_errors)} errors. Fix and retry.")

        # SubCategory cache (business-scoped). Name field is SubCategory.sub_cat in your project.
        # SubCategory cache (business-scoped). Detect the name field.
        if hasattr(SubCategory, "sub_cat"):
            subcat_name_field = "sub_cat"
        elif hasattr(SubCategory, "name"):
            subcat_name_field = "name"
        elif hasattr(SubCategory, "title"):
            subcat_name_field = "title"
        else:
            raise CommandError("Cannot determine SubCategory name field (expected sub_cat or name).")

        subcat_by_lower: dict[str, SubCategory] = {}
        for sc in SubCategory.objects.filter(business=business):
            raw = getattr(sc, subcat_name_field, "") or ""
            key = str(raw).strip().lower()
            if key:
                subcat_by_lower[key] = sc

        def resolve_subcategory(name: str) -> SubCategory | None:
            key = (name or "").strip().lower()
            if not key:
                return None
            sc = subcat_by_lower.get(key)
            if sc:
                return sc
            if opts["missing_subcategory"] == "skip":
                return None
            raise CommandError(f"Missing SubCategory for business {business_id}: {name!r}")

        def resolve_contact(row: dict[str, Any]) -> Contact:
            email = (row.get("contact_email") or row.get("bill_to_email") or "").strip()
            name = (row.get("contact_name") or row.get("bill_to_name") or "").strip()

            qs = Contact.objects.filter(business=business)

            if email:
                c = qs.filter(email__iexact=email).first()
                if c:
                    return c

            # Most common patterns; adjust if your Contact uses a different field
            for field in ["name", "company_name", "display_name"]:
                if hasattr(Contact, field) and name:
                    c = qs.filter(**{f"{field}__iexact": name}).first()
                    if c:
                        return c

            if not opts["create_missing_contact"]:
                raise CommandError(
                    "Contact not found for this business. "
                    "Create NHRA contact first, or run with --create-missing-contact."
                )

            c = Contact(business=business)
            if hasattr(Contact, "email") and email:
                c.email = email

            # Put name in the first supported field
            if name:
                for field in ["name", "company_name", "display_name"]:
                    if hasattr(Contact, field):
                        setattr(c, field, name)
                        break

            c.full_clean()
            c.save()
            return c

        def resolve_job(row: dict[str, Any]) -> Job | None:
            job_name = (row.get("job_name") or "").strip()
            if not job_name:
                return None

            qs = Job.objects.filter(business=business)
            for field in ["name", "title", "job", "job_name"]:
                if hasattr(Job, field):
                    j = qs.filter(**{f"{field}__iexact": job_name}).first()
                    if j:
                        return j
                    if opts["create_missing_jobs"]:
                        j = Job(business=business)
                        setattr(j, field, job_name)
                        j.full_clean()
                        j.save()
                        return j
                    return None
            return None

        header_errors: list[RowError] = []
        created = 0
        updated = 0
        skipped = 0
        items_created = 0

        @transaction.atomic
        def run_import():
            nonlocal created, updated, skipped, items_created

            with invoices_csv.open("r", newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                required = {
                    "business_id",
                    "invoice_number",
                    "status",
                    "issue_date",
                    "due_date",
                    "sent_date",
                    "paid_date",
                    "contact_name",
                    "contact_email",
                    "job_name",
                    "location",
                    "bill_to_name",
                    "bill_to_email",
                    "bill_to_address1",
                    "bill_to_address2",
                    "bill_to_city",
                    "bill_to_state",
                    "bill_to_postal_code",
                    "bill_to_country",
                    "memo",
                    "subtotal",
                    "total",
                    "revises_id",
                }
                missing = required - set(reader.fieldnames or [])
                if missing:
                    raise CommandError(f"Invoices CSV missing columns: {sorted(missing)}")

                valid_status = {c for c, _ in Invoice.Status.choices}

                for line_no, row in enumerate(reader, start=2):
                    row_business_id = (row.get("business_id") or "").strip()
                    if str(business_id) != str(row_business_id):
                        header_errors.append(
                            RowError("invoices", f"line {line_no}", f"business_id={row_business_id} does not match --business-id={business_id}")
                        )
                        continue

                    inv_no = (row.get("invoice_number") or "").strip()
                    if not inv_no:
                        header_errors.append(RowError("invoices", f"line {line_no}", "Missing invoice_number"))
                        continue

                    status = (row.get("status") or "draft").strip().lower()
                    if status not in valid_status:
                        header_errors.append(RowError("invoices", inv_no, f"Invalid status: {status!r}"))
                        continue

                    item_rows = items_by_number.get(inv_no, [])
                    if not item_rows:
                        header_errors.append(RowError("invoices", inv_no, "No items found for this invoice_number"))
                        continue

                    # contact/job
                    try:
                        contact = resolve_contact(row)
                        job = resolve_job(row)
                    except Exception as e:
                        header_errors.append(RowError("invoices", inv_no, str(e)))
                        continue

                    revises_id = (row.get("revises_id") or "").strip()
                    revises = None
                    if revises_id:
                        revises = Invoice.objects.filter(business=business, pk=revises_id).first()
                        if not revises:
                            revises = Invoice.objects.filter(business=business, invoice_number=revises_id).first()
                        if not revises:
                            header_errors.append(RowError("invoices", inv_no, f"revises_id not found: {revises_id!r}"))
                            continue

                    try:
                        issue_date = _as_date(row.get("issue_date"), "issue_date")
                        if not issue_date:
                            raise CommandError("issue_date is required")
                        due_date = _as_date(row.get("due_date"), "due_date")
                        sent_date = _as_date(row.get("sent_date"), "sent_date")
                        paid_date = _as_date(row.get("paid_date"), "paid_date")
                    except Exception as e:
                        header_errors.append(RowError("invoices", inv_no, f"Date parse failed: {e}"))
                        continue

                    existing = Invoice.objects.filter(business=business, invoice_number=inv_no).first()
                    if existing and skip_existing:
                        skipped += 1
                        continue

                    if existing and update_existing:
                        inv_obj = existing
                        mode = "update"
                    elif existing:
                        header_errors.append(RowError("invoices", inv_no, "Invoice already exists (use --skip-existing or --update-existing)"))
                        continue
                    else:
                        inv_obj = Invoice(business=business)
                        mode = "create"

                    inv_obj.invoice_number = inv_no
                    inv_obj.status = status
                    inv_obj.issue_date = issue_date
                    inv_obj.due_date = due_date
                    inv_obj.sent_date = sent_date
                    inv_obj.paid_date = paid_date
                    inv_obj.contact = contact
                    inv_obj.job = job
                    inv_obj.location = (row.get("location") or "").strip()
                    inv_obj.revises = revises

                    inv_obj.bill_to_name = (row.get("bill_to_name") or "").strip()
                    inv_obj.bill_to_email = (row.get("bill_to_email") or "").strip()
                    inv_obj.bill_to_address1 = (row.get("bill_to_address1") or "").strip()
                    inv_obj.bill_to_address2 = (row.get("bill_to_address2") or "").strip()
                    inv_obj.bill_to_city = (row.get("bill_to_city") or "").strip()
                    inv_obj.bill_to_state = (row.get("bill_to_state") or "").strip()
                    inv_obj.bill_to_postal_code = (row.get("bill_to_postal_code") or "").strip()
                    inv_obj.bill_to_country = (row.get("bill_to_country") or "US").strip() or "US"
                    inv_obj.memo = (row.get("memo") or "").strip()

                    # Save invoice before items
                    inv_obj.full_clean()
                    inv_obj.save()

                    if mode == "update":
                        inv_obj.items.all().delete()

                    # Create items, compute totals from items (source of truth)
                    running = Decimal("0.00")
                    for it in sorted(item_rows, key=lambda r: int((r.get("sort_order") or "0").strip() or "0")):
                        desc = (it.get("description") or "").strip()
                        if not desc:
                            header_errors.append(RowError("items", inv_no, "Item missing description"))
                            continue

                        qty = _as_decimal(it.get("quantity"), "qty", default=Decimal("1.00"))
                        unit_price = _as_decimal(it.get("unit_price"), "unit_price", default=Decimal("0.00"))

                        subcat_name = (it.get("subcategory_name") or "").strip()
                        try:
                            subcat = resolve_subcategory(subcat_name) if subcat_name else None
                        except Exception as e:
                            header_errors.append(RowError("items", inv_no, str(e)))
                            continue

                        sort_order = int((it.get("sort_order") or "0").strip() or "0")

                        item_obj = InvoiceItem(
                            business=business,
                            invoice=inv_obj,
                            description=desc,
                            qty=qty,
                            unit_price=unit_price,
                            subcategory=subcat,
                            sort_order=sort_order,
                        )
                        item_obj.full_clean()
                        item_obj.save()
                        items_created += 1
                        running += item_obj.line_total

                    inv_obj.subtotal = running
                    inv_obj.total = running
                    inv_obj.full_clean()
                    inv_obj.save(update_fields=["subtotal", "total", "updated_at"])

                    if mode == "create":
                        created += 1
                    else:
                        updated += 1

            if header_errors:
                self._print_errors(header_errors)
                raise CommandError(f"Import failed with {len(header_errors)} errors (rolled back).")

            if dry_run:
                raise CommandError("Dry-run complete (rolled back). No data saved.")

        try:
            run_import()
        except CommandError as e:
            msg = str(e)
            if msg.startswith("Dry-run complete"):
                self.stdout.write(self.style.SUCCESS(msg))
                self.stdout.write(self.style.SUCCESS(f"Would create invoices: {created} | update: {updated} | skip: {skipped} | items: {items_created}"))
                return
            raise

        self.stdout.write(self.style.SUCCESS("Import complete."))
        self.stdout.write(self.style.SUCCESS(f"Created invoices: {created}"))
        self.stdout.write(self.style.SUCCESS(f"Updated invoices: {updated}"))
        self.stdout.write(self.style.SUCCESS(f"Skipped invoices: {skipped}"))
        self.stdout.write(self.style.SUCCESS(f"Created items: {items_created}"))

    def _print_errors(self, errors: list[RowError]) -> None:
        self.stdout.write(self.style.ERROR("\nErrors:"))
        for e in errors[:200]:
            self.stdout.write(self.style.ERROR(f"- [{e.kind}] {e.identifier}: {e.message}"))
        if len(errors) > 200:
            self.stdout.write(self.style.ERROR(f"...and {len(errors) - 200} more"))