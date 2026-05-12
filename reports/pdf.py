from __future__ import annotations

from dataclasses import dataclass

from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string


@dataclass(frozen=True)
class PdfResult:
    response: HttpResponse


def render_pdf_from_template(*, request: HttpRequest, template_name: str, context: dict, filename: str = "report.pdf", download: bool = False) -> PdfResult:
    """Render a WeasyPrint PDF from a Django template.

    - Uses request.build_absolute_uri('/') as base_url so /static and /media URLs resolve.
    - If WeasyPrint isn't installed, raises RuntimeError with a clear message.
    """
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("WeasyPrint is not installed. Add weasyprint to requirements to enable PDF exports.") from exc

    html_str = render_to_string(template_name, context=context, request=request)
    base_url = request.build_absolute_uri("/")

    pdf_bytes = HTML(string=html_str, base_url=base_url).write_pdf()

    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    disp = "attachment" if download else "inline"
    resp["Content-Disposition"] = f'{disp}; filename="{filename}"'
    return PdfResult(response=resp)
