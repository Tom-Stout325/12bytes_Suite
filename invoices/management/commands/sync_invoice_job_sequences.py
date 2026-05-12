from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Business
from invoices.models import (
    InvoiceCounter,
    _max_existing_invoice_seq,
    _max_invoiceable_job_seq,
)
from ledger.models import Job


class Command(BaseCommand):
    help = (
        "Sync invoice counters with existing invoices + invoiceable jobs, and "
        "optionally normalize GENERAL jobs so they do not consume YY#### sequences."
    )

    def add_arguments(self, parser):
        parser.add_argument("--business-id", type=int, required=True)
        parser.add_argument("--year", type=int, required=True)
        parser.add_argument(
            "--fix-general",
            action="store_true",
            help="Rename General jobs to GENERAL-YYYY and set job_seq=0.",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        business_id = options["business_id"]
        year = options["year"]
        fix_general = options["fix_general"]
        dry_run = options["dry_run"]

        try:
            business = Business.objects.get(pk=business_id)
        except Business.DoesNotExist as exc:
            raise CommandError(f"Business id {business_id} does not exist.") from exc

        with transaction.atomic():
            general_jobs = Job.objects.filter(business=business, job_year=year).filter(
                label__istartswith="General"
            )

            if fix_general:
                for job in general_jobs:
                    self.stdout.write(
                        f"General job: id={job.pk} {job.job_number!r}/{job.job_seq} -> GENERAL-{year}/0"
                    )
                    if not dry_run:
                        job.job_number = f"GENERAL-{year}"
                        job.job_seq = 0
                        job.job_type = Job.JobType.INTERNAL
                        job.save(update_fields=["job_number", "job_seq", "job_type", "updated_at"])

            max_invoice_seq = _max_existing_invoice_seq(business=business, year=year)
            max_job_seq = _max_invoiceable_job_seq(business=business, year=year)
            floor = max(max_invoice_seq, max_job_seq)

            counter, _ = InvoiceCounter.objects.select_for_update().get_or_create(
                business=business,
                year=year,
                defaults={"last_seq": 0},
            )

            self.stdout.write(f"Business: {business} ({business.pk})")
            self.stdout.write(f"Year: {year}")
            self.stdout.write(f"Max existing invoice seq: {max_invoice_seq}")
            self.stdout.write(f"Max invoiceable job seq: {max_job_seq}")
            self.stdout.write(f"Current InvoiceCounter.last_seq: {counter.last_seq}")
            self.stdout.write(f"Target InvoiceCounter.last_seq: {floor}")

            if not dry_run and counter.last_seq < floor:
                counter.last_seq = floor
                counter.save(update_fields=["last_seq"])

            if dry_run:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("Dry run only; no changes saved."))
            else:
                self.stdout.write(self.style.SUCCESS("Invoice/job sequence sync complete."))
