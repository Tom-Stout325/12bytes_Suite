from __future__ import annotations

from django.urls import path

from . import views

app_name = "flightlogs"

urlpatterns = [
    path("", views.flightlog_list, name="flightlog_list"),
    path("portal/", views.drone_portal, name="drone_portal"),
    path("upload/", views.upload_flightlog_csv, name="flightlog_upload"),
    path("export/csv/", views.export_flightlogs_csv, name="export_flightlogs_csv"),
    path("map/", views.flight_map_view, name="flight_map"),
    path("map/embed/", views.flight_map_embed, name="flight_map_embed"),
    path("<int:pk>/", views.flightlog_detail, name="flightlog_detail"),
    path("<int:pk>/edit/", views.flightlog_edit, name="flightlog_edit"),
    path("<int:pk>/delete/", views.flightlog_delete, name="flightlog_delete"),
    path("<int:pk>/pdf/", views.flightlog_pdf, name="flightlog_pdf"),
]
