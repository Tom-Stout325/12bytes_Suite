from __future__ import annotations

from assets.models import AircraftModel, normalize_aircraft_model_name


PACKAGE_SUFFIXES = ("fly-more-combo", "combo")


def _keys(model: AircraftModel) -> set[str]:
    values = {model.name, f"{model.manufacturer} {model.name}".strip(), *(model.aliases or [])}
    return {normalize_aircraft_model_name(value) for value in values if value}


def _candidate_keys(value: str) -> list[str]:
    """Return exact key first, then only clearly identified retail-package variants."""
    key = normalize_aircraft_model_name(value)
    keys = [key] if key else []
    for suffix in PACKAGE_SUFFIXES:
        marker = f"-{suffix}"
        if key.endswith(marker):
            keys.append(key.removesuffix(marker))
            break
    return keys


def resolve_aircraft_model(
    *, business, drone_type="", drone_name="", drone_serial="", dji_model_code=None,
    aircraft_models=None
):
    """Return one confident tenant-owned model match, otherwise None."""
    models = list(
        aircraft_models
        if aircraft_models is not None
        else AircraftModel.objects.filter(business=business)
    )
    serial = (drone_serial or "").strip()
    if serial:
        from assets.models import Asset
        equipment_matches = list(
            Asset.objects.filter(
                business=business,
                serial_number__iexact=serial,
                aircraft_model__isnull=False,
            ).values_list("aircraft_model_id", flat=True).distinct()
        )
        if len(equipment_matches) == 1:
            return next((model for model in models if model.pk == equipment_matches[0]), None)
    if dji_model_code is not None:
        code_matches = [model for model in models if model.dji_model_code == dji_model_code]
        if len(code_matches) == 1:
            return code_matches[0]

    for raw_value in (drone_type, drone_name):
        for key in _candidate_keys(raw_value):
            matches = [model for model in models if key in _keys(model)]
            if len(matches) == 1:
                return matches[0]
    return None


def assign_aircraft_model(flight_log, *, dji_model_code=None, aircraft_models=None):
    """Resolve and persist a canonical model without making import dependent on it."""
    if flight_log.aircraft_model_id:
        return flight_log.aircraft_model
    model = resolve_aircraft_model(
        business=flight_log.business,
        drone_type=flight_log.drone_type,
        drone_name=flight_log.drone_name,
        drone_serial=flight_log.drone_serial,
        dji_model_code=dji_model_code,
        aircraft_models=aircraft_models,
    )
    if model:
        flight_log.aircraft_model = model
        if flight_log.pk:
            flight_log.save(update_fields=["aircraft_model"])
    return model
