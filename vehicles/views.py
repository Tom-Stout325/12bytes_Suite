
from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Max, Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView

from vehicles.forms import QuickMileageForm, VehicleForm, VehicleMilesForm, VehicleYearForm
from vehicles.models import Vehicle, VehicleMiles, VehicleYear
from vehicles.queries import get_yearly_mileage_summary

ZERO_TENTH = Decimal("0.0")
ZERO_CENTS = Decimal("0.00")


def _parse_year(value: str | None) -> int:
    current = timezone.localdate().year
    if not value:
        return current
    try:
        year = int(value)
    except (TypeError, ValueError):
        return current
    if year < 2000 or year > current + 1:
        return current
    return year


def _year_choices(min_year: int = 2023) -> list[int]:
    current = timezone.localdate().year
    return list(range(current, min_year - 1, -1))


def _month_series_for_business(*, business, year: int, vehicle: Vehicle | None = None):
    labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    series = [0.0] * 12
    qs = VehicleMiles.objects.filter(business=business, date__year=year, mileage_type=VehicleMiles.MileageType.BUSINESS)
    if vehicle is not None:
        qs = qs.filter(vehicle=vehicle)
    rows = qs.values_list("date__month").annotate(total=Sum("total"))
    for month, total in rows:
        if month:
            series[int(month) - 1] = float(total or 0)
    return labels, series


def _expense_transactions_for_vehicle(*, business, vehicle: Vehicle, year: int):
    Transaction = None
    for app_label in ("ledger", "money"):
        try:
            Transaction = apps.get_model(app_label, "Transaction")
            break
        except LookupError:
            continue
    if Transaction is None:
        return []
    qs = Transaction.objects.filter(business=business, vehicle=vehicle, date__year=year)
    if hasattr(Transaction, "TransactionType"):
        qs = qs.filter(trans_type=Transaction.TransactionType.EXPENSE)
    return list(qs.select_related("subcategory", "category", "contact", "job").order_by("-date", "-id")[:50])


def _snapshot_for_vehicle_year(vy: VehicleYear | None):
    if not vy:
        return None
    return {
        "object": vy,
        "summary": get_yearly_mileage_summary(business=vy.business, vehicle_id=vy.vehicle_id, year=vy.year),
        "alerts": vy.missing_data_flags,
    }


def _build_dashboard_cards(*, business, year: int):
    cards = []
    alerts: list[dict] = []
    vehicles = Vehicle.objects.filter(business=business).order_by("-is_active", "sort_order", "label")
    year_map = {
        vy.vehicle_id: vy
        for vy in VehicleYear.objects.filter(business=business, year=year).select_related("vehicle")
    }
    for vehicle in vehicles:
        vy = year_map.get(vehicle.id)
        if vy:
            summary = get_yearly_mileage_summary(business=business, vehicle_id=vehicle.id, year=year)
            cards.append({"vehicle": vehicle, "vehicle_year": vy, "summary": summary})
            for flag in vy.missing_data_flags:
                alerts.append({"vehicle": vehicle, "message": flag})
        else:
            cards.append({"vehicle": vehicle, "vehicle_year": None, "summary": None})
            if vehicle.is_active:
                alerts.append({"vehicle": vehicle, "message": f"Missing {year} annual vehicle record"})
    return cards, alerts


class VehiclesHomeView(LoginRequiredMixin, TemplateView):
    template_name = "vehicles/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        business = self.request.business
        year = _parse_year(self.request.GET.get("year"))
        cards, alerts = _build_dashboard_cards(business=business, year=year)

        total_business = sum((card["summary"].business_miles for card in cards if card["summary"]), ZERO_TENTH)
        total_other = sum((card["summary"].other_miles or ZERO_TENTH for card in cards if card["summary"]), ZERO_TENTH)
        total_miles = sum((card["summary"].total_miles or ZERO_TENTH for card in cards if card["summary"]), ZERO_TENTH)
        total_deduction = sum((card["summary"].deduction_amount or ZERO_CENTS for card in cards if card["summary"]), ZERO_CENTS)
        total_actual_expenses = sum((card["summary"].actual_expenses_total for card in cards if card["summary"]), ZERO_CENTS)

        labels, series = _month_series_for_business(business=business, year=year)
        ctx.update({
            "year": year,
            "year_choices": _year_choices(),
            "vehicles": Vehicle.objects.filter(business=business).order_by("-is_active", "sort_order", "label"),
            "vehicle_count": Vehicle.objects.filter(business=business).count(),
            "active_vehicle_count": Vehicle.objects.filter(business=business, is_active=True).count(),
            "vehicle_cards": cards,
            "alerts": alerts[:8],
            "ytd_business_miles": total_business,
            "ytd_other_miles": total_other,
            "ytd_total_miles": total_miles,
            "ytd_deduction_total": total_deduction,
            "ytd_actual_expenses_total": total_actual_expenses,
            "recent_miles": VehicleMiles.objects.filter(business=business, date__year=year).select_related("vehicle", "job", "invoice").order_by("-date", "-id")[:8],
            "quick_mileage_form": QuickMileageForm(business=business),
            "chart_labels_json": json.dumps(labels),
            "chart_values_json": json.dumps(series),
        })
        return ctx


