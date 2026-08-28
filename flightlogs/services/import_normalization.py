from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from django.db.models import Q

from assets.models import Asset
from flightlogs.models import FlightLog
from pilot.models import PilotProfile

METERS_TO_FEET = 3.280839895013123
MINIMUM_PLAUSIBLE_ASL_M = -500.0
MAXIMUM_PLAUSIBLE_ASL_M = 10_000.0


def normalize_serial_number(value: object) -> str:
    return str(value or "").strip().casefold()


def meters_asl_to_feet(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    meters = float(value)
    if not math.isfinite(meters) or not MINIMUM_PLAUSIBLE_ASL_M <= meters <= MAXIMUM_PLAUSIBLE_ASL_M:
        return None
    return meters * METERS_TO_FEET


class EquipmentMatchStatus(StrEnum):
    MATCHED = "matched"
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class EquipmentMatch:
    status: EquipmentMatchStatus
    equipment: Asset | None = None


def aircraft_equipment_for_business(business) -> tuple[Asset, ...]:
    return tuple(
        Asset.objects.filter(business=business)
        .filter(
            Q(drone_model__isnull=False)
            | Q(aircraft_model__isnull=False)
        )
        .select_related("drone_model", "aircraft_model")
    )


def match_aircraft_equipment(serial_number: object, equipment: tuple[Asset, ...]) -> EquipmentMatch:
    normalized = normalize_serial_number(serial_number)
    if not normalized:
        return EquipmentMatch(EquipmentMatchStatus.UNMATCHED)
    matches = [
        item
        for item in equipment
        if (item.normalized_serial_number or normalize_serial_number(item.serial_number)) == normalized
    ]
    if len(matches) == 1:
        return EquipmentMatch(EquipmentMatchStatus.MATCHED, matches[0])
    if len(matches) > 1:
        return EquipmentMatch(EquipmentMatchStatus.AMBIGUOUS)
    return EquipmentMatch(EquipmentMatchStatus.UNMATCHED)


def assign_pilot_snapshot(flight_log: FlightLog, pilot: PilotProfile) -> tuple[str, ...]:
    if pilot.business_id != flight_log.business_id:
        raise ValueError("Pilot and flight must belong to the same business.")
    flight_log.pilot = pilot
    flight_log.pilot_in_command = pilot.pilot_name[:100]
    flight_log.license_number = (pilot.license_number or "")[:100]
    return ("pilot", "pilot_in_command", "license_number")


def assign_equipment_snapshot(flight_log: FlightLog, match: EquipmentMatch) -> tuple[str, ...]:
    if match.status != EquipmentMatchStatus.MATCHED or match.equipment is None:
        return ()
    equipment = match.equipment
    if equipment.business_id != flight_log.business_id:
        raise ValueError("Equipment and flight must belong to the same business.")
    flight_log.equipment = equipment
    updated = ["equipment"]
    registration = (equipment.faa_registration or "").strip()
    if registration:
        flight_log.drone_reg_number = registration[:100]
        updated.append("drone_reg_number")
    return tuple(updated)
