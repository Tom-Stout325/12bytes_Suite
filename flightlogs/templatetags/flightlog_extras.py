from __future__ import annotations

from datetime import timedelta
from django import template

register = template.Library()


@register.filter
def duration_hm(value):
    if value is None or value == "":
        return "—"
    try:
        if isinstance(value, timedelta):
            total_seconds = int(value.total_seconds())
        else:
            total_seconds = int(value)
        hours, rem = divmod(total_seconds, 3600)
        minutes, seconds = divmod(rem, 60)
        if hours:
            return f"{hours}h {minutes}m"
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"
    except Exception:
        return "—"


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except Exception:
        return None
