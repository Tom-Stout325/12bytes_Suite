from __future__ import annotations

import io
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from PIL import Image, ImageDraw, ImageFont

from .services.nec1099 import (
    masked_recipient_tin,
    payer_block_for_business,
    payer_tin_for_business,
    recipient_address_lines,
    recipient_name,
)


def _money_str(val: Decimal) -> str:
    q = Decimal(val).quantize(Decimal("0.01"))
    return f"{q:,.2f}"


@dataclass(frozen=True)
class _Field:
    x: int
    y: int
    size: int = 28


_LAYOUTS: dict[str, dict[str, _Field]] = {
    "b": {
        "payer_block": _Field(170, 105, 23),
        "payer_tin": _Field(175, 320, 25),
        "recipient_tin": _Field(585, 320, 25),
        "year": _Field(1455, 244, 27),
        "box1": _Field(1490, 358, 30),
        "recipient_name": _Field(175, 402, 25),
        "recipient_street": _Field(175, 523, 24),
        "recipient_city_state_zip": _Field(175, 610, 24),
    },
    "1": {
        "payer_block": _Field(185, 112, 24),
        "payer_tin": _Field(190, 338, 26),
        "recipient_tin": _Field(636, 338, 26),
        "year": _Field(1525, 255, 28),
        "box1": _Field(1510, 385, 31),
        "recipient_name": _Field(185, 430, 26),
        "recipient_street": _Field(185, 560, 24),
        "recipient_city_state_zip": _Field(185, 648, 24),
    },
}


def _template_path(copy: Literal["b", "1"] = "b") -> Path:
    base = Path(settings.BASE_DIR) / "static" / "images"
    if copy == "b":
        candidate = base / "1099-NEC Copy B.png"
    else:
        candidate = base / "1099-NEC Copy 1.png"
    if not candidate.exists():
        raise FileNotFoundError(f"1099 template image not found: {candidate}")
    return candidate


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _draw_multiline(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, *, size: int, max_lines: int = 4) -> None:
    if not text:
        return
    font = _load_font(size)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:max_lines]
    line_gap = size + 6
    for idx, line in enumerate(lines):
        draw.text((x, y + (idx * line_gap)), line[:90], fill="black", font=font)


def _draw_text(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, *, size: int) -> None:
    if not text:
        return
    draw.text((x, y), text, fill="black", font=_load_font(size))


def _render_image(*, business: Any, contractor: Any, year: int, nonemployee_comp: Decimal, copy: Literal["b", "1"]) -> Image.Image:
    template = Image.open(_template_path(copy)).convert("RGB")
    draw = ImageDraw.Draw(template)
    layout = _LAYOUTS[copy]

    payer_block = payer_block_for_business(business=business)
    payer_tin = payer_tin_for_business(business=business)
    rec_name = recipient_name(contractor)
    street, city_state_zip = recipient_address_lines(contractor)
    recipient_tin = masked_recipient_tin(contractor)
    amount = _money_str(nonemployee_comp)

    _draw_multiline(draw, layout["payer_block"].x, layout["payer_block"].y, payer_block, size=layout["payer_block"].size)
    _draw_text(draw, layout["payer_tin"].x, layout["payer_tin"].y, payer_tin, size=layout["payer_tin"].size)
    _draw_text(draw, layout["recipient_tin"].x, layout["recipient_tin"].y, recipient_tin, size=layout["recipient_tin"].size)
    _draw_text(draw, layout["year"].x, layout["year"].y, str(year), size=layout["year"].size)
    _draw_text(draw, layout["box1"].x, layout["box1"].y, amount, size=layout["box1"].size)
    _draw_text(draw, layout["recipient_name"].x, layout["recipient_name"].y, rec_name, size=layout["recipient_name"].size)
    _draw_text(draw, layout["recipient_street"].x, layout["recipient_street"].y, street, size=layout["recipient_street"].size)
    _draw_text(draw, layout["recipient_city_state_zip"].x, layout["recipient_city_state_zip"].y, city_state_zip, size=layout["recipient_city_state_zip"].size)
    return template


def render_1099nec_pdf_bytes(
    *,
    business: Any,
    contractor: Any,
    year: int,
    nonemployee_comp: Decimal,
    copy: Literal["b", "1"] = "b",
) -> bytes:
    image = _render_image(
        business=business,
        contractor=contractor,
        year=year,
        nonemployee_comp=nonemployee_comp,
        copy=copy,
    )
    out = io.BytesIO()
    image.save(out, format="PDF", resolution=200.0)
    return out.getvalue()


def render_1099nec_pdf_response(
    *,
    request: HttpRequest,
    business: Any,
    contractor: Any,
    year: int,
    nonemployee_comp: Decimal,
) -> HttpResponse:
    pdf_bytes = render_1099nec_pdf_bytes(
        business=business,
        contractor=contractor,
        year=year,
        nonemployee_comp=nonemployee_comp,
        copy="b",
    )
    filename = f"1099-NEC_{year}_{getattr(contractor, 'id', 'contractor')}_copyB.pdf"
    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp
