from __future__ import annotations

import csv
import re
import tempfile
from calendar import month_name
from datetime import datetime, timedelta

from django.utils import timezone

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.db.models.functions import ExtractMonth, ExtractYear
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.clickjacking import xframe_options_exempt

try:
    from weasyprint import HTML
    WEASYPRINT_AVAILABLE = True
except Exception:
    WEASYPRINT_AVAILABLE = False

from .forms import FlightLogCSVUploadForm, FlightLogDJIUploadForm, FlightLogForm
from .models import FlightLog
from .services.dji import import_dji_upload

STATE_RE = re.compile(r",\s*([A-Z]{2})(?:[, ]|$)")


def _business_logs(request):
    return FlightLog.objects.filter(business=request.business)


def safe_int(value):
    try:
        if value is None:
            return None
        s = re.sub(r"[^0-9\-]+", "", str(value))
        return int(s) if s not in ("", "-") else None
    except Exception:
        return None


def safe_float(value):
    try:
        if value is None:
            return None
        s = re.sub(r"[^0-9\.\-]+", "", str(value))
        return float(s) if s not in ("", "-", ".") else None
    except Exception:
        return None


def safe_pct(value):
    return safe_int(str(value).replace("%", "")) if value is not None else None


def extract_state(address):
    match = STATE_RE.search(address or "")
    return match.group(1) if match else None


def _extract_city(addr):
    if not addr:
        return None
    city = addr.split(",", 1)[0].strip()
    return city or None


def _normalise_key(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _normalised_row(raw_row):
    """Return a dict that supports both exact CSV headers and normalized headers."""
    row = {}
    for key, value in (raw_row or {}).items():
        clean_key = (key or "").strip().replace("\ufeff", "")
        clean_value = value.strip() if isinstance(value, str) else (value if value is not None else "")
        row[clean_key] = clean_value
        row[_normalise_key(clean_key)] = clean_value
    return row


def row_value(row, *keys):
    for key in keys:
        for candidate in (key, _normalise_key(key)):
            value = row.get(candidate)
            if value not in (None, ""):
                return value
    return ""


def parse_date_value(value):
    value = str(value or "").strip()
    if not value:
        return None
    value = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", value)
    for fmt in (
        "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
        "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p",
        "%b %d, %Y %I:%M%p", "%b %d, %Y %I:%M:%S%p",
        "%B %d, %Y %I:%M%p", "%B %d, %Y %I:%M:%S%p",
        "%B %d, %Y %I:%M:%S %p", "%B %d, %Y %I:%M %p",
    ):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "")).date()
    except Exception:
        return None


def parse_datetime_value(value):
    value = str(value or "").strip()
    if not value:
        return None
    value = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", value, flags=re.IGNORECASE)
    parsed = None
    for fmt in (
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
        "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p",
        "%b %d, %Y %I:%M%p", "%b %d, %Y %I:%M:%S%p",
        "%B %d, %Y %I:%M%p", "%B %d, %Y %I:%M:%S%p",
        "%B %d, %Y %I:%M:%S %p", "%B %d, %Y %I:%M %p",
    ):
        try:
            parsed = datetime.strptime(value, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def parse_time_value(value):
    value = str(value or "").strip()
    if not value:
        return None
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "")).time()
    except Exception:
        return None


def parse_duration_value(value):
    value = str(value or "").strip()
    if not value:
        return None
    seconds = safe_float(value)
    if seconds is not None and ":" not in value:
        return timedelta(seconds=seconds)
    parts = value.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return timedelta(hours=int(hours), minutes=int(minutes), seconds=float(seconds))
        if len(parts) == 2:
            minutes, seconds = parts
            return timedelta(minutes=int(minutes), seconds=float(seconds))
    except Exception:
        return None
    return None


