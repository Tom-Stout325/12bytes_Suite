from __future__ import annotations

import io
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict, Optional

from django.conf import settings
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas


@dataclass(frozen=True)
class DrawSpec:
    x: float
    y: float
    size: int = 10


def money_str(v: Optional[Decimal | float | int | str]) -> str:
    if v in (None, ""):
        return ""
    d = Decimal(str(v)).quantize(Decimal("0.01"))
    return f"{d:,.2f}"


def make_overlay_pdf(*, page_width: float, page_height: float, values: Dict[str, str], layout: Dict[str, DrawSpec]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))
    c.setTitle("1099-NEC Overlay")

    for key, spec in layout.items():
        val = (values.get(key) or "").strip()
        if not val:
            continue
        c.setFont("Helvetica", spec.size)

        if "\n" in val:
            lines = [ln.strip() for ln in val.splitlines() if ln.strip()]
            line_gap = spec.size + 1
            y = spec.y
            for ln in lines:
                c.drawString(spec.x, y, ln[:80])
                y -= line_gap
        else:
            c.drawString(spec.x, spec.y, val[:120])

    c.showPage()
    c.save()
    return buf.getvalue()


def render_1099_single_page(*, template_abs_path: Path, values: Dict[str, str], layout: Dict[str, DrawSpec]) -> bytes:
    reader = PdfReader(str(template_abs_path))
    if not reader.pages:
        raise ValueError("1099 template PDF has no pages.")
    page = reader.pages[0]
    w, h = float(page.mediabox.width), float(page.mediabox.height)

    overlay_pdf = make_overlay_pdf(page_width=w, page_height=h, values=values, layout=layout)
    overlay_page = PdfReader(io.BytesIO(overlay_pdf)).pages[0]

    page.merge_page(overlay_page)

    writer = PdfWriter()
    writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def default_layout() -> Dict[str, DrawSpec]:
    """Starter layout for the IRS 1099-NEC (single page template).

    Coords assume US Letter (612 x 792). These are reasonable starters;
    you can fine-tune them later.
    """
    return {
        "payer_block": DrawSpec(x=55, y=740, size=9),
        "payer_tin": DrawSpec(x=55, y=695, size=9),

        "recipient_name": DrawSpec(x=55, y=660, size=9),
        "recipient_street": DrawSpec(x=55, y=640, size=9),
        "recipient_city": DrawSpec(x=55, y=625, size=9),

        "recipient_tin": DrawSpec(x=210, y=695, size=9),

        "tax_year": DrawSpec(x=440, y=715, size=9),

        # Box 1 Nonemployee compensation
        "box1": DrawSpec(x=365, y=676, size=10),
    }
