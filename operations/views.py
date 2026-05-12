from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.staticfiles.storage import staticfiles_storage
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import DeleteView, DetailView, FormView, TemplateView, UpdateView

from ledger.models import Job

from .forms import OpsPlanApprovalForm, OpsPlanForm
from .models import OpsPlan

try:
    from weasyprint import CSS, HTML
    WEASYPRINT_AVAILABLE = True
except Exception:
    WEASYPRINT_AVAILABLE = False


def _current_business(request):
    return getattr(request, "business", None)


def _job_qs_for_request(request):
    qs = Job.objects.select_related("business", "client")
    business = _current_business(request)
    if request.user.is_superuser and business is None:
        return qs
    if business is None:
        return qs.none()
    return qs.filter(business=business)


def _opsplan_qs_for_request(request):
    qs = OpsPlan.objects.select_related("business", "job", "client", "created_by", "updated_by")
    business = _current_business(request)
    if request.user.is_superuser and business is None:
        return qs
    if business is None:
        return qs.none()
    return qs.filter(business=business)


def _get_plan_or_404(request, pk: int) -> OpsPlan:
    return get_object_or_404(_opsplan_qs_for_request(request), pk=pk)


@login_required
def ops_plan_create_router(request):
    job_id = request.GET.get("job_id") or request.GET.get("event_id") or ""
    year = request.GET.get("year") or ""

    if not job_id.isdigit():
        messages.error(request, "Please select a job.")
        return redirect("operations:ops_plan_index")

    year_qs = f"?year={int(year)}" if year and str(year).isdigit() else ""
    return redirect(f"{reverse('operations:ops_plan_create', kwargs={'job_id': int(job_id)})}{year_qs}")


class OpsPlanCreateView(LoginRequiredMixin, View):
    def get(self, request, job_id: int):
        business = _current_business(request)
        if business is None:
            messages.error(request, "Select a business before creating an Ops Plan.")
            return redirect("operations:ops_plan_index")

        job = get_object_or_404(_job_qs_for_request(request), pk=job_id)
        try:
            plan_year = int(request.GET.get("year") or getattr(job, "job_year", None) or timezone.now().year)
        except (TypeError, ValueError):
            plan_year = timezone.now().year

        try:
            with transaction.atomic():
                plan = OpsPlan.objects.create(
                    business=business,
                    job=job,
                    event_name=getattr(job, "label", None) or str(job),
                    plan_year=plan_year,
                    status=OpsPlan.DRAFT,
                    created_by=request.user,
                    updated_by=request.user,
                )
            messages.success(request, "Draft Ops Plan created.")
        except IntegrityError:
            plan = _opsplan_qs_for_request(request).filter(job=job, plan_year=plan_year).first()
            if plan:
                messages.info(request, "An Ops Plan for this job and year already exists. Redirected to that plan.")
            else:
                messages.error(request, "Could not create Ops Plan.")
                return redirect("operations:ops_plan_index")

        return redirect("operations:ops_plan_update", pk=plan.pk)


class OpsPlanUpdateView(LoginRequiredMixin, UpdateView):
    model = OpsPlan
    form_class = OpsPlanForm
    template_name = "operations/ops_plan_create.html"
    context_object_name = "plan"

    def get_queryset(self):
        return _opsplan_qs_for_request(self.request)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["job"] = getattr(self.object, "job", None)
        kwargs["business"] = _current_business(self.request)
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["statuses"] = [label for (_value, label) in OpsPlan.STATUS_CHOICES]
        return ctx

    def form_valid(self, form):
        form.instance.business = _current_business(self.request)
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Ops Plan updated.")
        return super().form_valid(form)

    def get_success_url(self):
        return self.object.get_absolute_url()


class OpsPlanDeleteView(LoginRequiredMixin, DeleteView):
    model = OpsPlan
    template_name = "operations/ops_plan_confirm_delete.html"
    context_object_name = "plan"

    def get_queryset(self):
        return _opsplan_qs_for_request(self.request)

    def get_success_url(self):
        messages.success(self.request, "Ops Plan deleted.")
        return reverse("operations:ops_plan_index")


@login_required
def ops_plan_pdf_view(request, pk: int):
    plan = _get_plan_or_404(request, pk)

    if not WEASYPRINT_AVAILABLE:
        return HttpResponse("PDF generation requires WeasyPrint.", status=501, content_type="text/plain")

    try:
        logo_fs_path = staticfiles_storage.path("images/logo2.png")
        logo_url = Path(logo_fs_path).as_uri()
    except Exception:
        logo_url = request.build_absolute_uri(static("images/logo2.png"))

    brand_name = getattr(settings, "APP_NAME", None) or "12bytes Suite"
    html = render(
        request,
        "operations/ops_plan_pdf.html",
        {"plan": plan, "generated_at": timezone.now(), "logo_url": logo_url, "brand_name": brand_name},
    ).content.decode("utf-8")

    base_url = Path(getattr(settings, "STATIC_ROOT", None) or settings.BASE_DIR).as_uri()
    pdf = HTML(string=html, base_url=base_url).write_pdf(
        stylesheets=[CSS(string="@page { size: A4; margin: 18mm 16mm; }")]
    )

    filename = f"ops-plan-{plan.id}-{plan.plan_year}.pdf"
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{filename}"'
    return resp


