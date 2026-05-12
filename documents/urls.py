from __future__ import annotations

from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("", views.documents_portal, name="documents_portal"),
    path("incidents/", views.IncidentReportListView.as_view(), name="incident_reporting_system"),
    path("incidents/new/", views.IncidentReportCreateView.as_view(), name="incident_report_wizard"),
    path("incidents/<int:pk>/", views.IncidentReportDetailView.as_view(), name="incident_report_detail"),
    path("incidents/<int:pk>/pdf/", views.incident_report_pdf, name="incident_report_pdf"),
    path("sops/", views.SOPListView.as_view(), name="sop_list"),
    path("sops/upload/", views.SOPUploadView.as_view(), name="sop_upload"),
    path("sops/<int:pk>/delete/", views.SOPDeleteView.as_view(), name="delete_sop"),
    path("files/", views.GeneralDocumentListView.as_view(), name="general_document_list"),
    path("files/upload/", views.GeneralDocumentUploadView.as_view(), name="upload_general_document"),
    path("files/<int:pk>/delete/", views.GeneralDocumentDeleteView.as_view(), name="delete_document"),
]
