from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from django.utils.text import slugify

from .business_backup_exports import (
    _safe_filename_part,
    business_backup_table_specs,
    queryset_for_business,
    workbook_bytes_for_business,
)
from .models import BackupLog, Business


@dataclass(frozen=True)
class BackupRunResult:
    log: BackupLog
    deleted_count: int = 0


def backup_prefix_for_business(business: Business) -> str:
    return f"backups/business-{business.pk}"


def create_business_backup(*, business: Business, created_by=None, retention_days: int | None = None) -> BackupRunResult:
    """Create an Excel business backup, upload it to default storage/S3, and log it.

    In production, default storage is S3 when USE_S3=True. In local development,
    this safely falls back to Django's configured local media storage.
    """

    started_at = timezone.now()
    business_slug = _safe_filename_part(getattr(business, "slug", "") or getattr(business, "name", "business"))
    date_part = timezone.localdate().isoformat()
    ts = timezone.localtime(started_at).strftime("%Y%m%d-%H%M%S")
    key = f"{backup_prefix_for_business(business)}/{date_part}/{business_slug}-moneypro-backup-{ts}.xlsx"

    log = BackupLog.objects.create(
        business=business,
        created_by=created_by,
        status=BackupLog.Status.RUNNING,
        backup_type=BackupLog.BackupType.XLSX,
        storage_key=key,
        retention_days=retention_days if retention_days is not None else getattr(settings, "MONEYPRO_BACKUP_RETENTION_DAYS", 7),
        started_at=started_at,
    )

    try:
        data = workbook_bytes_for_business(business=business)
        saved_key = default_storage.save(key, ContentFile(data))
        log.storage_key = saved_key
        log.size_bytes = len(data)
        log.table_count = len(business_backup_table_specs())
        log.row_count = _count_exported_rows(business)
        log.status = BackupLog.Status.SUCCESS
        log.completed_at = timezone.now()
        log.save(update_fields=[
            "storage_key",
            "size_bytes",
            "table_count",
            "row_count",
            "status",
            "completed_at",
            "updated_at",
        ])
    except Exception as exc:
        log.status = BackupLog.Status.FAILED
        log.error_message = str(exc)[:4000]
        log.completed_at = timezone.now()
        log.save(update_fields=["status", "error_message", "completed_at", "updated_at"])
        raise

    deleted_count = cleanup_old_business_backups(business=business, retention_days=log.retention_days)
    return BackupRunResult(log=log, deleted_count=deleted_count)


def _count_exported_rows(business: Business) -> int:
    total = 0
    for spec in business_backup_table_specs():
        try:
            total += queryset_for_business(spec, business).count()
        except Exception:
            continue
    return total


def cleanup_old_business_backups(*, business: Business, retention_days: int | None = None) -> int:
    """Delete successful backup files and log rows older than retention_days for one business."""

    days = retention_days if retention_days is not None else getattr(settings, "MONEYPRO_BACKUP_RETENTION_DAYS", 7)
    cutoff = timezone.now() - timedelta(days=max(int(days), 1))

    old_logs = BackupLog.objects.filter(
        business=business,
        status=BackupLog.Status.SUCCESS,
        created_at__lt=cutoff,
    )

    deleted = 0
    for log in old_logs.iterator(chunk_size=100):
        if log.storage_key:
            try:
                default_storage.delete(log.storage_key)
            except Exception:
                # Keep going; the DB log can still be marked deleted.
                pass
        log.status = BackupLog.Status.DELETED
        log.deleted_at = timezone.now()
        log.save(update_fields=["status", "deleted_at", "updated_at"])
        deleted += 1

    return deleted


def backup_download_url(log: BackupLog) -> str:
    if not log.storage_key:
        return ""
    try:
        return default_storage.url(log.storage_key)
    except Exception:
        return ""
