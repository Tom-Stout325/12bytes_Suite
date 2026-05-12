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
from django.db.models import Count, Sum, Max
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

from .forms import FlightLogCSVUploadForm, FlightLogForm
from .models import FlightLog

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
    flight_date = parse_date_value(row_value(row, "flight_date", "Flight Date/Time", "Flight/Service Date"))
    landing_time = parse_time_value(row_value(row, "landing_time", "Landing Time"))
    if landing_time is None:
        landing_time = parse_time_value(row_value(row, "Flight Date/Time", "Flight/Service Date"))
    air_time = parse_duration_value(row_value(row, "air_time", "Air Time", "Air Seconds"))

    return {
        "flight_date": flight_date,
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

    base_qs = _business_logs(request)
    logs_qs = base_qs

    years = sorted(y for y in base_qs.annotate(y=ExtractYear("flight_date")).values_list("y", flat=True).distinct() if y)
    months_present = sorted(m for m in base_qs.annotate(m=ExtractMonth("flight_date")).values_list("m", flat=True).distinct() if m)
    month_labels = {i: month_name[i] for i in range(1, 13)}

    addresses = list(base_qs.exclude(takeoff_address__exact="").values_list("takeoff_address", flat=True))
    states = sorted({extract_state(addr) for addr in addresses if extract_state(addr)})
    cities = sorted({city for addr in addresses if (not sel_state or extract_state(addr) == sel_state) for city in [_extract_city(addr)] if city})

    if sel_year.isdigit():
        logs_qs = logs_qs.filter(flight_date__year=int(sel_year))
    if sel_month.isdigit():
        logs_qs = logs_qs.filter(flight_date__month=int(sel_month))
    if sel_state:
        logs_qs = logs_qs.filter(takeoff_address__regex=rf",\s*{re.escape(sel_state)}(?:[, ]|$)")
    if sel_city:
        logs_qs = logs_qs.filter(takeoff_address__istartswith=f"{sel_city},")
    if sel_location:
        logs_qs = logs_qs.filter(takeoff_address__icontains=sel_location) | logs_qs.filter(takeoff_latlong__icontains=sel_location)

    paginator = Paginator(logs_qs.order_by("-flight_date"), 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    qs = request.GET.copy()
    qs.pop("page", None)

    return render(request, "flightlogs/flightlog_list.html", {
        "logs": page_obj,
        "current_page": "flightlogs",
        "sel_state": sel_state,
        "sel_city": sel_city,
        "sel_year": sel_year,
        "sel_month": sel_month,
        "states": states,
        "cities": cities,
        "years": years,
        "months_present": months_present,
        "month_labels": month_labels,
        "qs_without_page": qs.urlencode(),
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

        created = skipped = errored = duplicate_skipped = 0
        for raw_row in reader:
            try:
                row = _normalised_row(raw_row)
                payload = _flightlog_payload_from_csv_row(row)

                if not payload.get("flight_date"):
                    skipped += 1
                    if skipped <= 5:
                        messages.warning(request, "Skipped a row because no valid flight_date/Flight Date was found.")
                    continue

                if _flightlog_duplicate_exists(request.business, payload):
                    duplicate_skipped += 1
                    continue

                FlightLog.objects.create(business=request.business, **payload)
                created += 1
            except Exception as e:
                errored += 1
                if errored <= 5:
                    messages.error(request, f"Row save error: {e}")

        total_skipped = skipped + duplicate_skipped
        messages.success(
            request,
            f"CSV processed. Created: {created}, Skipped: {total_skipped}, Errors: {errored}"
            + (f" ({duplicate_skipped} duplicate rows skipped)" if duplicate_skipped else ""),
        )
        return redirect("flightlogs:flightlog_list")

    form = FlightLogCSVUploadForm()
    return render(request, "flightlogs/flightlog_form.html", {"form": form, "current_page": "flightlogs"})


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
