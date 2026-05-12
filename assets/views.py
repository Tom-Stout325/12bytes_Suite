from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
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
        asset_type = self.request.GET.get("type") or ""
        if asset_type:
            qs = qs.filter(asset_type_id=asset_type)
        return qs.order_by("-purchase_date", "name")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["type_filter"] = self.request.GET.get("type") or ""
        ctx["type_choices"] = AssetType.objects.filter(business=self.request.business).order_by("sort_order", "name")
        return ctx


class AssetDetailView(LoginRequiredMixin, DetailView):
    model = Asset
    template_name = "assets/assets/asset_detail.html"
    context_object_name = "asset"

    def get_queryset(self):
        return Asset.objects.filter(business=self.request.business).select_related("asset_type")


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
        return Asset.objects.filter(business=self.request.business).select_related("asset_type")

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
        messages.success(self.request, "Asset type created.")
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
        messages.success(self.request, "Asset type updated.")
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
            messages.error(request, "This type is used by one or more assets. Mark it inactive instead.")
            return redirect("assets:asset_type_list")
        messages.success(self.request, "Asset type deleted.")
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse_lazy("assets:asset_type_list")
