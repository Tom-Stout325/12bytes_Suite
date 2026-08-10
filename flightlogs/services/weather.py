from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from urllib import error, parse, request

from flightlogs.models import FlightLog

logger = logging.getLogger(__name__)

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_TIMEOUT_SECONDS = 5
HOURLY_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "weather_code",
    "pressure_msl",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
)

HPA_TO_INHG = 0.029529983071445
KMH_TO_MPH = 0.621371192237334

# Open-Meteo documents these as WMO weather interpretation codes.
WMO_WEATHER_SUMMARIES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def _fetch_json(url: str, *, timeout: int) -> object:
    http_request = request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Suite-FlightLogs/1"},
    )
    with request.urlopen(http_request, timeout=timeout) as response:
        return json.load(response)


def _coordinates(value: str) -> tuple[float, float] | None:
    try:
        latitude_text, longitude_text = value.split(",", maxsplit=1)
        latitude = float(latitude_text.strip())
        longitude = float(longitude_text.strip())
    except (AttributeError, TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _utc_hour(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # The request explicitly asks Open-Meteo for UTC.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _nearest_hour_index(hourly: dict, takeoff: datetime) -> int | None:
    times = hourly.get("time")
    if not isinstance(times, list) or not times:
        return None
    candidates = [(_utc_hour(value), index) for index, value in enumerate(times)]
    candidates = [(value, index) for value, index in candidates if value is not None]
    if not candidates:
        return None
    takeoff_utc = takeoff.astimezone(timezone.utc)
    # In an exact tie, normal API ordering deterministically selects the earlier hour.
    return min(candidates, key=lambda item: abs(item[0] - takeoff_utc))[1]


def _hourly_value(hourly: dict, name: str, index: int) -> object:
    values = hourly.get(name)
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]


def _temperature_f(value: object) -> float | None:
    celsius = _finite_number(value)
    return celsius * 9 / 5 + 32 if celsius is not None else None


def _integer_percentage(value: object) -> int | None:
    number = _finite_number(value)
    if number is None or not 0 <= number <= 100 or not number.is_integer():
        return None
    return int(number)


def _direction_text(value: object) -> str | None:
    degrees = _finite_number(value)
    if degrees is None or not 0 <= degrees <= 360:
        return None
    return str(int(degrees)) if degrees.is_integer() else str(degrees)


def _weather_updates(hourly: dict, index: int) -> dict[str, object]:
    updates: dict[str, object] = {}

    mappings = {
        "ground_temp_f": _temperature_f(_hourly_value(hourly, "temperature_2m", index)),
        "dew_point_f": _temperature_f(_hourly_value(hourly, "dew_point_2m", index)),
        "humidity_pct": _integer_percentage(
            _hourly_value(hourly, "relative_humidity_2m", index)
        ),
    }
    pressure = _finite_number(_hourly_value(hourly, "pressure_msl", index))
    wind_speed = _finite_number(_hourly_value(hourly, "wind_speed_10m", index))
    cloud_cover = _integer_percentage(_hourly_value(hourly, "cloud_cover", index))
    weather_code = _finite_number(_hourly_value(hourly, "weather_code", index))
    mappings.update(
        {
            "pressure_inhg": pressure * HPA_TO_INHG if pressure is not None else None,
            "wind_speed": wind_speed * KMH_TO_MPH if wind_speed is not None else None,
            "wind_direction": _direction_text(
                _hourly_value(hourly, "wind_direction_10m", index)
            ),
            "cloud_cover": f"{cloud_cover}%" if cloud_cover is not None else None,
            "ground_weather_summary": (
                WMO_WEATHER_SUMMARIES.get(int(weather_code))
                if weather_code is not None and weather_code.is_integer()
                else None
            ),
        }
    )
    for field, value in mappings.items():
        if value is not None:
            updates[field] = value
    return updates


def enrich_flightlog_weather(flight_log: FlightLog) -> bool:
    """Add nearest-hour UTC Open-Meteo archive weather to one normalized log.

    This deliberately leaves visibility blank because the Historical Weather
    archive does not offer it. Hourly precipitation/rain are not requested or
    stored: their preceding-hour accumulation semantics do not safely match
    FlightLog.rain_rate. FlightLog.avg_wind and max_gust are reserved for
    in-flight telemetry and are never populated here. No API timestamp
    provenance field currently exists.
    """
    coordinates = _coordinates(flight_log.takeoff_latlong)
    takeoff = flight_log.takeoff_datetime
    if coordinates is None or takeoff is None or takeoff.tzinfo is None:
        return False

    takeoff_utc = takeoff.astimezone(timezone.utc)
    query_start = (takeoff_utc - timedelta(hours=1)).date().isoformat()
    query_end = (takeoff_utc + timedelta(hours=1)).date().isoformat()
    query = parse.urlencode(
        {
            "latitude": coordinates[0],
            "longitude": coordinates[1],
            "start_date": query_start,
            "end_date": query_end,
            "hourly": ",".join(HOURLY_VARIABLES),
            "timezone": "UTC",
            "timeformat": "iso8601",
        }
    )

    try:
        payload = _fetch_json(
            f"{OPEN_METEO_ARCHIVE_URL}?{query}",
            timeout=OPEN_METEO_TIMEOUT_SECONDS,
        )
        if not isinstance(payload, dict) or payload.get("error") is True:
            raise ValueError("invalid weather response")
        hourly = payload.get("hourly")
        if not isinstance(hourly, dict):
            raise ValueError("missing hourly weather")
        index = _nearest_hour_index(hourly, takeoff_utc)
        if index is None:
            return False
        updates = _weather_updates(hourly, index)
        if not updates:
            return False
        for field, value in updates.items():
            setattr(flight_log, field, value)
        flight_log.save(update_fields=list(updates))
        return True
    except (error.URLError, TimeoutError, json.JSONDecodeError, ValueError, TypeError):
        logger.warning("Open-Meteo flight-log enrichment failed safely")
        return False
    except Exception:
        logger.warning("Unexpected weather enrichment failure (details withheld)")
        return False
