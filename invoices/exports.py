from __future__ import annotations

import csv
from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.utils import timezone

from .models import Invoice


def _active_business_or_404(request: HttpRequest):
    business = getattr(request, "business", None)
    if not business:
        raise Http404("No active business.")
    return business


@login_required
def invoice_export_csv(request: HttpRequest) -> HttpResponse:
    """Export all invoices for the active business as CSV.

    - Row-based multi-tenant safe (business-scoped).
    - Exports *all model fields* dynamically (no guessing).
    - Adds a few helpful computed columns when present.
    """
    business = _active_business_or_404(request)

    # Select-related all FK fields (except business) to avoid N+1 on string columns.
    fk_names: list[str] = []
    for f in Invoice._meta.fields:
        if f.is_relation and f.many_to_one and f.name not in ("business",):
            fk_names.append(f.name)

    qs = Invoice.objects.filter(business=business)
    if fk_names:
        qs = qs.select_related(*fk_names)
    qs = qs.order_by("-issue_date", "-id")

    # Build headers from model fields, exporting FK as both *_id and human string.
    headers: list[str] = []
    field_extractors: list[tuple[str, Any]] = []

    for f in Invoice._meta.fields:
        if f.is_relation and f.many_to_one:
            # FK: export id and string form (useful in spreadsheets)
            headers.append(f"{f.name}_id")
            field_extractors.append((f"{f.name}_id", lambda obj, n=f.attname: getattr(obj, n)))
            headers.append(f.name)
            field_extractors.append((f.name, lambda obj, n=f.name: str(getattr(obj, n) or "")))
        else:
            headers.append(f.name)
            field_extractors.append((f.name, lambda obj, n=f.name: getattr(obj, n)))

    # Helpful computed columns when available on the model
    extra_cols = [
        ("subtotal_amount", lambda o: getattr(o, "subtotal_amount", "")),
        ("total_amount", lambda o: getattr(o, "total_amount", "")),
        ("balance_due", lambda o: getattr(o, "balance_due", "")),
        ("item_count", lambda o: getattr(getattr(o, "items", None), "count", lambda: "")()),
    ]
    for name, _ in extra_cols:
        if name not in headers:
            headers.append(name)
    # but only compute if the attribute exists to avoid side effects
    def _extra_value(o, name, fn):
        try:
            val = fn(o)
        except Exception:
            return ""
        return val

    now = timezone.localtime()
    filename = f"invoices_{getattr(business, 'id', 'business')}_{now.strftime('%Y%m%d_%H%M%S')}.csv"

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(resp)
    writer.writerow(headers)

    for inv in qs.iterator(chunk_size=2000):
        row = []
        for col_name, getter in field_extractors:
            val = getter(inv)
            # Normalize values for CSV
            if val is None:
                val = ""
            row.append(val)
        # extras
        for name, fn in extra_cols:
            if name in headers and name not in [c for c,_ in field_extractors]:
                row.append(_extra_value(inv, name, fn))
        # The above may misalign because we appended extras at end; ensure we append exactly len(extra_cols) always.
        # Simpler: rebuild extras in the same order as headers beyond model fields.
        # We'll do that by trimming and then adding extras in header order:
        base_len = len(field_extractors)
        row = row[:base_len]
        for name in headers[base_len:]:
            fn_map = dict(extra_cols)
            if name in fn_map:
                row.append(_extra_value(inv, name, fn_map[name]))
            else:
                row.append("")
        writer.writerow(row)

    return resp
