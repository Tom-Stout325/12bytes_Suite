from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Iterable, Optional

from django.db.models import Model, QuerySet
from django.http import HttpRequest, HttpResponse
from django.utils import timezone


ValueGetter = Callable[[Any], Any]


@dataclass(frozen=True)
class ExportColumn:
    """One CSV column."""

    header: str
    getter: ValueGetter


@dataclass(frozen=True)
class ExportSpec:
    """Spec describing a CSV export."""

    filename_prefix: str
    columns: list[ExportColumn]


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    return str(value)


def _get_attr_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        cur = getattr(cur, part, None)
    return cur


def col(header: str, attr_path: str) -> ExportColumn:
    return ExportColumn(header=header, getter=lambda obj: _get_attr_path(obj, attr_path))


def col_fn(header: str, fn: ValueGetter) -> ExportColumn:
    return ExportColumn(header=header, getter=fn)


def export_queryset_csv(
    *,
    request: HttpRequest,
    queryset: QuerySet,
    spec: ExportSpec,
    filename: Optional[str] = None,
) -> HttpResponse:
    """Stream a queryset to CSV using the given spec."""

    now = timezone.localtime(timezone.now())
    safe_ts = now.strftime("%Y%m%d-%H%M%S")
    if filename is None:
        filename = f"{spec.filename_prefix}-{safe_ts}.csv"

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(resp)
    writer.writerow([c.header for c in spec.columns])

    for obj in queryset.iterator(chunk_size=2000):
        row: list[str] = []
        for c in spec.columns:
            try:
                raw = c.getter(obj)
            except Exception as e:  # pragma: no cover
                raw = f"[ERROR: {e}]"
            row.append(_format_value(raw))
        writer.writerow(row)

    return resp


def model_fields_spec(
    *,
    model: type[Model],
    filename_prefix: str,
    exclude: Optional[set[str]] = None,
    include_related: bool = True,
    include_related_str: bool = True,
) -> ExportSpec:
    """Build an ExportSpec from a model's concrete DB fields.

    - Excludes heavy FileField/ImageField automatically.
    - For FK fields, exports <field>_id and optionally <field>_str.
    """

    exclude = exclude or set()

    columns: list[ExportColumn] = []

    for f in model._meta.concrete_fields:
        name = f.name
        if name in exclude:
            continue

        internal_type = getattr(f, "get_internal_type", lambda: "")()
        if internal_type in {"FileField", "ImageField"}:
            # don't stream file paths/keys by default
            continue

        # ForeignKey
        if getattr(f, "is_relation", False) and getattr(f, "many_to_one", False):
            if include_related:
                columns.append(col(f"{name}_id", f"{name}_id"))
                if include_related_str:
                    columns.append(
                        col_fn(
                            f"{name}_str",
                            lambda obj, n=name: str(getattr(obj, n)) if getattr(obj, n, None) else "",
                        )
                    )
            else:
                columns.append(col(f"{name}_id", f"{name}_id"))
            continue

        columns.append(col(name, name))

    return ExportSpec(filename_prefix=filename_prefix, columns=columns)
