from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime

from flightlogs.models import FlightLog, FlightLogSource
from flightlogs.services.weather import enrich_flightlog_weather

from .errors import DJIImportError, import_error
from .subprocess_adapter import parse_dji_source

METERS_TO_FEET = 3.280839895013123
METERS_PER_SECOND_TO_MPH = 2.2369362920544
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DJIImportResult:
    source: FlightLogSource
    duplicate: bool = False


def _uploaded_sha256(uploaded):
    digest = hashlib.sha256()
    for chunk in uploaded.chunks():
        digest.update(chunk)
    uploaded.seek(0)
    return digest.hexdigest()


def _source_metadata(payload):
    return {
        "parser_version": _optional_text(payload, "parser_version")[:32],
        "log_version": payload.get("log_version"),
        "encrypted": payload.get("encrypted"),
        "aircraft_model_code": payload.get("aircraft_model_code"),
        "aircraft_serial": _optional_text(payload, "aircraft_serial"),
        "aircraft_serial_header": _optional_text(payload, "aircraft_serial_header"),
        "battery_serial": _optional_text(payload, "battery_serial"),
        "battery_serial_header": _optional_text(payload, "battery_serial_header"),
    }


def _optional_number(payload, field):
    value = payload.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise import_error("DJI_PARSER_OUTPUT_INVALID")
    return float(value)


def _optional_text(payload, field):
    value = payload.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise import_error("DJI_PARSER_OUTPUT_INVALID")
    return value[:100]


def _optional_integer(payload, field, *, maximum=None):
    value = payload.get(field)
    if value is None:
        return None
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        raise import_error("DJI_PARSER_OUTPUT_INVALID")
    return value


def _bounded_text_list(
    payload,
    field,
    *,
    max_items,
    max_item_chars,
    max_total_chars,
    separator,
):
    values = payload.get(field, [])
    if not isinstance(values, list):
        raise import_error("DJI_PARSER_OUTPUT_INVALID")
    output = []
    seen = set()
    for value in values[:max_items]:
        if not isinstance(value, str):
            raise import_error("DJI_PARSER_OUTPUT_INVALID")
        normalized = " ".join(value.split())[:max_item_chars]
        if normalized and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return separator.join(output)[:max_total_chars]