class OpsPlanIndexView(LoginRequiredMixin, TemplateView):
    template_name = "operations/ops_plan_index.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["plans"] = _opsplan_qs_for_request(self.request).order_by("-updated_at")[:50]
        ctx["jobs"] = _job_qs_for_request(self.request).filter(is_active=True).order_by("-job_year", "label")[:200]
        ctx["events"] = ctx["jobs"]
        ctx["current_year"] = timezone.now().year
        ctx["statuses"] = [OpsPlan.DRAFT, OpsPlan.IN_REVIEW, OpsPlan.APPROVED, OpsPlan.ARCHIVED]
        return ctx


class OpsPlanDetailView(LoginRequiredMixin, DetailView):
    model = OpsPlan
    template_name = "operations/ops_plan_detail.html"
    context_object_name = "plan"

    def get_queryset(self):
        return _opsplan_qs_for_request(self.request)


@require_POST
@login_required
def ops_plan_submit_view(request, pk: int):
    plan = _get_plan_or_404(request, pk)
    if plan.status != OpsPlan.DRAFT:
        messages.warning(request, f"Only Draft plans can be submitted (current status: {plan.status}).")
        return redirect(plan.get_absolute_url())

    plan.status = OpsPlan.IN_REVIEW
    plan.updated_by = request.user
    plan.save(update_fields=["status", "updated_by", "updated_at"])
    messages.success(request, "Ops Plan submitted for review.")
    return redirect(plan.get_absolute_url())


@require_POST
@staff_member_required
def ops_plan_approve_view(request, pk: int):
    plan = _get_plan_or_404(request, pk)
    if plan.status != OpsPlan.IN_REVIEW:
        messages.warning(request, f"Only plans In Review can be approved (current status: {plan.status}).")
        return redirect(plan.get_absolute_url())

    plan.status = OpsPlan.APPROVED
    plan.updated_by = request.user
    plan.save(update_fields=["status", "updated_by", "updated_at"])
    messages.success(request, "Ops Plan approved.")
    return redirect(plan.get_absolute_url())


@require_POST
@staff_member_required
def ops_plan_archive_view(request, pk: int):
    plan = _get_plan_or_404(request, pk)
    if plan.status == OpsPlan.ARCHIVED:
        messages.info(request, "This Ops Plan is already archived.")
        return redirect(plan.get_absolute_url())

    plan.status = OpsPlan.ARCHIVED
    plan.updated_by = request.user
    plan.save(update_fields=["status", "updated_by", "updated_at"])
    messages.success(request, "Ops Plan archived.")
    return redirect(plan.get_absolute_url())


@require_POST
@login_required
def change_ops_plan_status(request, pk: int, new_status: str):
    plan = _get_plan_or_404(request, pk)
    valid_statuses = [OpsPlan.DRAFT, OpsPlan.IN_REVIEW, OpsPlan.APPROVED, OpsPlan.ARCHIVED]
    if new_status not in valid_statuses:
        messages.error(request, f"Invalid status '{new_status}'.")
        return redirect("operations:ops_plan_index")

    plan.status = new_status
    plan.updated_by = request.user
    plan.save(update_fields=["status", "updated_by", "updated_at"])
    messages.success(request, f"Ops Plan updated to {new_status}.")
    return redirect(plan.get_absolute_url())


class OpsPlanApprovalView(FormView):
    template_name = "operations/ops_plan_approve.html"
    form_class = OpsPlanApprovalForm

    def dispatch(self, request, *args, **kwargs):
        self.plan = get_object_or_404(OpsPlan, pk=kwargs["pk"], approval_token=kwargs["token"])
        if self.plan.approved_at:
            return render(request, "operations/ops_plan_already_approved.html", {"plan": self.plan})
        if self.plan.approval_token_expires_at and timezone.now() > self.plan.approval_token_expires_at:
            return render(request, "operations/ops_plan_expired.html", {"plan": self.plan})
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["plan"] = self.plan
        return ctx

    def form_valid(self, form):
        self.plan.approved_name = form.cleaned_data["full_name"]
        self.plan.approved_at = timezone.now()
        self.plan.approved_ip = self.request.META.get("REMOTE_ADDR", "")
        self.plan.approved_user_agent = self.request.META.get("HTTP_USER_AGENT", "")
        self.plan.approved_notes_snapshot = self.plan.notes
        self.plan.compute_attestation_hash()
        self.plan.status = OpsPlan.APPROVED
        self.plan.approval_token = None
        self.plan.save()
        return render(self.request, "operations/ops_plan_approved_success.html", {"plan": self.plan})