class VehicleListView(LoginRequiredMixin, ListView):
    model = Vehicle
    template_name = "vehicles/vehicle_list.html"
    context_object_name = "vehicles"

    def get_queryset(self):
        return Vehicle.objects.filter(business=self.request.business).order_by("-is_active", "sort_order", "label")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        year = _parse_year(self.request.GET.get("year"))
        ctx["year"] = year
        ctx["year_choices"] = _year_choices()
        year_map = {vy.vehicle_id: vy for vy in VehicleYear.objects.filter(business=self.request.business, year=year)}
        ctx["vehicle_rows"] = [
            {
                "vehicle": vehicle,
                "vehicle_year": year_map.get(vehicle.id),
                "summary": get_yearly_mileage_summary(business=self.request.business, vehicle_id=vehicle.id, year=year) if year_map.get(vehicle.id) else None,
            }
            for vehicle in ctx["vehicles"]
        ]
        return ctx


class VehicleDetailView(LoginRequiredMixin, DetailView):
    model = Vehicle
    template_name = "vehicles/vehicle_detail.html"
    context_object_name = "vehicle"

    def get_queryset(self):
        return Vehicle.objects.filter(business=self.request.business)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        business = self.request.business
        vehicle = self.object
        year = _parse_year(self.request.GET.get("year"))
        vy = VehicleYear.objects.filter(business=business, vehicle=vehicle, year=year).first()
        summary = get_yearly_mileage_summary(business=business, vehicle_id=vehicle.id, year=year) if vy else None
        miles_qs = VehicleMiles.objects.filter(business=business, vehicle=vehicle, date__year=year).select_related("job", "invoice").order_by("-date", "-id")
        labels, series = _month_series_for_business(business=business, year=year, vehicle=vehicle)

        loan = getattr(vehicle, "loan", None)
        loan_payments_qs = loan.payments.filter(payment_date__year=year).order_by("payment_date", "payment_number") if loan else None
        latest_end = miles_qs.aggregate(v=Max("end"))["v"]
        ctx.update({
            "year": year,
            "year_choices": _year_choices(),
            "vehicle_year": vy,
            "summary": summary,
            "alerts": vy.missing_data_flags if vy else [f"Missing {year} annual vehicle record"],
            "miles_entries": miles_qs[:25],
            "all_miles_count": miles_qs.count(),
            "odometer_today": latest_end,
            "transactions": _expense_transactions_for_vehicle(business=business, vehicle=vehicle, year=year),
            "quick_mileage_form": QuickMileageForm(business=business, initial={"vehicle": vehicle.id}),
            "loan": loan,
            "loan_payments": list(loan_payments_qs[:24]) if loan_payments_qs is not None else [],
            "loan_payments_count": loan_payments_qs.count() if loan_payments_qs is not None else 0,
            "chart_labels_json": json.dumps(labels),
            "chart_values_json": json.dumps(series),
        })
        return ctx


class VehicleCreateView(LoginRequiredMixin, CreateView):
    model = Vehicle
    form_class = VehicleForm
    template_name = "vehicles/vehicle_form.html"
    success_url = reverse_lazy("vehicles:vehicle_list")

    def form_valid(self, form):
        self.object = form.save(commit=False)
        self.object.business = self.request.business
        self.object.save()
        messages.success(self.request, "Vehicle saved.")
        if form.cleaned_data.get("create_current_year_record"):
            current_year = timezone.localdate().year
            existing = VehicleYear.objects.filter(business=self.request.business, vehicle=self.object, year=current_year).first()
            if existing:
                messages.info(self.request, f"{current_year} annual record already exists. You can edit it below.")
                return redirect("vehicles:vehicle_year_edit", pk=existing.pk)
            return redirect(f"{reverse('vehicles:vehicle_year_add')}?vehicle={self.object.pk}&year={current_year}&next=detail")
        return redirect(self.get_success_url())


