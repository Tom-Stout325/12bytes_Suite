from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from assets.forms import AssetForm
from assets.models import Asset


class AssetListView(LoginRequiredMixin, ListView):
    model = Asset
    template_name = "assets/assets/asset_list.html"
    context_object_name = "assets"

    def get_queryset(self):
        qs = Asset.objects.filter(business=self.request.business).select_related("vehicle")
        asset_type = self.request.GET.get("type") or ""
        if asset_type:
            qs = qs.filter(asset_type=asset_type)
        return qs.order_by("-purchase_date", "name")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["type_filter"] = self.request.GET.get("type") or ""
        ctx["type_choices"] = Asset.AssetType.choices
        return ctx


class AssetDetailView(LoginRequiredMixin, DetailView):
    model = Asset
    template_name = "assets/assets/asset_detail.html"
    context_object_name = "asset"

    def get_queryset(self):
        return Asset.objects.filter(business=self.request.business).select_related("vehicle")


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
        messages.success(self.request, "Asset created.")
        return resp

    def get_success_url(self):
        return reverse_lazy("assets:asset_detail", kwargs={"pk": self.object.pk})


class AssetUpdateView(LoginRequiredMixin, UpdateView):
    model = Asset
    form_class = AssetForm
    template_name = "assets/assets/asset_form.html"

    def get_queryset(self):
        return Asset.objects.filter(business=self.request.business)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["business"] = self.request.business
        return kwargs

    def form_valid(self, form):
        resp = super().form_valid(form)
        messages.success(self.request, "Asset updated.")
        return resp

    def get_success_url(self):
        return reverse_lazy("assets:asset_detail", kwargs={"pk": self.object.pk})


class AssetDeleteView(LoginRequiredMixin, DeleteView):
    model = Asset
    template_name = "assets/assets/asset_confirm_delete.html"

    def get_queryset(self):
        return Asset.objects.filter(business=self.request.business)

    def delete(self, request, *args, **kwargs):
        messages.success(self.request, "Asset deleted.")
        return super().delete(request, *args, **kwargs)

    def get_success_url(self):
        return reverse_lazy("assets:asset_list")
