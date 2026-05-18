from __future__ import annotations

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from invoices.models import Invoice
from invoices.services import ensure_number, render_invoice_pdf_bytes


class Command(BaseCommand):
    help = "Regenerate saved invoice PDF files for existing invoices."

    def add_arguments(self, parser):
        parser.add_argument(
            "--business-id",
            type=int,
            help="Only regenerate invoices for this business ID.",
        )
        parser.add_argument(
            "--invoice-id",
            type=int,
            action="append",
            dest="invoice_ids",
            help="Regenerate a specific invoice ID. Can be passed multiple times.",
        )
        parser.add_argument(
            "--year",
            type=int,
            help="Only regenerate invoices with issue_date in this year.",
        )
        parser.add_argument(
            "--status",
            action="append",
            choices=[choice[0] for choice in Invoice.Status.choices],
            help="Only regenerate invoices with this status. Can be passed multiple times.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Only process the first N matching invoices. Useful for testing.",
        )
        parser.add_argument(
            "--base-url",
            default=None,
            help=(
                "Optional base URL/path for WeasyPrint. Usually not needed now that "
                "the logo is embedded from storage."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show which invoices would be regenerated without changing files.",
        )
        parser.add_argument(
            "--keep-old-files",
            action="store_true",
            help="Do not delete the old stored PDF before saving the regenerated PDF.",
        )

    def handle(self, *args, **options):
        qs = (
            Invoice.objects.all()
            .select_related("business", "contact", "job", "team", "income_transaction")
            .prefetch_related("items", "items__subcategory")
            .order_by("business_id", "issue_date", "id")
        )

        if options.get("business_id"):
            qs = qs.filter(business_id=options["business_id"])

        if options.get("invoice_ids"):
            qs = qs.filter(id__in=options["invoice_ids"])

        if options.get("year"):
            qs = qs.filter(issue_date__year=options["year"])

        if options.get("status"):
            qs = qs.filter(status__in=options["status"])

        if options.get("limit") is not None:
            if options["limit"] < 1:
                raise CommandError("--limit must be greater than 0.")
            qs = qs[: options["limit"]]

        invoices = list(qs)
        total = len(invoices)

        if total == 0:
            self.stdout.write(self.style.WARNING("No matching invoices found."))
            return

        dry_run = bool(options.get("dry_run"))
        keep_old_files = bool(options.get("keep_old_files"))
        base_url = options.get("base_url")

        self.stdout.write(f"Matching invoices: {total}")
        if dry_run:
            for invoice in invoices:
                self.stdout.write(
                    f"DRY RUN: would regenerate invoice id={invoice.id} "
                    f"number={invoice.invoice_number or '(missing)'} "
                    f"business_id={invoice.business_id} status={invoice.status}"
                )
            return

        regenerated = 0
        failed = 0

        for invoice in invoices:
            label = f"id={invoice.id} number={invoice.invoice_number or '(missing)'} business_id={invoice.business_id}"
            try:
                with transaction.atomic():
                    ensure_number(invoice=invoice)
                    pdf_bytes = render_invoice_pdf_bytes(invoice=invoice, base_url=base_url)

                    filename = f"{invoice.invoice_number or invoice.pk}.pdf"

                    if invoice.pdf_file and not keep_old_files:
                        invoice.pdf_file.delete(save=False)

                    invoice.pdf_file.save(filename, ContentFile(pdf_bytes), save=False)
                    invoice.save(update_fields=["pdf_file", "updated_at"])

                regenerated += 1
                self.stdout.write(self.style.SUCCESS(f"Regenerated {label} -> {invoice.pdf_file.name}"))
            except Exception as exc:
                failed += 1
                self.stderr.write(self.style.ERROR(f"FAILED {label}: {exc}"))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Done. Regenerated: {regenerated}. Failed: {failed}."))

        if failed:
            raise CommandError(f"{failed} invoice PDF(s) failed to regenerate.")