class VehicleUpdateView(LoginRequiredMixin, UpdateView):
    model = Vehicle
    form_class = VehicleForm
    template_name = "vehicles/vehicle_form.html"
    success_url = reverse_lazy("vehicles:vehicle_list")

    def get_queryset(self):
        return Vehicle.objects.filter(business=self.request.business)

    def form_valid(self, form):
        form.instance.business = self.request.business
        messages.success(self.request, "Vehicle updated.")
        return super().form_valid(form)


class VehicleDeleteView(LoginRequiredMixin, DeleteView):
    model = Vehicle
    template_name = "vehicles/vehicle_confirm_delete.html"
    success_url = reverse_lazy("vehicles:vehicle_list")

    def get_queryset(self):
        return Vehicle.objects.filter(business=self.request.business)


@login_required
@require_POST
def vehicle_archive(request: HttpRequest, pk: int) -> HttpResponse:
    vehicle = get_object_or_404(Vehicle, pk=pk, business=request.business)
    vehicle.is_active = False
    vehicle.save(update_fields=["is_active"])
    messages.success(request, f"Archived: {vehicle.label}")
    return redirect(request.POST.get("next") or "vehicles:vehicle_list")


@login_required
@require_POST
def vehicle_unarchive(request: HttpRequest, pk: int) -> HttpResponse:
    vehicle = get_object_or_404(Vehicle, pk=pk, business=request.business)
    vehicle.is_active = True
    vehicle.save(update_fields=["is_active"])
    messages.success(request, f"Unarchived: {vehicle.label}")
    return redirect(request.POST.get("next") or "vehicles:vehicle_list")


class VehicleYearListView(LoginRequiredMixin, ListView):
    model = VehicleYear
    template_name = "vehicles/vehicle_year_list.html"
    context_object_name = "vehicle_years"
    paginate_by = 50

    def get_queryset(self):
        year = _parse_year(self.request.GET.get("year"))
        return VehicleYear.objects.filter(business=self.request.business, year=year).select_related("vehicle").order_by("vehicle__label")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        year = _parse_year(self.request.GET.get("year"))
        rows = []
        for r in ctx["vehicle_years"]:
            rows.append({"object": r, "summary": get_yearly_mileage_summary(business=self.request.business, vehicle_id=r.vehicle_id, year=r.year)})
        ctx.update({"year": year, "year_choices": _year_choices(), "rows": rows})
        return ctx


