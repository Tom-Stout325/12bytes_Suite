from django.urls import path

from .views import (
    InvoiceDetailView,
    InvoiceListView,
    invoice_create,
    invoice_mark_paid,
    invoice_pdf_download,
    invoice_pdf_preview,
    invoice_revise,
    invoice_send,
    invoice_update,
    invoice_void,
)

from .exports import invoice_export_csv

app_name = "invoices"

urlpatterns = [
    path("export/csv/", invoice_export_csv, name="invoice_export_csv"),
    path("", InvoiceListView.as_view(), name="invoice_list"),
    path("new/", invoice_create, name="invoice_create"),
    path("<int:pk>/", InvoiceDetailView.as_view(), name="invoice_detail"),
    path("<int:pk>/edit/", invoice_update, name="invoice_update"),

    # Actions
    path("<int:pk>/send/", invoice_send, name="invoice_send"),
    path("<int:pk>/paid/", invoice_mark_paid, name="invoice_mark_paid"),
    path("<int:pk>/void/", invoice_void, name="invoice_void"),
    path("<int:pk>/revise/", invoice_revise, name="invoice_revise"),

    # PDF
    path("<int:pk>/pdf/", invoice_pdf_preview, name="invoice_pdf_preview"),
    path("<int:pk>/pdf/download/", invoice_pdf_download, name="invoice_pdf_download"),
]
