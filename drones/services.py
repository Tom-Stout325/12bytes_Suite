from __future__ import annotations

from django.db.models import Q

from .models import DroneModel, DroneModelAlias, DroneModelIdentifier, normalize_catalog_name


def _normalized_identifier(value: object) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def resolve_drone_model(
    *,
    business=None,
    drone_serial: str = "",
    provider: str = "",
    identifier: object = "",
    model_text: str = "",
) -> DroneModel | None:
    """Resolve only exact catalog evidence; return None when evidence conflicts."""
    serial = " ".join((drone_serial or "").strip().split())
    if business is not None and serial:
        from assets.models import Asset

        model_ids = list(
            Asset.objects.filter(
                business=business,
                serial_number__iexact=serial,
                drone_model__isnull=False,
            )
            .order_by()
            .values_list("drone_model_id", flat=True)
            .distinct()
        )
        if len(model_ids) == 1:
            return DroneModel.objects.filter(pk=model_ids[0], active=True).first()
        if len(model_ids) > 1:
            return None

    provider_key = " ".join((provider or "").strip().split())
    identifier_key = _normalized_identifier(identifier)
    if provider_key and identifier_key:
        matches = DroneModelIdentifier.objects.filter(
            normalized_provider=normalize_catalog_name(provider_key),
            normalized_identifier=identifier_key,
            drone_model__active=True,
        ).select_related("drone_model")
        if matches.count() == 1:
            return matches.first().drone_model

    model_key = normalize_catalog_name(model_text)
    if not model_key:
        return None

    alias = DroneModelAlias.objects.filter(
        normalized_alias=model_key,
        drone_model__active=True,
    ).select_related("drone_model").first()
    if alias:
        return alias.drone_model

    matches = DroneModel.objects.filter(active=True).filter(
        Q(normalized_name=model_key)
        | Q(full_display_name__iexact=" ".join((model_text or "").strip().split()))
    ).distinct()
    return matches.first() if matches.count() == 1 else None
