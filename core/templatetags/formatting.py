# core/templatetags/formatting.py
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
import re
from django import template

register = template.Library()


def _to_decimal(value: Any) -> Decimal | None:
    """
    Convert common numeric inputs to Decimal safely.
    Returns None for blank/None values.
    """
    if value is None:
        return None
    if value == "":
        return None

    if isinstance(value, Decimal):
        return value

    try:
        # Avoid float artifacts by converting through str
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _fmt_money(amount: Decimal) -> str:
    """
    Format with USD $ and comma grouping, always 2 decimals.
    Negative amounts render as -$123.45 (not parentheses).
    """
    sign = "-" if amount < 0 else ""
    amt = abs(amount).quantize(Decimal("0.01"))
    return f"{sign}${amt:,.2f}"


@register.filter(name="money")
def money(value: Any) -> str:
    """
    $ with cents. Blank/None => '-'.
    Use for normal display; if a computed value is negative, it will show -$...
    """
    dec = _to_decimal(value)
    if dec is None:
        return "-"
    return _fmt_money(dec)


@register.filter(name="money_loss")
def money_loss(value: Any) -> str:
    """
    For report net values where you only want a negative when it's truly a loss.
    Blank/None => '-'.
    """
    dec = _to_decimal(value)
    if dec is None:
        return "-"
    # If it's a loss, show negative. Otherwise normal.
    return _fmt_money(dec)


@register.filter(name="mdy")
def mdy(value: Any) -> str:
    """
    Format dates as MM/DD/YYYY. Blank/None => '-'.
    """
    if not value:
        return "-"
    try:
        return value.strftime("%m/%d/%Y")
    except Exception:
        return "-"



@register.filter
def phone_us(value):
    """
    Format a US phone number as 317-987-7387
    """
    if not value:
        return ""

    digits = re.sub(r"\D", "", str(value))

    if len(digits) == 10:
        return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"
    return value