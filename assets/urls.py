from __future__ import annotations

from django.urls import path

from assets import views

app_name = "assets"

urlpatterns = [
    path("", views.AssetListView.as_view(), name="asset_list"),
    path("add/", views.AssetCreateView.as_view(), name="asset_create"),
    path("types/", views.AssetTypeListView.as_view(), name="asset_type_list"),
    path("types/add/", views.AssetTypeCreateView.as_view(), name="asset_type_create"),
    path("types/<int:pk>/edit/", views.AssetTypeUpdateView.as_view(), name="asset_type_update"),
    path("types/<int:pk>/delete/", views.AssetTypeDeleteView.as_view(), name="asset_type_delete"),
    path("<int:pk>/", views.AssetDetailView.as_view(), name="asset_detail"),
    path("<int:pk>/edit/", views.AssetUpdateView.as_view(), name="asset_update"),
    path("<int:pk>/delete/", views.AssetDeleteView.as_view(), name="asset_delete"),
]
