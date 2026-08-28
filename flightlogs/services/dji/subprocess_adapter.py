from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings

from .errors import DJIImportError, import_error

logger = logging.getLogger(__name__)

PARSER_TIMEOUT_SECONDS = 60
MAX_STDOUT_BYTES = 64 * 1024
MAX_STDERR_BYTES = 16 * 1024
DIAGNOSTIC_CODE_RE = re.compile(rb"diagnostic_code=([A-Z0-9_]+)")
CODE_ALIASES = {
    "FILE_READ_FAILURE": "DJI_IO_ERROR",
    "INVALID_DJI_FLIGHT_RECORD": "DJI_INVALID_FILE",
    "DJI_HTTP_STATUS_FAILURE": "DJI_KEYCHAIN_UNAVAILABLE",
    "DJI_NETWORK_TLS_FAILURE": "DJI_KEYCHAIN_UNAVAILABLE",
    "DJI_MALFORMED_RESPONSE": "DJI_KEYCHAIN_RESPONSE_INVALID",
    "DJI_RECORD_PARSE_FAILURE": "DJI_PARSE_ERROR",
}


def _safe_stderr(stderr):
    """Return bounded diagnostics while redacting the configured credential."""
    text = stderr[:MAX_STDERR_BYTES].decode("utf-8", errors="replace")
    api_key = os.environ.get("DJI_API_KEY", "")
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    return " ".join(text.split())[:2000]


EXPECTED_FIELDS = {
    "success",
    "parser_version",
    "log_version",
    "encrypted",
    "aircraft_model",
    "aircraft_model_code",
    "aircraft_name",
    "aircraft_serial",
    "aircraft_serial_header",
    "battery_serial",
    "battery_serial_header",
    "start_time",
    "duration_seconds",
    "airborne_duration_seconds",
    "takeoff_latitude",
    "takeoff_longitude",
    "takeoff_altitude_asl_m",
    "maximum_altitude_relative_m",
    "maximum_distance_from_home_m",
    "total_distance_m",
    "maximum_satellites",
    "minimum_satellites_airborne",
    "minimum_airborne_satellites",
    "minimum_gps_signal_level_airborne",
    "maximum_gps_signal_level",
    "takeoff_battery_percent",
    "landing_battery_percent",
    "takeoff_battery_voltage_v",
    "landing_battery_voltage_v",
    "takeoff_battery_capacity_mah",
    "landing_battery_capacity_mah",
    "maximum_battery_temperature_c",
    "minimum_cell_voltage_v",
    "maximum_cell_voltage_v",
    "battery_cycle_count",
    "battery_life_value",
    "battery_life_raw",
    "maximum_horizontal_speed_m_s",
    "maximum_vertical_speed_m_s",
    "maximum_vertical_speed_mps",
    "signal_loss_events_over_one_second",
    "photo_count",
    "flight_modes",
    "rc_serial",
    "camera_serial",
    "warnings",
    "serious_warnings",
    "tips",
    "messages",
}


def _safe_code_from_stderr(stderr):
    match = DIAGNOSTIC_CODE_RE.search(stderr[:MAX_STDERR_BYTES])
    if not match:
        return "DJI_PARSER_WORKER_FAILURE"
    code = match.group(1).decode("ascii", errors="ignore")
    return CODE_ALIASES.get(code, code)


def _validate_result(payload):
    if not isinstance(payload, dict) or set(payload) - EXPECTED_FIELDS:
        raise import_error("DJI_PARSER_OUTPUT_INVALID")
    if payload.get("success") is not True:
        raise import_error("DJI_PARSER_OUTPUT_INVALID")
    if (
        not isinstance(payload.get("parser_version"), str)
        or type(payload.get("log_version")) is not int
        or type(payload.get("encrypted")) is not bool
        or not all(
            isinstance(payload.get(field, []), list)
            for field in ("flight_modes", "warnings", "serious_warnings", "tips", "messages")
        )
    ):
        raise import_error("DJI_PARSER_OUTPUT_INVALID")
    return payload


def parse_dji_source(source_file):
    parser_path = Path(settings.DJI_PARSER_PATH).expanduser().resolve()
    exists = parser_path.is_file()
    executable = exists and os.access(parser_path, os.X_OK)
    if not executable:
        logger.error(
            "DJI parser unavailable path=%s exists=%s executable=%s",
            parser_path,
            exists,
            executable,
        )
        raise import_error("DJI_PARSER_MISSING")

    child_env = os.environ.copy()
    api_key = os.environ.get("DJI_API_KEY")
    if api_key:
        child_env["DJI_API_KEY"] = api_key

    with tempfile.NamedTemporaryFile(suffix=".txt") as input_file:
        source_file.open("rb")
        for chunk in source_file.chunks():
            input_file.write(chunk)
        source_file.seek(0)
        input_file.flush()

        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            try:
                completed = subprocess.run(
                    [str(parser_path), input_file.name],
                    shell=False,
                    env=child_env,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=PARSER_TIMEOUT_SECONDS,
                    check=False,
                )
            except FileNotFoundError as exc:
                logger.exception(
                    "DJI parser launch failed path=%s exists=%s executable=%s",
                    parser_path,
                    parser_path.is_file(),
                    os.access(parser_path, os.X_OK),
                )
                raise import_error("DJI_PARSER_MISSING") from exc
            except subprocess.TimeoutExpired as exc:
                logger.error(
                    "DJI parser timed out path=%s timeout_seconds=%s",
                    parser_path,
                    PARSER_TIMEOUT_SECONDS,
                )
                raise import_error("DJI_PARSER_TIMEOUT") from exc
            except OSError as exc:
                logger.exception(
                    "DJI parser OS failure path=%s exists=%s executable=%s",
                    parser_path,
                    parser_path.is_file(),
                    os.access(parser_path, os.X_OK),
                )
                raise import_error("DJI_PARSER_WORKER_FAILURE") from exc

            stdout_size = stdout_file.tell()
            stderr_size = stderr_file.tell()
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read(MAX_STDOUT_BYTES + 1)
            stderr = stderr_file.read(MAX_STDERR_BYTES)

    if completed.returncode != 0:
        code = _safe_code_from_stderr(stderr)
        logger.error(
            "DJI parser rejected input path=%s returncode=%s stderr=%r diagnostic_code=%s",
            parser_path,
            completed.returncode,
            _safe_stderr(stderr),
            code,
        )
        raise import_error(code)
    if stdout_size > MAX_STDOUT_BYTES or stderr_size > MAX_STDERR_BYTES:
        logger.error(
            "DJI parser output exceeded limit path=%s returncode=%s stdout_bytes=%s stderr_bytes=%s",
            parser_path,
            completed.returncode,
            stdout_size,
            stderr_size,
        )
        raise import_error("DJI_PARSER_OUTPUT_INVALID")
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.exception(
            "DJI parser returned invalid JSON path=%s returncode=%s stderr=%r",
            parser_path,
            completed.returncode,
            _safe_stderr(stderr),
        )
        raise import_error("DJI_PARSER_OUTPUT_INVALID") from exc
    return _validate_result(payload)
