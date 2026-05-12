from __future__ import annotations

from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from .pdf import render_pdf_from_template
from .tax_queries import build_schedule_c_lines


def _year_choices() -> list[int]:
    today = date.today()
    start = 2023
    return list(range(start, today.year + 1))


def _selected_year(request: HttpRequest) -> int:
    today = date.today()
    try:
        return int(request.GET.get("year") or today.year)
    except (TypeError, ValueError):
        return today.year


def _date_range_for_year(year: int) -> tuple[date, date]:
    today = date.today()
    date_from = date(year, 1, 1)
    date_to = today if year == today.year else date(year, 12, 31)
    return date_from, date_to


def _company_context(request: HttpRequest) -> dict:
    business = getattr(request, "business", None)
    company_profile = getattr(business, "company_profile", None) if business else None

    company_name = ""
    if company_profile:
        company_name = getattr(company_profile, "company_name", "") or getattr(company_profile, "name", "") or ""
    if not company_name and business:
        company_name = getattr(business, "name", "") or ""

    return {
        "business": business,
        "company_profile": company_profile,
        "company_name": company_name,
    }


@login_required
def schedule_c_pdf_preview(request: HttpRequest) -> HttpResponse:
    return _schedule_c_pdf(request, download=False)


@login_required
def schedule_c_pdf_download(request: HttpRequest) -> HttpResponse:
    return _schedule_c_pdf(request, download=True)


def _schedule_c_pdf(request: HttpRequest, *, download: bool) -> HttpResponse:
    business = getattr(request, "business", None)
    year = _selected_year(request)
    date_from, date_to = _date_range_for_year(year)
    lines, grand_total = build_schedule_c_lines(business=business, date_from=date_from, date_to=date_to)

    ctx = {
        "selected_year": year,
        "lines": lines,
        "grand_total": grand_total,
        "meals_rate": 50,
        **_company_context(request),
    }

    filename = f"Operating_Expenses_{year}.pdf"
    return render_pdf_from_template(
        request=request,
        template_name="reports/pdf/schedule_c_pdf.html",
        context=ctx,
        filename=filename,
        download=download,
    )
