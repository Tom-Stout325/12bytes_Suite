from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from project.views import home


def healthcheck(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", healthcheck, name="healthcheck"),
    path("", home, name="home"),
    path("help/", include(("helpcenter.urls", "helpcenter"), namespace="helpcenter")),
    path("accounts/", include("accounts.urls")),
    path("accounts/", include("allauth.urls")),
    path("dashboard/", include("dashboard.urls")),
    path("business/", include(("core.urls", "core"), namespace="core")),
    path("", include("ledger.urls")),  # ledger is mounted at root in your project
    path("reports/", include(("reports.urls", "reports"), namespace="reports")),
    path("vehicles/", include(("vehicles.urls", "vehicles"), namespace="vehicles")),
    path("assets/", include(("assets.urls", "assets"), namespace="assets")),
    path("documents/", include(("documents.urls", "documents"), namespace="documents")),
    path("operations/", include(("operations.urls", "operations"), namespace="operations")),
    path("invoices/", include(("invoices.urls", "invoices"), namespace="invoices")),
    path("contractors/", include(("contractor.urls", "contractor"), namespace="contractor")),
    
    path("exports/", include(("core.urls_exports", "exports"), namespace="exports")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
