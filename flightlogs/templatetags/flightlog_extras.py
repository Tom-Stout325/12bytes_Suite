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


@register.filter
def miles_from_feet(value):
    try:
        return f"{float(value) / 5280:,.1f}"
    except (TypeError, ValueError):
        return "—"


@register.filter
def number(value, decimals=0):
    try:
        return f"{float(value):,.{int(decimals)}f}"
    except (TypeError, ValueError):
        return "—"


@register.filter
def weather_icon(value):
    summary = str(value or "").lower()
    if any(word in summary for word in ("thunder", "storm", "lightning")):
        return "fa-cloud-bolt"
    if any(word in summary for word in ("snow", "sleet", "ice", "freezing")):
        return "fa-snowflake"
    if any(word in summary for word in ("rain", "drizzle", "shower")):
        return "fa-cloud-rain"
    if any(word in summary for word in ("fog", "mist", "haze", "smoke")):
        return "fa-smog"
    if any(word in summary for word in ("partly", "mostly sunny", "few clouds")):
        return "fa-cloud-sun"
    if any(word in summary for word in ("cloud", "overcast")):
        return "fa-cloud"
    if any(word in summary for word in ("clear", "sunny", "fair")):
        return "fa-sun"
    return "fa-cloud-sun"


@register.filter
def compass_direction(value):
    try:
        degrees = float(value) % 360
    except (TypeError, ValueError):
        return "—"
    points = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    return points[int((degrees + 22.5) // 45) % 8]
