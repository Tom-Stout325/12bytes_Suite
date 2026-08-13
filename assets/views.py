from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Sum
from django.db.models.deletion import ProtectedError
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from assets.forms import AssetForm, AssetTypeForm
from assets.models import Asset, AssetType


class AssetListView(LoginRequiredMixin, ListView):
    model = Asset
    template_name = "assets/assets/asset_list.html"
    context_object_name = "assets"

    def get_queryset(self):
        qs = Asset.objects.filter(business=self.request.business).select_related("asset_type")
        if self.request.GET.get("show") != "all":
            qs = qs.filter(is_active=True)
        asset_type = self.request.GET.get("type") or ""
        if asset_type:
            qs = qs.filter(asset_type_id=asset_type)
        return qs.order_by("-purchase_date", "name")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["type_filter"] = self.request.GET.get("type") or ""
        ctx["show_all"] = self.request.GET.get("show") == "all"
        ctx["type_choices"] = AssetType.objects.filter(business=self.request.business).order_by("sort_order", "name")
        return ctx


class AssetDetailView(LoginRequiredMixin, DetailView):
    model = Asset
    template_name = "assets/assets/asset_detail.html"
    context_object_name = "asset"

    def get_queryset(self):
        return Asset.objects.filter(business=self.request.business).select_related(
            "asset_type", "aircraft_model", "drone_model", "drone_model__battery_family"
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if not self.object.drone_model_id and not self.object.aircraft_model_id:
            return ctx
        from flightlogs.models import FlightLog

        if self.object.drone_model_id:
            variant_flights = FlightLog.objects.filter(
                business=self.request.business,
                drone_model=self.object.drone_model,
            )
            ctx["catalog_model"] = self.object.drone_model
        else:
            variant_flights = FlightLog.objects.filter(
                business=self.request.business,
                aircraft_model=self.object.aircraft_model,
            )
            ctx["catalog_model"] = self.object.aircraft_model
        total = variant_flights.aggregate(total=Sum("air_time"))["total"]
        ctx["flight_summary"] = {
            "flights": variant_flights.count(),
            "hours": total.total_seconds() / 3600 if total else 0.0,
            "last_flight": variant_flights.order_by("-flight_date").values_list("flight_date", flat=True).first(),
        }
        family = self.object.drone_model.battery_family if self.object.drone_model_id else None
        if family:
            from drones.batteries import battery_family_flights
            battery_flights = battery_family_flights(
                business=self.request.business,
                drone_model=self.object.drone_model,
            )
        else:
            battery_flights = variant_flights
        batteries = {}
        for flight in battery_flights.only(
            "battery_serial_internal", "battery_serial_printed", "battery_name",
            "battery_cycle_count", "air_time", "flight_date",
        ):
            identifier = (flight.battery_serial_internal or flight.battery_serial_printed).strip()
            if not identifier:
                continue
            item = batteries.setdefault(identifier, {
                "identifier": identifier, "name": "", "cycle_count": None,
                "flights": 0, "hours": 0.0, "last_used": None,
            })
            item["flights"] += 1
            if flight.air_time:
                item["hours"] += flight.air_time.total_seconds() / 3600
            if flight.battery_cycle_count is not None:
                item["cycle_count"] = max(item["cycle_count"] or 0, flight.battery_cycle_count)
            if not item["name"] and flight.battery_name:
                item["name"] = flight.battery_name
            if item["last_used"] is None or flight.flight_date > item["last_used"]:
                item["last_used"] = flight.flight_date
        ctx["battery_summary"] = sorted(
            batteries.values(), key=lambda item: (item["last_used"], item["identifier"]), reverse=True
        )
        return ctx


class AssetCreateView(LoginRequiredMixin, CreateView):
    model = Asset
    form_class = AssetForm
    template_name = "assets/assets/asset_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["business"] = self.request.business
        return kwargs

    def form_valid(self, form):
        form.instance.business = self.request.business
        resp = super().form_valid(form)
        messages.success(self.request, "Equipment created.")
        return resp

    def get_success_url(self):
        return reverse_lazy("assets:asset_detail", kwargs={"pk": self.object.pk})


class AssetUpdateView(LoginRequiredMixin, UpdateView):
    model = Asset
    form_class = AssetForm
    template_name = "assets/assets/asset_form.html"

    def get_queryset(self):
        return Asset.objects.filter(business=self.request.business).select_related("asset_type")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["business"] = self.request.business
        return kwargs

    def form_valid(self, form):
        resp = super().form_valid(form)
        messages.success(self.request, "Equipment updated.")
        return resp

    def get_success_url(self):
        return reverse_lazy("assets:asset_detail", kwargs={"pk": self.object.pk})


class AssetDeleteView(LoginRequiredMixin, DeleteView):
    model = Asset
    template_name = "assets/assets/asset_confirm_delete.html"

    def get_queryset(self):
        return Asset.objects.filter(business=self.request.business)

    def delete(self, request, *args, **kwargs):
        messages.success(self.request, "Equipment deleted.")
        return super().delete(request, *args, **kwargs)

    def get_success_url(self):
        return reverse_lazy("assets:asset_list")


class AssetTypeListView(LoginRequiredMixin, ListView):
    model = AssetType
    template_name = "assets/types/asset_type_list.html"
    context_object_name = "asset_types"

    def get_queryset(self):
        return AssetType.objects.filter(business=self.request.business).order_by("sort_order", "name")


class AssetTypeCreateView(LoginRequiredMixin, CreateView):
    model = AssetType
    form_class = AssetTypeForm
    template_name = "assets/types/asset_type_form.html"

    def form_valid(self, form):
        form.instance.business = self.request.business
        resp = super().form_valid(form)
        messages.success(self.request, "Equipment type created.")
        return resp

    def get_success_url(self):
        return reverse_lazy("assets:asset_type_list")


class AssetTypeUpdateView(LoginRequiredMixin, UpdateView):
    model = AssetType
    form_class = AssetTypeForm
    template_name = "assets/types/asset_type_form.html"

    def get_queryset(self):
        return AssetType.objects.filter(business=self.request.business)

    def form_valid(self, form):
        resp = super().form_valid(form)
        messages.success(self.request, "Equipment type updated.")
        return resp

    def get_success_url(self):
        return reverse_lazy("assets:asset_type_list")


class AssetTypeDeleteView(LoginRequiredMixin, DeleteView):
    model = AssetType
    template_name = "assets/types/asset_type_confirm_delete.html"

    def get_queryset(self):
        return AssetType.objects.filter(business=self.request.business)

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        try:
            self.object.delete()
        except ProtectedError:
            messages.error(request, "This type is used by one or more equipment records. Mark it inactive instead.")
            return redirect("assets:asset_type_list")
        messages.success(self.request, "Equipment type deleted.")
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse_lazy("assets:asset_type_list")
