from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from timezonefinder import TimezoneFinder


AIRDATA_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %I:%M %p",
    "%b %d, %Y %I:%M%p",
    "%b %d, %Y %I:%M:%S%p",
    "%B %d, %Y %I:%M%p",
    "%B %d, %Y %I:%M:%S%p",
    "%B %d, %Y %I:%M:%S %p",
    "%B %d, %Y %I:%M %p",
)


@dataclass(frozen=True)
class TimestampResolution:
    raw: str
    parsed: datetime | None = None
    local_wall_time: datetime | None = None
    timezone_name: str = ""
    utc_offset: str = ""
    proposed_utc: datetime | None = None
    reason: str = ""
    dst_ambiguous: bool = False
    dst_nonexistent: bool = False


def parse_coordinates(value):
    try:
        latitude_text, longitude_text = str(value).split(",", 1)
        latitude, longitude = float(latitude_text.strip()), float(longitude_text.strip())
    except (TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def parse_airdata_datetime(value):
    text = str(value or "").strip()
    if not text:
        return None
    normalized = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", text, flags=re.IGNORECASE)
    for date_format in AIRDATA_DATETIME_FORMATS:
        try:
            return datetime.strptime(normalized, date_format)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None


@lru_cache(maxsize=1)
def get_timezone_finder():
    return TimezoneFinder(in_memory=True)


def _valid_localizations(local_wall_time, zone):
    valid = []
    for fold in (0, 1):
        candidate = local_wall_time.replace(tzinfo=zone, fold=fold)
        round_trip = candidate.astimezone(timezone.utc).astimezone(zone)
        if round_trip.replace(tzinfo=None) == local_wall_time and round_trip.fold == fold:
            valid.append(candidate)
    return valid


def _format_offset(value):
    offset = value.utcoffset()
    if offset is None:
        return ""
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def resolve_airdata_timestamp(raw_value, coordinates, timezone_finder=None):
    parsed = parse_airdata_datetime(raw_value)
    if parsed is None:
        return TimestampResolution(str(raw_value or ""), reason="invalid or missing flight timestamp")
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        proposed = parsed.astimezone(timezone.utc)
        return TimestampResolution(
            str(raw_value), parsed, parsed.replace(tzinfo=None), str(parsed.tzinfo),
            _format_offset(parsed), proposed,
        )
    if coordinates is None:
        return TimestampResolution(
            str(raw_value), parsed, parsed, reason="missing or invalid takeoff coordinates"
        )
    finder = timezone_finder or get_timezone_finder()
    timezone_name = finder.certain_timezone_at(lat=coordinates[0], lng=coordinates[1])
    if not timezone_name:
        return TimestampResolution(
            str(raw_value), parsed, parsed, reason="timezone could not be resolved confidently"
        )
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return TimestampResolution(
            str(raw_value), parsed, parsed, timezone_name=timezone_name,
            reason="resolved timezone is unavailable to zoneinfo",
        )
    valid = _valid_localizations(parsed, zone)
    distinct_instants = {candidate.astimezone(timezone.utc) for candidate in valid}
    if not valid:
        return TimestampResolution(
            str(raw_value), parsed, parsed, timezone_name=timezone_name,
            reason="local timestamp is nonexistent during DST transition",
            dst_nonexistent=True,
        )
    if len(distinct_instants) > 1:
        return TimestampResolution(
            str(raw_value), parsed, parsed, timezone_name=timezone_name,
            reason="local timestamp is ambiguous during DST transition",
            dst_ambiguous=True,
        )
    localized = valid[0]
    return TimestampResolution(
        str(raw_value), parsed, parsed, timezone_name, _format_offset(localized),
        localized.astimezone(timezone.utc),
    )