def _flightlog_payload_from_csv_row(row):
    """Build a FlightLog payload from either Suite exports or older AirData/FlightPlan CSVs."""
    takeoff_datetime = parse_datetime_value(
        row_value(row, "takeoff_datetime", "Flight Date/Time", "Flight/Service Date")
    )
    flight_date = takeoff_datetime.date() if takeoff_datetime else parse_date_value(
        row_value(row, "flight_date", "Flight Date/Time", "Flight/Service Date")
    )
    landing_time = parse_time_value(row_value(row, "landing_time", "Landing Time"))
    if landing_time is None:
        landing_time = parse_time_value(row_value(row, "Flight Date/Time", "Flight/Service Date"))
    air_time = parse_duration_value(row_value(row, "air_time", "Air Time", "Air Seconds"))

    return {
        "flight_date": flight_date,
        "takeoff_datetime": takeoff_datetime,
        "flight_title": row_value(row, "flight_title", "Flight Title"),
        "flight_description": row_value(row, "flight_description", "Flight Description"),
        "pilot_in_command": row_value(row, "pilot_in_command", "Pilot-in-Command", "Pilot in Command"),
        "license_number": row_value(row, "license_number", "License Number"),
        "flight_application": row_value(row, "flight_application", "Flight App"),
        "remote_id": row_value(row, "remote_id", "Remote ID"),
        "takeoff_latlong": row_value(row, "takeoff_latlong", "Takeoff Lat/Long", "Takeoff Lat Long"),
        "takeoff_address": row_value(row, "takeoff_address", "Takeoff Address"),
        "landing_time": landing_time,
        "air_time": air_time,
        "above_sea_level_ft": safe_float(row_value(row, "above_sea_level_ft", "Above Sea Level (Feet)")),
        "drone_name": row_value(row, "drone_name", "Drone Name"),
        "drone_type": row_value(row, "drone_type", "Drone Type"),
        "drone_serial": row_value(row, "drone_serial", "Drone Serial Number"),
        "drone_reg_number": row_value(row, "drone_reg_number", "Drone Registration Number"),
        "battery_name": row_value(row, "battery_name", "Battery Name"),
        "battery_serial_printed": row_value(row, "battery_serial_printed", "Bat Printed Serial"),
        "battery_serial_internal": row_value(row, "battery_serial_internal", "Bat Internal Serial"),
        "takeoff_battery_pct": safe_pct(row_value(row, "takeoff_battery_pct", "Takeoff Bat %")),
        "takeoff_mah": safe_int(row_value(row, "takeoff_mah", "Takeoff mAh")),
        "takeoff_volts": safe_float(row_value(row, "takeoff_volts", "Takeoff Volts")),
        "landing_battery_pct": safe_pct(row_value(row, "landing_battery_pct", "Landing Bat %")),
        "landing_mah": safe_int(row_value(row, "landing_mah", "Landing mAh")),
        "landing_volts": safe_float(row_value(row, "landing_volts", "Landing Volts")),
        "max_altitude_ft": safe_float(row_value(row, "max_altitude_ft", "Max Altitude (Feet)")),
        "max_distance_ft": safe_float(row_value(row, "max_distance_ft", "Max Distance (Feet)")),
        "max_battery_temp_f": safe_float(row_value(row, "max_battery_temp_f", "Max Bat Temp (f)")),
        "max_speed_mph": safe_float(row_value(row, "max_speed_mph", "Max Speed (mph)")),
        "total_mileage_ft": safe_float(row_value(row, "total_mileage_ft", "Total Mileage (Feet)")),
        "signal_score": safe_float(row_value(row, "signal_score", "Signal Score")),
        "max_compass_rate": safe_float(row_value(row, "max_compass_rate", "Max Compass Rate")),
        "avg_wind": safe_float(row_value(row, "avg_wind", "Avg Wind")),
        "max_gust": safe_float(row_value(row, "max_gust", "Max Gust")),
        "signal_losses": safe_int(row_value(row, "signal_losses", "Signal Losses (>1 sec)")),
        "ground_weather_summary": row_value(row, "ground_weather_summary", "Ground Weather Summary"),
        "ground_temp_f": safe_float(row_value(row, "ground_temp_f", "Ground Temperature (f)")),
        "visibility_miles": safe_float(row_value(row, "visibility_miles", "Ground Visibility (Miles)")),
        "wind_speed": safe_float(row_value(row, "wind_speed", "Ground Wind Speed")),
        "wind_direction": row_value(row, "wind_direction", "Ground Wind Direction"),
        "cloud_cover": row_value(row, "cloud_cover", "Cloud Cover"),
        "humidity_pct": safe_pct(row_value(row, "humidity_pct", "Humidity")),
        "dew_point_f": safe_float(row_value(row, "dew_point_f", "Dew Point (f)")),
        "pressure_inhg": safe_float(row_value(row, "pressure_inhg", "Pressure")),
        "rain_rate": row_value(row, "rain_rate", "Rain Rate"),
        "rain_chance": row_value(row, "rain_chance", "Rain Chance"),
        "sunrise": row_value(row, "sunrise", "Sunrise"),
        "sunset": row_value(row, "sunset", "Sunset"),
        "moon_phase": row_value(row, "moon_phase", "Moon Phase"),
        "moon_visibility": row_value(row, "moon_visibility", "Moon Visibility"),
        "photos": safe_int(row_value(row, "photos", "Photos")),
        "videos": safe_int(row_value(row, "videos", "Videos")),
        "notes": row_value(row, "notes", "Add Additional Notes"),
        "tags": row_value(row, "tags", "Tags"),
    }


