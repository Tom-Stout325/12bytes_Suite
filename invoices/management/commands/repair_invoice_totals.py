from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Business
from invoices.models import Invoice


class Command(BaseCommand):
    help = "Audit and optionally repair cached invoice totals from authoritative line items."

    def add_arguments(self, parser):
        parser.add_argument("--business", required=True, help="Business primary key or slug.")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist repairs. Without this flag the command performs a read-only audit.",
        )

    def handle(self, *args, **options):
        identifier = str(options["business"]).strip()
        business = Business.objects.filter(slug=identifier).first()
        if business is None and identifier.isdigit():
            business = Business.objects.filter(pk=int(identifier)).first()
        if business is None:
            raise CommandError(f"Business not found: {identifier}")

        apply_repairs = options["apply"]
        stale = 0
        repaired = 0
        missing_items = 0

        with transaction.atomic():
            invoices = Invoice.objects.filter(business=business).prefetch_related("items").order_by("pk")
            for invoice in invoices:
                items = list(invoice.items.all())
                if not items:
                    missing_items += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"MISSING_ITEMS invoice={invoice.invoice_number or invoice.pk} "
                            f"stored_subtotal={invoice.subtotal} stored_total={invoice.total}"
                        )
                    )
                    continue

                authoritative = sum((item.line_total for item in items), Decimal("0.00"))
                if invoice.subtotal == authoritative and invoice.total == authoritative:
                    continue

                stale += 1
                self.stdout.write(
                    f"STALE invoice={invoice.invoice_number or invoice.pk} "
                    f"stored={invoice.subtotal}/{invoice.total} authoritative={authoritative}"
                )
                if apply_repairs:
                    Invoice.objects.filter(pk=invoice.pk, business=business).update(
                        subtotal=authoritative,
                        total=authoritative,
                    )
                    repaired += 1

            if not apply_repairs:
                transaction.set_rollback(True)

        mode = "APPLIED" if apply_repairs else "AUDIT"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode} business={business.pk} stale={stale} repaired={repaired} "
                f"missing_items={missing_items}"
            )
        )
