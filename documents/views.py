from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView

from .forms import DroneIncidentReportForm, GeneralDocumentForm, SOPDocumentForm
from .models import DroneIncidentReport, GeneralDocument, SOPDocument

try:
    from weasyprint import CSS, HTML
    WEASYPRINT_AVAILABLE = True
except Exception:  # pragma: no cover - depends on system libraries in some environments
    WEASYPRINT_AVAILABLE = False


def business_required(request: HttpRequest):
    return getattr(request, "business", None)


@login_required
def documents_portal(request: HttpRequest) -> HttpResponse:
    business = business_required(request)
    context = {
        "current_page": "documents",
        "incident_count": DroneIncidentReport.objects.filter(business=business).count(),
        "sop_count": SOPDocument.objects.filter(business=business).count(),
        "general_count": GeneralDocument.objects.filter(business=business).count(),
    }
    return render(request, "documents/documents_portal.html", context)


class IncidentReportListView(LoginRequiredMixin, ListView):
    model = DroneIncidentReport
    template_name = "documents/incident_reporting_system.html"
    context_object_name = "incident_reports"
    paginate_by = 12

    def get_queryset(self):
        qs = DroneIncidentReport.objects.filter(business=self.request.business)
        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(reported_by__icontains=q)
                | Q(location__icontains=q)
                | Q(description__icontains=q)
                | Q(drone_model__icontains=q)
                | Q(registration__icontains=q)
            )
        return qs.order_by("-report_date", "-id")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["search_query"] = (self.request.GET.get("q") or "").strip()
        ctx["current_page"] = "incidents"
        return ctx


class IncidentReportCreateView(LoginRequiredMixin, CreateView):
    model = DroneIncidentReport
    form_class = DroneIncidentReportForm
    template_name = "documents/incident_report_form.html"

    def form_valid(self, form):
        form.instance.business = self.request.business
        response = super().form_valid(form)
        messages.success(self.request, "Incident report submitted.")
        return response

    def get_success_url(self):
        return reverse_lazy("documents:incident_report_detail", kwargs={"pk": self.object.pk})


class IncidentReportDetailView(LoginRequiredMixin, DetailView):
    model = DroneIncidentReport
    template_name = "documents/incident_report_detail.html"
    context_object_name = "report"

    def get_queryset(self):
        return DroneIncidentReport.objects.filter(business=self.request.business)


@login_required
def incident_report_pdf(request: HttpRequest, pk: int) -> HttpResponse:
    report = get_object_or_404(DroneIncidentReport, business=request.business, pk=pk)

    if not WEASYPRINT_AVAILABLE:
        messages.error(request, "PDF generation is not available because WeasyPrint is not installed or configured.")
        return redirect("documents:incident_report_detail", pk=pk)

    html_string = render_to_string("documents/incident_report_pdf.html", {"report": report}, request=request)
    pdf_content = HTML(string=html_string, base_url=request.build_absolute_uri("/")).write_pdf(
        stylesheets=[CSS(string="@page { size: Letter; margin: 0.5in; }")]
    )
    response = HttpResponse(pdf_content, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="incident_report_{report.pk}.pdf"'
    return response


class SOPListView(LoginRequiredMixin, ListView):
    model = SOPDocument
    template_name = "documents/sop_list.html"
    context_object_name = "sops"
    paginate_by = 15

    def get_queryset(self):
        qs = SOPDocument.objects.filter(business=self.request.business)
        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
        return qs.order_by("title")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["search_query"] = (self.request.GET.get("q") or "").strip()
        ctx["current_page"] = "sop"
        return ctx


class SOPUploadView(LoginRequiredMixin, CreateView):
    model = SOPDocument
    form_class = SOPDocumentForm
    template_name = "documents/sop_upload.html"
    success_url = reverse_lazy("documents:sop_list")

    def form_valid(self, form):
        form.instance.business = self.request.business
        messages.success(self.request, "SOP added successfully.")
        return super().form_valid(form)


class SOPDeleteView(LoginRequiredMixin, DeleteView):
    model = SOPDocument
    success_url = reverse_lazy("documents:sop_list")

    def get_queryset(self):
        return SOPDocument.objects.filter(business=self.request.business)

    def post(self, request, *args, **kwargs):
        messages.success(request, "SOP deleted successfully.")
        return super().post(request, *args, **kwargs)


class GeneralDocumentListView(LoginRequiredMixin, ListView):
    model = GeneralDocument
    template_name = "documents/general_list.html"
    context_object_name = "documents"
    paginate_by = 10

    def get_queryset(self):
        qs = GeneralDocument.objects.filter(business=self.request.business)
        q = (self.request.GET.get("q") or "").strip()
        category = (self.request.GET.get("category") or "").strip()
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
        if category:
            qs = qs.filter(category=category)
        return qs.order_by("title")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["search_query"] = (self.request.GET.get("q") or "").strip()
        ctx["selected_category"] = (self.request.GET.get("category") or "").strip()
        ctx["categories"] = GeneralDocument.Category.choices
        ctx["current_page"] = "documents"
        return ctx


class GeneralDocumentUploadView(LoginRequiredMixin, CreateView):
    model = GeneralDocument
    form_class = GeneralDocumentForm
    template_name = "documents/upload_general.html"
    success_url = reverse_lazy("documents:general_document_list")

    def form_valid(self, form):
        form.instance.business = self.request.business
        messages.success(self.request, "Document added successfully.")
        return super().form_valid(form)


class GeneralDocumentDeleteView(LoginRequiredMixin, DeleteView):
    model = GeneralDocument
    success_url = reverse_lazy("documents:general_document_list")

    def get_queryset(self):
        return GeneralDocument.objects.filter(business=self.request.business)

    def post(self, request, *args, **kwargs):
        messages.success(request, "Document deleted successfully.")
        return super().post(request, *args, **kwargs)