def _values_match(existing_value, incoming_value):
    """Compare imported values to model values without treating distinct rows as duplicates."""
    if existing_value in (None, "") and incoming_value in (None, ""):
        return True
    return str(existing_value or "") == str(incoming_value or "")


FLIGHTLOG_IMPORT_FIELDS = (
    "flight_date",
    "takeoff_datetime",
    "flight_title",
    "flight_description",
    "pilot_in_command",
    "license_number",
    "flight_application",
    "remote_id",
    "takeoff_latlong",
    "takeoff_address",
    "landing_time",
    "air_time",
    "above_sea_level_ft",
    "drone_name",
    "drone_type",
    "drone_serial",
    "drone_reg_number",
    "battery_name",
    "battery_serial_printed",
    "battery_serial_internal",
    "takeoff_battery_pct",
    "takeoff_mah",
    "takeoff_volts",
    "landing_battery_pct",
    "landing_mah",
    "landing_volts",
    "max_altitude_ft",
    "max_distance_ft",
    "max_battery_temp_f",
    "max_speed_mph",
    "total_mileage_ft",
    "signal_score",
    "max_compass_rate",
    "avg_wind",
    "max_gust",
    "signal_losses",
    "ground_weather_summary",
    "ground_temp_f",
    "visibility_miles",
    "wind_speed",
    "wind_direction",
    "cloud_cover",
    "humidity_pct",
    "dew_point_f",
    "pressure_inhg",
    "rain_rate",
    "rain_chance",
    "sunrise",
    "sunset",
    "moon_phase",
    "moon_visibility",
    "photos",
    "videos",
    "notes",
    "tags",
)


def _normalise_import_value(value):
    """Convert DB/model values and parsed CSV values to a stable duplicate-check value."""
    if value in (None, ""):
        return ""
    return str(value)


def _flightlog_signature_from_payload(payload):
    return tuple(_normalise_import_value(payload.get(field)) for field in FLIGHTLOG_IMPORT_FIELDS)


def _flightlog_signature_from_obj(obj):
    return tuple(_normalise_import_value(getattr(obj, field)) for field in FLIGHTLOG_IMPORT_FIELDS)




def _flightlog_legacy_signature_from_payload(payload):
    """Signature used to backfill takeoff datetimes on pre-migration records."""
    return tuple(
        _normalise_import_value(payload.get(field))
        for field in FLIGHTLOG_IMPORT_FIELDS
        if field != "takeoff_datetime"
    )


