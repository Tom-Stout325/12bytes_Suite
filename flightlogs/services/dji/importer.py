from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime

from flightlogs.models import FlightLog, FlightLogSource
from flightlogs.services.matching import MatchType, match_existing_flight
from flightlogs.services.weather import enrich_flightlog_weather
from flightlogs.services.locations import enrich_flightlog_location
from flightlogs.services.aircraft_models import assign_aircraft_model
from flightlogs.services.import_normalization import (
    EquipmentMatchStatus,
    aircraft_equipment_for_business,
    assign_equipment_snapshot,
    assign_pilot_snapshot,
    match_aircraft_equipment,
    meters_asl_to_feet,
)

from .errors import import_error
from .subprocess_adapter import parse_dji_source

METERS_TO_FEET = 3.280839895013123
METERS_PER_SECOND_TO_MPH = 2.2369362920544
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DJIImportResult:
    source: FlightLogSource
    duplicate: bool = False
    match_type: MatchType | None = None


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
    takeoff_altitude_asl_ft = meters_asl_to_feet(payload.get("takeoff_altitude_asl_m"))
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
        "above_sea_level_ft": takeoff_altitude_asl_ft,
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


def _delete_successful_source_file(source_id, stored_name, storage):
    try:
        storage.delete(stored_name)
    except Exception:
        logger.warning("Successful DJI source file deletion failed safely")
        return
    FlightLogSource.objects.filter(pk=source_id, file=stored_name).update(file="")


def import_dji_upload(*, business, user, uploaded, pilot=None, equipment_candidates=None):
    if pilot is not None and pilot.business_id != business.pk:
        raise ValueError("Pilot and DJI upload must belong to the same business.")
    sha256 = _uploaded_sha256(uploaded)
    existing = FlightLogSource.objects.filter(
        business=business,
        sha256=sha256,
        status__in=(FlightLogSource.Status.COMPLETE, FlightLogSource.Status.REVIEW),
    ).first()
    if existing:
        return DJIImportResult(existing, duplicate=True)

    # Parsing is deliberately performed before a source row or storage object
    # is created. Parser failures therefore leave no hash that can block retry.
    parsed = parse_dji_source(uploaded)
    metadata = _source_metadata(parsed)
    flight_payload = _flightlog_payload(parsed)
    if equipment_candidates is None:
        equipment_candidates = aircraft_equipment_for_business(business)
    equipment_match = match_aircraft_equipment(
        metadata["aircraft_serial"], equipment_candidates
    )
    source = FlightLogSource(
        business=business,
        source_type=FlightLogSource.SourceType.DJI_TXT,
        original_filename=(uploaded.name or "DJIFlightRecord.txt")[:255],
        file=uploaded,
        sha256=sha256,
        size_bytes=uploaded.size,
        created_by=user,
    )
    stored_name = ""
    try:
        with transaction.atomic():
            stale = (
                FlightLogSource.objects.select_for_update()
                .filter(business=business, sha256=sha256)
                .first()
            )
            if stale and stale.status in {
                FlightLogSource.Status.COMPLETE,
                FlightLogSource.Status.REVIEW,
            }:
                return DJIImportResult(stale, duplicate=True)
            stale_file_name = stale.file.name if stale and stale.file else ""
            stale_storage = stale.file.storage if stale_file_name else None
            if stale:
                stale.delete()
            source.save()
            stored_name = source.file.name
            match = match_existing_flight(
                business=business,
                payload=flight_payload,
                authoritative_aircraft_serial=metadata["aircraft_serial"],
                authoritative_battery_serial=metadata["battery_serial"],
            )
            if match.match_type in {
                MatchType.HIGH_CONFIDENCE,
                MatchType.HIGH_CONFIDENCE_LOCATION_VARIANCE,
            }:
                flight_log = match.matched_flight
            elif match.match_type in {
                MatchType.PROBABLE,
                MatchType.REVIEW_PARTIAL_AIRDATA,
                MatchType.AMBIGUOUS,
            }:
                flight_log = None
            else:
                flight_log = FlightLog.objects.create(business=business, **flight_payload)
            if flight_log is not None:
                updated_fields = []
                if pilot is not None:
                    updated_fields.extend(assign_pilot_snapshot(flight_log, pilot))
                updated_fields.extend(assign_equipment_snapshot(flight_log, equipment_match))
                if updated_fields:
                    flight_log.save(update_fields=list(dict.fromkeys(updated_fields)))
            for field, value in metadata.items():
                setattr(source, field, value)
            source.flight_log = flight_log
            source.status = (
                FlightLogSource.Status.REVIEW
                if match.match_type
                in {
                    MatchType.PROBABLE,
                    MatchType.REVIEW_PARTIAL_AIRDATA,
                    MatchType.AMBIGUOUS,
                }
                else FlightLogSource.Status.COMPLETE
            )
            if (
                source.status == FlightLogSource.Status.COMPLETE
                and equipment_match.status == EquipmentMatchStatus.AMBIGUOUS
            ):
                source.status = FlightLogSource.Status.REVIEW
                source.safe_error_code = "DJI_EQUIPMENT_AMBIGUOUS"
                source.safe_error_detail = "Multiple aircraft equipment records matched the complete serial number."
            else:
                source.safe_error_code = ""
                source.safe_error_detail = ""
            source.save()
            if stale_file_name:
                transaction.on_commit(
                    lambda: stale_storage.delete(stale_file_name)
                )
            if (
                source.status == FlightLogSource.Status.COMPLETE
                and settings.DJI_DELETE_SUCCESSFUL_SOURCE_FILES
            ):
                committed_name = source.file.name
                storage = source.file.storage
                transaction.on_commit(
                    lambda: _delete_successful_source_file(source.pk, committed_name, storage)
                )
    except IntegrityError:
        if stored_name:
            source.file.storage.delete(stored_name)
        existing = FlightLogSource.objects.filter(
            business=business,
            sha256=sha256,
            status__in=(FlightLogSource.Status.COMPLETE, FlightLogSource.Status.REVIEW),
        ).first()
        if existing:
            return DJIImportResult(existing, duplicate=True)
        raise
    except Exception:
        if stored_name:
            source.file.storage.delete(stored_name)
        raise
    else:
        if (
            source.status in {FlightLogSource.Status.COMPLETE, FlightLogSource.Status.REVIEW}
            and source.flight_log_id
        ):
            try:
                assign_aircraft_model(
                    source.flight_log,
                    dji_model_code=source.aircraft_model_code,
                )
            except Exception:
                logger.warning("DJI aircraft-model resolution failed safely")
        # Weather is optional enrichment and must never change a successful DJI
        # parser/import result into a failure. CSV imports do not call this path.
        if match.match_type == MatchType.NO_MATCH:
            try:
                enrich_flightlog_location(flight_log)
            except Exception:
                logger.warning("DJI location enrichment failed safely")
            try:
                enrich_flightlog_weather(flight_log)
            except Exception:
                logger.warning("DJI weather enrichment failed safely")
    return DJIImportResult(source, match_type=match.match_type)
