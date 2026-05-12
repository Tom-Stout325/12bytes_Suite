from __future__ import annotations

import csv
import re
import tempfile
from calendar import month_name
from datetime import datetime, timedelta

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


@login_required
def drone_portal(request):
    qs = _business_logs(request)
    total_seconds = 0
    for value in qs.exclude(air_time__isnull=True).values_list("air_time", flat=True):
        total_seconds += int(value.total_seconds())
    context = {
        "current_page": "flightlogs",
        "total_flights": qs.count(),
        "total_flight_time_seconds": total_seconds,
        "active_drones": qs.exclude(drone_name="").values("drone_name").distinct().count(),
        "highest_altitude_flight": qs.exclude(max_altitude_ft__isnull=True).order_by("-max_altitude_ft").first(),
        "fastest_speed_flight": qs.exclude(max_speed_mph__isnull=True).order_by("-max_speed_mph").first(),
        "longest_flight": qs.exclude(max_distance_ft__isnull=True).order_by("-max_distance_ft").first(),
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
        field_aliases = {"Flight/Service Date": "Flight Date/Time"}

        created = skipped = errored = 0
        for raw_row in reader:
            try:
                row = {}
                for k, v in (raw_row or {}).items():
                    key = (k or "").strip().replace("\ufeff", "")
                    key = field_aliases.get(key, key)
                    row[key] = (v.strip() if isinstance(v, str) else (v or ""))

                dt_raw = (row.get("Flight Date/Time") or "").strip()
                if not dt_raw:
                    skipped += 1
                    continue
                dt_raw_clean = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", dt_raw)
                dt = None
                for fmt in (
                    "%b %d, %Y %I:%M%p", "%b %d, %Y %I:%M:%S%p",
                    "%B %d, %Y %I:%M%p", "%B %d, %Y %I:%M:%S%p",
                    "%B %d, %Y %I:%M:%S %p", "%B %d, %Y %I:%M %p",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
                    "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p",
                ):
                    try:
                        dt = datetime.strptime(dt_raw_clean, fmt)
                        break
                    except Exception:
                        continue
                if dt is None:
                    try:
                        dt = datetime.fromisoformat(dt_raw_clean.replace("Z", ""))
                    except Exception:
                        dt = None
                if dt is None:
                    errored += 1
                    if errored <= 5:
                        messages.error(request, f"Could not parse Flight Date/Time: '{dt_raw}'")
                    continue

                FlightLog.objects.create(
                    business=request.business,
                    flight_date=dt.date(),
                    flight_title=row.get("Flight Title", ""),
                    flight_description=row.get("Flight Description", ""),
                    pilot_in_command=row.get("Pilot-in-Command", ""),
                    license_number=row.get("License Number", ""),
                    flight_application=row.get("Flight App", ""),
                    remote_id=row.get("Remote ID", ""),
                    takeoff_latlong=row.get("Takeoff Lat/Long", ""),
                    takeoff_address=row.get("Takeoff Address", ""),
                    landing_time=dt.time(),
                    air_time=timedelta(seconds=safe_int(row.get("Air Seconds")) or 0),
                    above_sea_level_ft=safe_float(row.get("Above Sea Level (Feet)")),
                    drone_name=row.get("Drone Name", ""),
                    drone_type=row.get("Drone Type", ""),
                    drone_serial=row.get("Drone Serial Number", ""),
                    drone_reg_number=row.get("Drone Registration Number", ""),
                    battery_name=row.get("Battery Name", ""),
                    battery_serial_printed=row.get("Bat Printed Serial", ""),
                    battery_serial_internal=row.get("Bat Internal Serial", ""),
                    takeoff_battery_pct=safe_pct(row.get("Takeoff Bat %")),
                    takeoff_mah=safe_int(row.get("Takeoff mAh")),
                    takeoff_volts=safe_float(row.get("Takeoff Volts")),
                    landing_battery_pct=safe_pct(row.get("Landing Bat %")),
                    landing_mah=safe_int(row.get("Landing mAh")),
                    landing_volts=safe_float(row.get("Landing Volts")),
                    max_altitude_ft=safe_float(row.get("Max Altitude (Feet)")),
                    max_distance_ft=safe_float(row.get("Max Distance (Feet)")),
                    max_battery_temp_f=safe_float(row.get("Max Bat Temp (f)")),
                    max_speed_mph=safe_float(row.get("Max Speed (mph)")),
                    total_mileage_ft=safe_float(row.get("Total Mileage (Feet)")),
                    signal_score=safe_float(row.get("Signal Score")),
                    max_compass_rate=safe_float(row.get("Max Compass Rate")),
                    avg_wind=safe_float(row.get("Avg Wind")),
                    max_gust=safe_float(row.get("Max Gust")),
                    signal_losses=safe_int(row.get("Signal Losses (>1 sec)")),
                    ground_weather_summary=row.get("Ground Weather Summary", ""),
                    ground_temp_f=safe_float(row.get("Ground Temperature (f)")),
                    visibility_miles=safe_float(row.get("Ground Visibility (Miles)")),
                    wind_speed=safe_float(row.get("Ground Wind Speed")),
                    wind_direction=row.get("Ground Wind Direction", ""),
                    cloud_cover=row.get("Cloud Cover", ""),
                    humidity_pct=safe_pct(row.get("Humidity")),
                    dew_point_f=safe_float(row.get("Dew Point (f)")),
                    pressure_inhg=safe_float(row.get("Pressure")),
                    rain_rate=row.get("Rain Rate", ""),
                    rain_chance=row.get("Rain Chance", ""),
                    sunrise=row.get("Sunrise", ""),
                    sunset=row.get("Sunset", ""),
                    moon_phase=row.get("Moon Phase", ""),
                    moon_visibility=row.get("Moon Visibility", ""),
                    photos=safe_int(row.get("Photos")),
                    videos=safe_int(row.get("Videos")),
                    notes=row.get("Add Additional Notes", ""),
                    tags=row.get("Tags", ""),
                )
                created += 1
            except Exception as e:
                errored += 1
                if errored <= 5:
                    messages.error(request, f"Row save error: {e}")
        messages.success(request, f"CSV processed. Created: {created}, Skipped: {skipped}, Errors: {errored}")
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