def _flightlog_legacy_signature_from_obj(obj):
    return tuple(
        _normalise_import_value(getattr(obj, field))
        for field in FLIGHTLOG_IMPORT_FIELDS
        if field != "takeoff_datetime"
    )


def _flightlog_duplicate_exists(business, payload):
    """Return True only when an existing row is an exact import match.

    AirData can produce multiple valid flights with the same date, landing time,
    drone name, and takeoff location. The previous Suite importer used only those
    fields as the duplicate key, which caused valid rows to be skipped.

    We still use those fields to narrow the search, but then compare the full
    payload so only truly identical rows are skipped on re-import.
    """
    lookup = {
        "business": business,
        "flight_date": payload.get("flight_date"),
        "landing_time": payload.get("landing_time"),
        "takeoff_latlong": payload.get("takeoff_latlong", ""),
        "drone_name": payload.get("drone_name", ""),
    }
    candidates = FlightLog.objects.filter(**lookup)
    for candidate in candidates:
        if all(_values_match(getattr(candidate, field), value) for field, value in payload.items()):
            return True
    return False


def _sum_air_time_seconds(qs):
    total_seconds = 0
    for value in qs.exclude(air_time__isnull=True).values_list("air_time", flat=True):
        total_seconds += int(value.total_seconds())
    return total_seconds


@login_required
def drone_portal(request):
    qs = _business_logs(request)
    current_year = timezone.localdate().year
    ytd_qs = qs.filter(flight_date__year=current_year)

    context = {
        "current_page": "flightlogs",
        "current_year": current_year,
        "total_flights": qs.count(),
        "total_flight_time_seconds": _sum_air_time_seconds(qs),
        "ytd_flights": ytd_qs.count(),
        "ytd_flight_time_seconds": _sum_air_time_seconds(ytd_qs),
        "active_drones": qs.exclude(drone_name="").values("drone_name").distinct().count(),
        "highest_altitude_flight": qs.exclude(max_altitude_ft__isnull=True).order_by("-max_altitude_ft", "-flight_date", "-id").first(),
        "fastest_speed_flight": qs.exclude(max_speed_mph__isnull=True).order_by("-max_speed_mph", "-flight_date", "-id").first(),
        "farthest_flight": qs.exclude(max_distance_ft__isnull=True).order_by("-max_distance_ft", "-flight_date", "-id").first(),
    }
    return render(request, "flightlogs/drone_portal.html", context)