def _flightlog_payload(payload):
    start_time = parse_datetime(payload.get("start_time") or "")
    if start_time is None or start_time.tzinfo is None:
        raise import_error("DJI_PARSER_OUTPUT_INVALID")

    airborne_duration_seconds = _optional_number(payload, "airborne_duration_seconds")
    latitude = _optional_number(payload, "takeoff_latitude")
    longitude = _optional_number(payload, "takeoff_longitude")
    if (latitude is None) != (longitude is None):
        raise import_error("DJI_PARSER_OUTPUT_INVALID")

    # FlightLog stores imperial performance fields. Rust emits validated meters.
    altitude_m = _optional_number(payload, "maximum_altitude_relative_m")
    max_distance_m = _optional_number(payload, "maximum_distance_from_home_m")
    total_distance_m = _optional_number(payload, "total_distance_m")
    maximum_speed_m_s = _optional_number(payload, "maximum_horizontal_speed_m_s")
    maximum_battery_temperature_c = _optional_number(
        payload, "maximum_battery_temperature_c"
    )
    takeoff_battery_percent = _optional_integer(payload, "takeoff_battery_percent")
    landing_battery_percent = _optional_integer(payload, "landing_battery_percent")
    takeoff_battery_capacity_mah = _optional_integer(
        payload, "takeoff_battery_capacity_mah"
    )
    landing_battery_capacity_mah = _optional_integer(
        payload, "landing_battery_capacity_mah"
    )
    signal_losses = _optional_integer(payload, "signal_loss_events_over_one_second")
    photo_count = _optional_integer(payload, "photo_count")
    maximum_satellites = _optional_integer(payload, "maximum_satellites", maximum=255)
    minimum_airborne_satellites = _optional_integer(
        payload,
        "minimum_airborne_satellites",
        maximum=255,
    )
    if minimum_airborne_satellites is None:
        minimum_airborne_satellites = _optional_integer(
            payload,
            "minimum_satellites_airborne",
            maximum=255,
        )
    minimum_airborne_gps_level = _optional_integer(
        payload,
        "minimum_gps_signal_level_airborne",
        maximum=255,
    )
    battery_cycle_count = _optional_integer(payload, "battery_cycle_count", maximum=65535)
    battery_life_raw = _optional_integer(payload, "battery_life_raw", maximum=255)
    if battery_life_raw is None:
        battery_life_raw = _optional_integer(payload, "battery_life_value", maximum=255)
    minimum_cell_voltage_v = _optional_number(payload, "minimum_cell_voltage_v")
    maximum_cell_voltage_v = _optional_number(payload, "maximum_cell_voltage_v")
    if any(
        value is not None and value <= 0
        for value in (minimum_cell_voltage_v, maximum_cell_voltage_v)
    ):
        raise import_error("DJI_PARSER_OUTPUT_INVALID")
    maximum_vertical_speed_mps = _optional_number(payload, "maximum_vertical_speed_mps")
    if maximum_vertical_speed_mps is None:
        maximum_vertical_speed_mps = _optional_number(payload, "maximum_vertical_speed_m_s")
    if maximum_vertical_speed_mps is not None and maximum_vertical_speed_mps < 0:
        raise import_error("DJI_PARSER_OUTPUT_INVALID")
    if any(
        value is not None and value > 100
        for value in (takeoff_battery_percent, landing_battery_percent)
    ):
        raise import_error("DJI_PARSER_OUTPUT_INVALID")
    aircraft_model = _optional_text(payload, "aircraft_model")
    aircraft_name = _optional_text(payload, "aircraft_name")
    aircraft_serial = _optional_text(payload, "aircraft_serial")
    battery_serial = _optional_text(payload, "battery_serial")
    battery_serial_header = _optional_text(payload, "battery_serial_header")

    return {
        "flight_date": start_time.date(),
        "takeoff_datetime": start_time,
        "takeoff_latlong": (
            f"{latitude:.8f}, {longitude:.8f}" if latitude is not None else ""
        ),
        # Unlike Details.total_time (motor-start record duration), this is
        # summed only across decoded airborne OSD frame intervals.
        "air_time": (
            timedelta(seconds=airborne_duration_seconds)
            if airborne_duration_seconds is not None
            else None
        ),
        "drone_type": aircraft_model,
        "drone_name": aircraft_name,
        "drone_serial": aircraft_serial,
        # ComponentSerial is authoritative when present. The fixed-width
        # Details header is provenance-compatible fallback data and may be
        # truncated, but is preferable to discarding the available serial.
        "battery_serial_internal": battery_serial or battery_serial_header,
        "takeoff_battery_pct": takeoff_battery_percent,
        "takeoff_mah": takeoff_battery_capacity_mah,
        "takeoff_volts": _optional_number(payload, "takeoff_battery_voltage_v"),
        "landing_battery_pct": landing_battery_percent,
        "landing_mah": landing_battery_capacity_mah,
        "landing_volts": _optional_number(payload, "landing_battery_voltage_v"),
        "battery_cycle_count": battery_cycle_count,
        "minimum_cell_voltage_v": minimum_cell_voltage_v,
        "maximum_cell_voltage_v": maximum_cell_voltage_v,
        # DJI's record field is retained as a raw ordinal/value; no percentage
        # semantics are assigned by this importer.
        "battery_life_raw": battery_life_raw,
        "max_altitude_ft": altitude_m * METERS_TO_FEET if altitude_m is not None else None,
        "max_distance_ft": max_distance_m * METERS_TO_FEET if max_distance_m is not None else None,
        "max_battery_temp_f": (
            maximum_battery_temperature_c * 9.0 / 5.0 + 32.0
            if maximum_battery_temperature_c is not None
            else None
        ),
        "max_speed_mph": (
            maximum_speed_m_s * METERS_PER_SECOND_TO_MPH
            if maximum_speed_m_s is not None
            else None
        ),
        "maximum_vertical_speed_mps": maximum_vertical_speed_mps,
        "total_mileage_ft": total_distance_m * METERS_TO_FEET if total_distance_m is not None else None,
        "signal_losses": signal_losses,
        "maximum_satellites": maximum_satellites,
        "minimum_airborne_satellites": minimum_airborne_satellites,
        # This is DJI's ordinal GPS quality code, never a percentage.
        "minimum_airborne_gps_level": minimum_airborne_gps_level,
        "flight_modes": _bounded_text_list(
            payload,
            "flight_modes",
            max_items=25,
            max_item_chars=50,
            max_total_chars=500,
            separator=", ",
        ),
        "dji_warnings": _bounded_text_list(
            payload,
            "warnings",
            max_items=25,
            max_item_chars=300,
            max_total_chars=7500,
            separator="\n",
        ),
        "dji_serious_warnings": _bounded_text_list(
            payload,
            "serious_warnings",
            max_items=25,
            max_item_chars=300,
            max_total_chars=7500,
            separator="\n",
        ),
        "dji_tips": _bounded_text_list(
            payload,
            "tips" if "tips" in payload else "messages",
            max_items=25,
            max_item_chars=300,
            max_total_chars=7500,
            separator="\n",
        ),
        "rc_serial": _optional_text(payload, "rc_serial"),
        "camera_serial": _optional_text(payload, "camera_serial"),
        "photos": photo_count,
    }


