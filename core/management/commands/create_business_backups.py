from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.business_backup_storage import cleanup_old_business_backups, create_business_backup
from core.models import Business


class Command(BaseCommand):
    help = "Create MoneyPro per-business Excel backups in configured storage/S3 and apply retention cleanup."

    def add_arguments(self, parser):
        parser.add_argument("--business-id", type=int, help="Backup one business by ID. Omit to backup all businesses.")
        parser.add_argument(
            "--retention-days",
            type=int,
            default=getattr(settings, "MONEYPRO_BACKUP_RETENTION_DAYS", 7),
            help="Delete successful backups older than this many days. Default: settings.MONEYPRO_BACKUP_RETENTION_DAYS or 7.",
        )
        parser.add_argument(
            "--cleanup-only",
            action="store_true",
            help="Only delete backups older than the retention period; do not create a new backup.",
        )

    def handle(self, *args, **options):
        business_id = options.get("business_id")
        retention_days = int(options.get("retention_days") or 7)
        cleanup_only = bool(options.get("cleanup_only"))

        businesses = Business.objects.all().order_by("name")
        if business_id:
            businesses = businesses.filter(pk=business_id)

        if not businesses.exists():
            raise CommandError("No matching businesses found.")

        total_created = 0
        total_deleted = 0

        for business in businesses:
            if cleanup_only:
                deleted = cleanup_old_business_backups(business=business, retention_days=retention_days)
                total_deleted += deleted
                self.stdout.write(self.style.SUCCESS(f"{business}: cleanup complete, deleted {deleted} old backups."))
                continue

            result = create_business_backup(business=business, retention_days=retention_days)
            total_created += 1
            total_deleted += result.deleted_count
            self.stdout.write(
                self.style.SUCCESS(
                    f"{business}: backup saved to {result.log.storage_key} "
                    f"({result.log.row_count} rows, {result.log.table_count} tables, {result.log.size_mb} MB). "
                    f"Deleted old backups: {result.deleted_count}."
                )
            )

        self.stdout.write(self.style.SUCCESS(f"Done. Created: {total_created}. Deleted old backups: {total_deleted}."))
