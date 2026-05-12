from __future__ import annotations

from datetime import timedelta

from django import template

register = template.Library()


@register.filter
def seconds_to_hms(value):
    if value in (None, ""):
        return "—"
    try:
        total = int(value.total_seconds()) if isinstance(value, timedelta) else int(value)
    except Exception:
        return "—"
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
