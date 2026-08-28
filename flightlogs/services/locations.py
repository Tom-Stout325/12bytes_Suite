from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from urllib import error, parse, request

from flightlogs.models import FlightLog
from flightlogs.services.airdata_timezones import parse_coordinates

logger = logging.getLogger(__name__)

NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_TIMEOUT_SECONDS = 5
COORDINATE_CACHE_DECIMALS = 5
OBVIOUS_COUNTRIES = {
    "australia", "canada", "france", "germany", "ireland", "italy", "japan",
    "mexico", "new zealand", "spain", "united kingdom",
}

US_ADDRESS_RE = re.compile(
    r"^(?P<street>.+),\s*(?P<city>[^,]+),\s*(?P<state>[A-Za-z]{2})\s+"
    r"(?P<postal>\d{5}(?:-\d{4})?)(?:,\s*(?P<country>USA|US|United States(?: of America)?))?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LocationComponents:
    city: str = ""
    state: str = ""
    country: str = ""
    postal_code: str = ""
    formatted_address: str = ""

    def as_model_fields(self) -> dict[str, str]:
        return {
            "takeoff_city": self.city,
            "takeoff_state": self.state,
            "takeoff_country": self.country,
            "takeoff_postal_code": self.postal_code,
            "takeoff_address": self.formatted_address,
        }


@dataclass(frozen=True)
class LocationEnrichmentResult:
    updated_fields: tuple[str, ...] = ()
    source: str = ""
    geocode_attempted: bool = False


_coordinate_cache: dict[tuple[int, float, float], LocationComponents] = {}


def clear_location_cache() -> None:
    _coordinate_cache.clear()


def _country_value(value: object, country_code: object = "") -> str:
    text = str(value or "").strip()
    code = str(country_code or "").strip().lower()
    if code == "us" or text.casefold() in {"us", "usa", "united states", "united states of america"}:
        return "USA"
    return text[:100]


def parse_takeoff_address(raw_address: object) -> LocationComponents:
    """Parse only confidently recognizable US-style address suffixes.

    The street portion is deliberately ignored and never rewritten. For an
    unrecognized international address, only an obvious trailing country name
    is retained; US city/state/postal values are never invented.
    """
    address = str(raw_address or "").strip()
    if not address:
        return LocationComponents()
    match = US_ADDRESS_RE.fullmatch(address)
    if match:
        return LocationComponents(
            city=match.group("city").strip()[:100],
            state=match.group("state").upper(),
            postal_code=match.group("postal"),
            country=_country_value(match.group("country") or "USA"),
        )
    parts = [part.strip() for part in address.split(",") if part.strip()]
    if len(parts) >= 3 and parts[-1].casefold() in OBVIOUS_COUNTRIES:
        country = _country_value(parts[-1])
        return LocationComponents(country=country)
    return LocationComponents()


def _fetch_json(url: str, *, timeout: int) -> object:
    http_request = request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "12Bytes-Suites-FlightLogs/1"},
    )
    with request.urlopen(http_request, timeout=timeout) as response:
        return json.load(response)


def _nominatim_components(payload: object) -> LocationComponents:
    if not isinstance(payload, dict):
        return LocationComponents()
    address = payload.get("address")
    if not isinstance(address, dict):
        return LocationComponents()
    city = next(
        (str(address.get(key) or "").strip() for key in ("city", "town", "village", "municipality", "hamlet") if address.get(key)),
        "",
    )
    state = str(address.get("state_code") or "").strip()
    if not state:
        iso_state = str(address.get("ISO3166-2-lvl4") or "").strip()
        state = iso_state.split("-", 1)[1] if iso_state.upper().startswith("US-") else str(address.get("state") or "").strip()
    return LocationComponents(
        city=city[:100],
        state=state[:100],
        country=_country_value(address.get("country"), address.get("country_code")),
        postal_code=str(address.get("postcode") or "").strip()[:20],
        formatted_address=str(payload.get("display_name") or "").strip()[:255],
    )


