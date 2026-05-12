from __future__ import annotations

from typing import Iterable, Optional

from django.apps import apps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model, QuerySet
from django.http import Http404, HttpRequest, HttpResponse

from .exports import ExportSpec, export_queryset_csv, model_fields_spec, col_fn


def _find_unique_model_by_classnames(classnames: Iterable[str]) -> type[Model]:
    """Find a unique installed model by class name.

    This avoids hard-coding app labels (ledger/invoices/vehicles/etc.) and is robust
    as long as class names are unique in the project.
    """

    wanted = {c.lower() for c in classnames}
    matches: list[type[Model]] = []
    for m in apps.get_models():
        if m.__name__.lower() in wanted:
            matches.append(m)

    if not matches:
        raise Http404(f"No model found for {', '.join(classnames)}")

    # If multiple matches, require explicit settings-based wiring later.
    # For now, surface a clear error.
    uniq = {(m._meta.app_label, m.__name__): m for m in matches}
    if len(uniq) != 1:
        names = ", ".join([f"{a}.{n}" for (a, n) in uniq.keys()])
        raise ImproperlyConfigured(
            f"Multiple models match {', '.join(classnames)}: {names}. "
            "Rename for uniqueness or create explicit export wiring."
        )

    return next(iter(uniq.values()))


def _business_scoped_queryset(request: HttpRequest, model: type[Model]) -> QuerySet:
    qs = model._default_manager.all()

    # Business scoping: if model has a 'business' field and request has request.business.
    field_names = {f.name for f in model._meta.get_fields()}
    if "business" in field_names:
        business = getattr(request, "business", None)
        if business is None:
            # If your project uses a different mechanism, you can customize here.
            raise ImproperlyConfigured(
                "request.business is missing; ensure your Business middleware is installed "
                "or update core.views_exports._business_scoped_queryset."
            )
        qs = qs.filter(business=business)

    return qs


def _default_ordering(model: type[Model]) -> list[str]:
    fields = {f.name for f in model._meta.get_fields()}
    for candidate in ("date", "issue_date", "created_at", "updated_at"):
        if candidate in fields:
            return [f"-{candidate}", "-id"] if "id" in fields else [f"-{candidate}"]
    return ["-id"] if "id" in fields else []


def _spec_for(model: type[Model], filename_prefix: str, extras: Optional[list] = None) -> ExportSpec:
    spec = model_fields_spec(model=model, filename_prefix=filename_prefix)
    if extras:
        spec.columns.extend(extras)
    return spec


@login_required
def export_invoices_csv(request: HttpRequest) -> HttpResponse:
    Invoice = _find_unique_model_by_classnames(["Invoice"])  # type: ignore
    qs = _business_scoped_queryset(request, Invoice).order_by(*_default_ordering(Invoice))

    extras = []
    # Option A totals (computed live) if properties exist
    if hasattr(Invoice, "subtotal_amount"):
        extras.append(col_fn("subtotal_amount", lambda inv: getattr(inv, "subtotal_amount", "")))
    if hasattr(Invoice, "total_amount"):
        extras.append(col_fn("total_amount", lambda inv: getattr(inv, "total_amount", "")))

    spec = _spec_for(Invoice, "invoices", extras)
    return export_queryset_csv(request=request, queryset=qs, spec=spec)


@login_required
def export_transactions_csv(request: HttpRequest) -> HttpResponse:
    Transaction = _find_unique_model_by_classnames(["Transaction"])  # type: ignore
    qs = _business_scoped_queryset(request, Transaction).order_by(*_default_ordering(Transaction))

    extras = []
    if hasattr(Transaction, "deductible_amount"):
        extras.append(col_fn("deductible_amount", lambda t: getattr(t, "deductible_amount", "")))

    spec = _spec_for(Transaction, "transactions", extras)
    return export_queryset_csv(request=request, queryset=qs, spec=spec)


@login_required
def export_vehicles_csv(request: HttpRequest) -> HttpResponse:
    Vehicle = _find_unique_model_by_classnames(["Vehicle"])  # type: ignore
    qs = _business_scoped_queryset(request, Vehicle).order_by(*_default_ordering(Vehicle))
    spec = _spec_for(Vehicle, "vehicles")
    return export_queryset_csv(request=request, queryset=qs, spec=spec)


@login_required
def export_mileage_csv(request: HttpRequest) -> HttpResponse:
    # Support common class names
    Miles = _find_unique_model_by_classnames(["Miles", "Mileage", "MileageEntry", "MileageLog"])  # type: ignore
    qs = _business_scoped_queryset(request, Miles).order_by(*_default_ordering(Miles))

    extras = []
    for candidate in ("total_miles", "miles", "distance"):
        if hasattr(Miles, candidate):
            extras.append(col_fn(candidate, lambda m, c=candidate: getattr(m, c, "")))

    spec = _spec_for(Miles, "mileage", extras)
    return export_queryset_csv(request=request, queryset=qs, spec=spec)


@login_required
def export_contacts_csv(request: HttpRequest) -> HttpResponse:
    Contact = _find_unique_model_by_classnames(["Contact", "Client"])  # type: ignore
    qs = _business_scoped_queryset(request, Contact).order_by(*_default_ordering(Contact))
    spec = _spec_for(Contact, "contacts")
    return export_queryset_csv(request=request, queryset=qs, spec=spec)


@login_required
def export_jobs_csv(request: HttpRequest) -> HttpResponse:
    Job = _find_unique_model_by_classnames(["Job"])  # type: ignore
    qs = _business_scoped_queryset(request, Job).order_by(*_default_ordering(Job))
    spec = _spec_for(Job, "jobs")
    return export_queryset_csv(request=request, queryset=qs, spec=spec)


@login_required
def export_payees_csv(request: HttpRequest) -> HttpResponse:
    Payee = _find_unique_model_by_classnames(["Payee"])  # type: ignore
    qs = _business_scoped_queryset(request, Payee).order_by(*_default_ordering(Payee))
    spec = _spec_for(Payee, "payees")
    return export_queryset_csv(request=request, queryset=qs, spec=spec)


@login_required
def export_teams_csv(request: HttpRequest) -> HttpResponse:
    Team = _find_unique_model_by_classnames(["Team"])  # type: ignore
    qs = _business_scoped_queryset(request, Team).order_by(*_default_ordering(Team))
    spec = _spec_for(Team, "teams")
    return export_queryset_csv(request=request, queryset=qs, spec=spec)

@login_required
def export_assets_csv(request: HttpRequest) -> HttpResponse:
    Asset = _find_unique_model_by_classnames(["Asset"])  # type: ignore
    qs = _business_scoped_queryset(request, Asset).order_by(*_default_ordering(Asset))
    spec = _spec_for(Asset, "assets")
    return export_queryset_csv(request=request, queryset=qs, spec=spec)