def _mark_failed(source, failure):
    source.status = FlightLogSource.Status.FAILED
    source.safe_error_code = failure.code
    source.safe_error_detail = failure.detail
    source.save(update_fields=["status", "safe_error_code", "safe_error_detail", "updated_at"])


def import_dji_upload(*, business, user, uploaded):
    sha256 = _uploaded_sha256(uploaded)
    existing = FlightLogSource.objects.filter(business=business, sha256=sha256).first()
    if existing:
        return DJIImportResult(existing, duplicate=True)

    source = FlightLogSource(
        business=business,
        source_type=FlightLogSource.SourceType.DJI_TXT,
        original_filename=(uploaded.name or "DJIFlightRecord.txt")[:255],
        file=uploaded,
        sha256=sha256,
        size_bytes=uploaded.size,
        created_by=user,
    )
    stored_name = None
    try:
        with transaction.atomic():
            source.save()
            stored_name = source.file.name
    except IntegrityError:
        stored_name = source.file.name if source.file._committed else stored_name
        if stored_name:
            source.file.storage.delete(stored_name)
        existing = FlightLogSource.objects.get(business=business, sha256=sha256)
        return DJIImportResult(existing, duplicate=True)

    source.status = FlightLogSource.Status.PARSING
    source.save(update_fields=["status", "updated_at"])
    try:
        parsed = parse_dji_source(source.file)
        metadata = _source_metadata(parsed)
        flight_payload = _flightlog_payload(parsed)
        with transaction.atomic():
            flight_log = FlightLog.objects.create(business=business, **flight_payload)
            for field, value in metadata.items():
                setattr(source, field, value)
            source.flight_log = flight_log
            source.status = FlightLogSource.Status.COMPLETE
            source.safe_error_code = ""
            source.safe_error_detail = ""
            source.save()
    except DJIImportError as failure:
        _mark_failed(source, failure)
    except Exception:
        _mark_failed(source, import_error("DJI_PARSER_WORKER_FAILURE"))
    else:
        # Weather is optional enrichment and must never change a successful DJI
        # parser/import result into a failure. CSV imports do not call this path.
        try:
            enrich_flightlog_weather(flight_log)
        except Exception:
            logger.warning("DJI weather enrichment failed safely")
    return DJIImportResult(source)