def reverse_geocode_coordinates(latitude: float, longitude: float) -> LocationComponents:
    query = parse.urlencode(
        {"lat": latitude, "lon": longitude, "format": "jsonv2", "addressdetails": 1, "zoom": 18}
    )
    try:
        payload = _fetch_json(f"{NOMINATIM_REVERSE_URL}?{query}", timeout=NOMINATIM_TIMEOUT_SECONDS)
        return _nominatim_components(payload)
    except (error.URLError, TimeoutError, json.JSONDecodeError, ValueError, TypeError):
        logger.warning("Flight-log reverse geocoding failed safely")
    except Exception:
        logger.warning("Unexpected flight-log reverse geocoding failure (details withheld)")
    return LocationComponents()


def _cache_key(business_id: int, coordinates: tuple[float, float]) -> tuple[int, float, float]:
    return (business_id, round(coordinates[0], COORDINATE_CACHE_DECIMALS), round(coordinates[1], COORDINATE_CACHE_DECIMALS))


def _existing_components(flight_log: FlightLog, coordinates: tuple[float, float]) -> LocationComponents:
    key = _cache_key(flight_log.business_id, coordinates)
    cached = _coordinate_cache.get(key)
    if cached is not None:
        return cached
    candidates = (
        FlightLog.objects.filter(business_id=flight_log.business_id)
        .exclude(pk=flight_log.pk)
        .exclude(takeoff_city="", takeoff_state="", takeoff_country="", takeoff_postal_code="")
        .only("takeoff_latlong", "takeoff_city", "takeoff_state", "takeoff_country", "takeoff_postal_code")
    )
    for candidate in candidates.iterator(chunk_size=500):
        candidate_coordinates = parse_coordinates(candidate.takeoff_latlong)
        if candidate_coordinates and _cache_key(flight_log.business_id, candidate_coordinates) == key:
            components = LocationComponents(
                candidate.takeoff_city,
                candidate.takeoff_state,
                candidate.takeoff_country,
                candidate.takeoff_postal_code,
                candidate.takeoff_address,
            )
            _coordinate_cache[key] = components
            return components
    return LocationComponents()


def enrich_flightlog_location(
    flight_log: FlightLog, *, allow_geocode: bool = True, force: bool = False
) -> LocationEnrichmentResult:
    current = {
        "takeoff_address": flight_log.takeoff_address,
        "takeoff_city": flight_log.takeoff_city,
        "takeoff_state": flight_log.takeoff_state,
        "takeoff_country": flight_log.takeoff_country,
        "takeoff_postal_code": flight_log.takeoff_postal_code,
    }
    updates: dict[str, str] = {}
    parsed = parse_takeoff_address(flight_log.takeoff_address)
    for field, value in parsed.as_model_fields().items():
        if value and (force or not current[field]):
            updates[field] = value
    source = "address" if updates else ""

    effective = {**current, **updates}
    missing_city_or_state = force or not effective["takeoff_city"] or not effective["takeoff_state"]
    coordinates = parse_coordinates(flight_log.takeoff_latlong)
    geocode_attempted = False
    if allow_geocode and missing_city_or_state and coordinates:
        components = _existing_components(flight_log, coordinates)
        if not any(components.as_model_fields().values()):
            geocode_attempted = True
            components = reverse_geocode_coordinates(*coordinates)
            if any(components.as_model_fields().values()):
                _coordinate_cache[_cache_key(flight_log.business_id, coordinates)] = components
        for field, value in components.as_model_fields().items():
            if value and (force or not effective[field]):
                updates[field] = value
        if components.city or components.state:
            source = "geocode"

    if updates:
        for field, value in updates.items():
            setattr(flight_log, field, value)
        flight_log.save(update_fields=list(updates))
    return LocationEnrichmentResult(tuple(updates), source, geocode_attempted)