@login_required
def flightlog_list(request):
    sel_state = request.GET.get("state", "").strip()
    sel_city = request.GET.get("city", "").strip()
    sel_year = request.GET.get("year", "").strip()
    sel_month = request.GET.get("month", "").strip()
    sel_location = request.GET.get("location", "").strip()
    sel_pilot = request.GET.get("pilot", "").strip()
    sel_drone = request.GET.get("drone", "").strip()

    base_qs = _business_logs(request)
    logs_qs = base_qs

    years = sorted(y for y in base_qs.annotate(y=ExtractYear("flight_date")).values_list("y", flat=True).distinct() if y)
    months_present = sorted(m for m in base_qs.annotate(m=ExtractMonth("flight_date")).values_list("m", flat=True).distinct() if m)
    month_labels = {i: month_name[i] for i in range(1, 13)}
    pilots = list(base_qs.exclude(pilot_in_command="").values_list("pilot_in_command", flat=True).distinct().order_by("pilot_in_command"))
    drones = list(base_qs.exclude(drone_name="").values_list("drone_name", flat=True).distinct().order_by("drone_name"))

    addresses = list(base_qs.exclude(takeoff_address__exact="").values_list("takeoff_address", flat=True))
    states = sorted({extract_state(addr) for addr in addresses if extract_state(addr)})
    cities = sorted({
        city
        for addr in addresses
        if not sel_state or extract_state(addr) == sel_state
        for city in [_extract_city(addr)]
        if city
    })

    if sel_year.isdigit():
        logs_qs = logs_qs.filter(flight_date__year=int(sel_year))
    if sel_month.isdigit():
        logs_qs = logs_qs.filter(flight_date__month=int(sel_month))
    if sel_state:
        logs_qs = logs_qs.filter(takeoff_address__regex=rf",\s*{re.escape(sel_state)}(?:[, ]|$)")
    if sel_city:
        logs_qs = logs_qs.filter(takeoff_address__istartswith=f"{sel_city},")
    if sel_pilot:
        logs_qs = logs_qs.filter(pilot_in_command=sel_pilot)
    if sel_drone:
        logs_qs = logs_qs.filter(drone_name=sel_drone)
    if sel_location:
        logs_qs = logs_qs.filter(
            Q(takeoff_address__icontains=sel_location) | Q(takeoff_latlong__icontains=sel_location)
        )

    total_flights = logs_qs.count()
    totals = logs_qs.aggregate(total_photos=Sum("photos"), total_videos=Sum("videos"), total_mileage_ft=Sum("total_mileage_ft"))
    longest_flight = logs_qs.exclude(air_time__isnull=True).order_by("-air_time", "-flight_date", "-id").first()
    farthest_flight = logs_qs.exclude(max_distance_ft__isnull=True).order_by("-max_distance_ft", "-flight_date", "-id").first()
    highest_flight = logs_qs.exclude(max_altitude_ft__isnull=True).order_by("-max_altitude_ft", "-flight_date", "-id").first()
    longest_mileage_flight = logs_qs.exclude(total_mileage_ft__isnull=True).order_by("-total_mileage_ft", "-flight_date", "-id").first()
    hottest_battery_flight = logs_qs.exclude(max_battery_temp_f__isnull=True).order_by("-max_battery_temp_f", "-flight_date", "-id").first()
    fastest_flight = logs_qs.exclude(max_speed_mph__isnull=True).order_by("-max_speed_mph", "-flight_date", "-id").first()

    paginator = Paginator(logs_qs.order_by("-flight_date", "-takeoff_datetime", "-id"), 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)

    return render(request, "flightlogs/flightlog_list.html", {
        "logs": page_obj,
        "current_page": "flightlogs",
        "sel_state": sel_state,
        "sel_city": sel_city,
        "sel_year": sel_year,
        "sel_month": sel_month,
        "sel_pilot": sel_pilot,
        "sel_drone": sel_drone,
        "sel_location": sel_location,
        "states": states,
        "cities": cities,
        "years": years,
        "months_present": months_present,
        "month_labels": month_labels,
        "pilots": pilots,
        "drones": drones,
        "total_flights": total_flights,
        "total_air_time_seconds": _sum_air_time_seconds(logs_qs),
        "total_photos": totals["total_photos"] or 0,
        "total_videos": totals["total_videos"] or 0,
        "total_mileage_ft": totals["total_mileage_ft"] or 0,
        "longest_flight": longest_flight,
        "farthest_flight": farthest_flight,
        "highest_flight": highest_flight,
        "longest_mileage_flight": longest_mileage_flight,
        "hottest_battery_flight": hottest_battery_flight,
        "fastest_flight": fastest_flight,
        "qs_without_page": query_params.urlencode(),
    })


@login_required
def export_flightlogs_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="flight_logs.csv"'
    writer = csv.writer(response)
    fields = [f.name for f in FlightLog._meta.fields if f.name != "business"]
    writer.writerow(fields)
    for log in _business_logs(request).order_by("-flight_date"):
        writer.writerow([getattr(log, name) for name in fields])
    return response


@login_required
def flightlog_detail(request, pk):
    log = get_object_or_404(_business_logs(request), pk=pk)
    return render(request, "flightlogs/flightlog_detail.html", {"log": log, "current_page": "flightlogs"})


