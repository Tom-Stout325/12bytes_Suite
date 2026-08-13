from __future__ import annotations

from flightlogs.models import FlightLog


def battery_family_flights(*, business, drone_model):
    """Return the tenant-scoped source queryset for family-level summaries."""
    if not drone_model or not drone_model.battery_family_id:
        return FlightLog.objects.none()
    return FlightLog.objects.filter(
        business=business,
        drone_model__battery_family_id=drone_model.battery_family_id,
    )


def effective_battery_identifier(flight_log) -> str:
    return (flight_log.battery_serial_internal or flight_log.battery_serial_printed or "").strip()
