from __future__ import annotations

from django.urls import path

from . import views_exports

app_name = "exports"

urlpatterns = [
    path("invoices/csv/", views_exports.export_invoices_csv, name="invoices_csv"),
    path("transactions/csv/", views_exports.export_transactions_csv, name="transactions_csv"),
    path("vehicles/csv/", views_exports.export_vehicles_csv, name="vehicles_csv"),
    path("mileage/csv/", views_exports.export_mileage_csv, name="mileage_csv"),
    path("contacts/csv/", views_exports.export_contacts_csv, name="contacts_csv"),
    path("jobs/csv/", views_exports.export_jobs_csv, name="jobs_csv"),
    path("payees/csv/", views_exports.export_payees_csv, name="payees_csv"),
    path("teams/csv/", views_exports.export_teams_csv, name="teams_csv"),
    path("assets/csv/", views_exports.export_assets_csv, name="assets_csv"),
]