@login_required
def flightlog_edit(request, pk):
    log = get_object_or_404(_business_logs(request), pk=pk)
    if request.method == "POST":
        form = FlightLogForm(request.POST, instance=log)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.business = request.business
            obj.save()
            messages.success(request, "Flight log updated.")
            return redirect("flightlogs:flightlog_detail", pk=obj.pk)
        messages.error(request, "There was a problem updating the flight log.")
    else:
        form = FlightLogForm(instance=log)
    return render(request, "flightlogs/flightlog_form.html", {"form": form, "log": log, "current_page": "flightlogs"})


@login_required
def flightlog_delete(request, pk):
    log = get_object_or_404(_business_logs(request), pk=pk)
    if request.method == "POST":
        title = log.flight_title or f"Log {pk}"
        log.delete()
        messages.success(request, f"{title} deleted.")
        return redirect("flightlogs:flightlog_list")
    return render(request, "flightlogs/flightlog_confirm_delete.html", {"log": log, "current_page": "flightlogs"})


@login_required
def flightlog_pdf(request, pk):
    if not WEASYPRINT_AVAILABLE:
        messages.error(request, "PDF generation is not available on this server.")
        return redirect("flightlogs:flightlog_detail", pk=pk)
    log = get_object_or_404(_business_logs(request), pk=pk)
    html_string = render_to_string("flightlogs/flightlog_detail_pdf.html", {"log": log, "current_page": "flightlogs"})
    with tempfile.NamedTemporaryFile(delete=True, suffix=".pdf") as tmp_file:
        HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf(tmp_file.name)
        tmp_file.seek(0)
        response = HttpResponse(tmp_file.read(), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="FlightLog_{log.pk}.pdf"'
        return response


@login_required
def upload_flightlog_csv(request):
    if request.method == "POST":
        form = FlightLogCSVUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Invalid form submission.")
            return render(request, "flightlogs/flightlog_form.html", {"form": form, "current_page": "flightlogs"})

        uploaded = form.cleaned_data["csv_file"]
        try:
            decoded_lines = uploaded.read().decode("utf-8-sig").splitlines()
        except Exception:
            messages.error(request, "Could not read the CSV file. Please upload a valid UTF-8 CSV.")
            return redirect("flightlogs:flightlog_upload")

        reader = csv.DictReader(decoded_lines)
        if not reader.fieldnames:
            messages.error(request, "CSV has no headers.")
            return redirect("flightlogs:flightlog_upload")
        reader.fieldnames = [h.strip().replace("\ufeff", "") for h in reader.fieldnames]

        # Build the duplicate set once instead of querying the database once per CSV row.
        # This keeps large AirData uploads under Heroku's 30-second web request limit.
        existing_logs = list(
            _business_logs(request).only("pk", *FLIGHTLOG_IMPORT_FIELDS).iterator(chunk_size=1000)
        )
        existing_signatures = {_flightlog_signature_from_obj(log) for log in existing_logs}
        legacy_datetime_matches = {
            _flightlog_legacy_signature_from_obj(log): log
            for log in existing_logs
            if log.takeoff_datetime is None
        }

        created = updated = skipped = errored = duplicate_skipped = 0
        pending_logs = []
        pending_updates = []
        batch_size = 500

        for raw_row in reader:
            try:
                row = _normalised_row(raw_row)
                payload = _flightlog_payload_from_csv_row(row)

                if not payload.get("flight_date"):
                    skipped += 1
                    if skipped <= 5:
                        messages.warning(request, "Skipped a row because no valid flight_date/Flight Date was found.")
                    continue

                signature = _flightlog_signature_from_payload(payload)
                if signature in existing_signatures:
                    duplicate_skipped += 1
                    continue

                # Records imported before takeoff_datetime existed should be updated,
                # not duplicated, when the same source CSV is uploaded again.
                legacy_signature = _flightlog_legacy_signature_from_payload(payload)
                legacy_match = legacy_datetime_matches.pop(legacy_signature, None)
                if legacy_match is not None and payload.get("takeoff_datetime") is not None:
                    legacy_match.takeoff_datetime = payload["takeoff_datetime"]
                    pending_updates.append(legacy_match)
                    existing_signatures.add(signature)
                    updated += 1
                    continue

                existing_signatures.add(signature)
                pending_logs.append(FlightLog(business=request.business, **payload))

                if len(pending_logs) >= batch_size:
                    FlightLog.objects.bulk_create(pending_logs, batch_size=batch_size)
                    created += len(pending_logs)
                    pending_logs = []

            except Exception as e:
                errored += 1
                if errored <= 5:
                    messages.error(request, f"Row save error: {e}")

        if pending_logs:
            FlightLog.objects.bulk_create(pending_logs, batch_size=batch_size)
            created += len(pending_logs)
        if pending_updates:
            FlightLog.objects.bulk_update(pending_updates, ["takeoff_datetime"], batch_size=batch_size)

        total_skipped = skipped + duplicate_skipped
        messages.success(
            request,
            f"CSV processed. Created: {created}, Updated: {updated}, Skipped: {total_skipped}, Errors: {errored}"
            + (f" ({duplicate_skipped} duplicate rows skipped)" if duplicate_skipped else ""),
        )
        return redirect("flightlogs:flightlog_list")

    form = FlightLogCSVUploadForm()
    return render(request, "flightlogs/flightlog_form.html", {"form": form, "current_page": "flightlogs"})


@login_required
def upload_flightlog_dji(request):
    if request.business is None:
        return redirect("accounts:onboarding")
    if request.method == "POST":
        form = FlightLogDJIUploadForm(request.POST, request.FILES)
        if form.is_valid():
            result = import_dji_upload(
                business=request.business,
                user=request.user,
                uploaded=form.cleaned_data["dji_file"],
            )
            source = result.source
            if result.duplicate:
                if source.status == source.Status.COMPLETE and source.flight_log_id:
                    messages.info(request, "This exact DJI source was already imported.")
                    return redirect("flightlogs:flightlog_detail", pk=source.flight_log_id)
                messages.info(request, "This exact DJI source was already uploaded.")
            elif source.status == source.Status.COMPLETE:
                messages.success(request, "DJI flight record imported successfully.")
                return redirect("flightlogs:flightlog_detail", pk=source.flight_log_id)
            else:
                messages.error(request, source.safe_error_detail)
            return redirect("flightlogs:flightlog_dji_upload")
        messages.error(request, "Please correct the DJI upload error below.")
    else:
        form = FlightLogDJIUploadForm()
    return render(
        request,
        "flightlogs/flightlog_dji_upload.html",
        {"form": form, "current_page": "flightlogs"},
    )


@login_required
def flight_map_view(request):
    locations = list(
        _business_logs(request)
        .values("takeoff_latlong", "takeoff_address")
        .annotate(count=Count("id"))
        .exclude(takeoff_latlong__exact="")
        .order_by("takeoff_address")
    )
    states = {extract_state(loc.get("takeoff_address", "")) for loc in locations if extract_state(loc.get("takeoff_address", ""))}
    cities = {loc.get("takeoff_address", "").strip() for loc in locations if loc.get("takeoff_address")}
    return render(request, "flightlogs/map.html", {
        "locations": locations,
        "num_states": len(states),
        "num_cities": len(cities),
        "logs": _business_logs(request).order_by("-flight_date")[:100],
        "current_page": "flightlogs",
    })


@xframe_options_exempt
@login_required
def flight_map_embed(request):
    locations = list(
        _business_logs(request)
        .values("takeoff_latlong", "takeoff_address")
        .annotate(count=Count("id"))
        .exclude(takeoff_latlong__exact="")
    )
    states = {extract_state(loc.get("takeoff_address", "")) for loc in locations if extract_state(loc.get("takeoff_address", ""))}
    cities = {loc.get("takeoff_address", "").strip() for loc in locations if loc.get("takeoff_address")}
    return render(request, "flightlogs/map_embed.html", {"locations": locations, "num_states": len(states), "num_cities": len(cities)})