class VehicleYearCreateView(LoginRequiredMixin, CreateView):
    model = VehicleYear
    form_class = VehicleYearForm
    template_name = "vehicles/vehicle_year_form.html"
    success_url = reverse_lazy("vehicles:vehicle_year_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["business"] = self.request.business
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        vehicle = self.request.GET.get("vehicle")
        year = self.request.GET.get("year")
        if vehicle:
            initial["vehicle"] = vehicle
        if year:
            initial["year"] = year
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["year"] = _parse_year(self.request.GET.get("year"))
        ctx["year_choices"] = _year_choices()
        ctx["vehicle_id"] = self.request.GET.get("vehicle") or ""
        ctx["next"] = self.request.GET.get("next") or ""
        return ctx

    def form_valid(self, form):
        form.instance.business = self.request.business
        self.object = form.save()
        messages.success(self.request, "Annual vehicle record saved.")
        next_target = self.request.GET.get("next")
        if next_target == "detail":
            return redirect(f"{reverse('vehicles:vehicle_detail', args=[self.object.vehicle_id])}?year={self.object.year}")
        return redirect(self.get_success_url() + f"?year={self.object.year}")


class VehicleYearUpdateView(LoginRequiredMixin, UpdateView):
    model = VehicleYear
    form_class = VehicleYearForm
    template_name = "vehicles/vehicle_year_form.html"
    success_url = reverse_lazy("vehicles:vehicle_year_list")

    def get_queryset(self):
        return VehicleYear.objects.filter(business=self.request.business).select_related("vehicle")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["business"] = self.request.business
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["year"] = getattr(self.object, "year", None) or _parse_year(self.request.GET.get("year"))
        ctx["year_choices"] = _year_choices()
        ctx["vehicle_id"] = getattr(self.object, "vehicle_id", "")
        ctx["next"] = self.request.GET.get("next") or ""
        return ctx

    def form_valid(self, form):
        form.instance.business = self.request.business
        messages.success(self.request, "Annual vehicle record updated.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("vehicles:vehicle_detail", args=[self.object.vehicle_id]) + f"?year={self.object.year}"


class VehicleYearDeleteView(LoginRequiredMixin, DeleteView):
    model = VehicleYear
    template_name = "vehicles/vehicle_year_confirm_delete.html"
    success_url = reverse_lazy("vehicles:vehicle_year_list")

    def get_queryset(self):
        return VehicleYear.objects.filter(business=self.request.business)


class VehicleMilesListView(LoginRequiredMixin, ListView):
    model = VehicleMiles
    template_name = "vehicles/vehicle_miles_list.html"
    context_object_name = "miles_entries"
    paginate_by = 50

    def get_queryset(self):
        year = _parse_year(self.request.GET.get("year"))
        qs = VehicleMiles.objects.filter(business=self.request.business, date__year=year).select_related("vehicle", "job", "invoice").order_by("-date", "-id")
        vehicle_id = self.request.GET.get("vehicle")
        if vehicle_id:
            try:
                qs = qs.filter(vehicle_id=int(vehicle_id))
            except (TypeError, ValueError):
                pass
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        year = _parse_year(self.request.GET.get("year"))
        vehicle_filter = self.request.GET.get("vehicle") or ""
        # Use a fresh, unsliced queryset for totals. The paginated object_list is
        # sliced by Django before this method runs, and sliced querysets cannot
        # be filtered again. Keeping totals separate also makes the KPI cards
        # reflect the full filtered result set, not just the current page.
        totals_qs = self.get_queryset()
        total_logged = totals_qs.aggregate(total=Sum("total"))["total"] or ZERO_TENTH
        total_business = totals_qs.filter(mileage_type=VehicleMiles.MileageType.BUSINESS).aggregate(total=Sum("total"))["total"] or ZERO_TENTH
        ctx.update({
            "year": year,
            "year_choices": _year_choices(),
            "vehicles": Vehicle.objects.filter(business=self.request.business).order_by("label"),
            "vehicle_filter": vehicle_filter,
            "logged_total": total_logged,
            "business_total": total_business,
            "other_total": (Decimal(str(total_logged)) - Decimal(str(total_business))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP),
        })
        return ctx


class VehicleMilesCreateView(LoginRequiredMixin, CreateView):
    model = VehicleMiles
    form_class = VehicleMilesForm
    template_name = "vehicles/vehicle_miles_form.html"
    success_url = reverse_lazy("vehicles:vehicle_miles_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["business"] = self.request.business
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        for key in ("invoice", "job", "vehicle"):
            value = self.request.GET.get(key)
            if value:
                initial[key] = value
        return initial

    def form_valid(self, form):
        form.instance.business = self.request.business
        self.object = form.save()
        messages.success(self.request, "Mileage entry saved.")
        return redirect(reverse("vehicles:vehicle_detail", args=[self.object.vehicle_id]) + f"?year={self.object.date.year}")


class VehicleMilesUpdateView(LoginRequiredMixin, UpdateView):
    model = VehicleMiles
    form_class = VehicleMilesForm
    template_name = "vehicles/vehicle_miles_form.html"
    success_url = reverse_lazy("vehicles:vehicle_miles_list")

    def get_queryset(self):
        return VehicleMiles.objects.filter(business=self.request.business).select_related("vehicle", "job", "invoice")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["business"] = self.request.business
        return kwargs

    def form_valid(self, form):
        form.instance.business = self.request.business
        messages.success(self.request, "Mileage entry updated.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("vehicles:vehicle_detail", args=[self.object.vehicle_id]) + f"?year={self.object.date.year}"


class VehicleMilesDeleteView(LoginRequiredMixin, DeleteView):
    model = VehicleMiles
    template_name = "vehicles/vehicle_miles_confirm_delete.html"
    success_url = reverse_lazy("vehicles:vehicle_miles_list")

    def get_queryset(self):
        return VehicleMiles.objects.filter(business=self.request.business)


@login_required
@require_POST
def quick_mileage_add(request: HttpRequest) -> HttpResponse:
    form = QuickMileageForm(request.POST, business=request.business)
    if form.is_valid():
        entry = form.save(commit=False)
        entry.business = request.business
        entry.mileage_type = VehicleMiles.MileageType.BUSINESS
        entry.save()
        messages.success(request, "Business mileage added.")
        return redirect(request.POST.get("next") or reverse("vehicles:vehicle_detail", args=[entry.vehicle_id]) + f"?year={entry.date.year}")
    messages.error(request, "Please correct the quick mileage form.")
    return render(request, "vehicles/quick_mileage_error.html", {"form": form}, status=400)
