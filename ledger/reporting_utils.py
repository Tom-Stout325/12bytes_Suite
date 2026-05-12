# ledger/reporting_utils.py
from __future__ import annotations

import re

from ledger.models import Category

LINE_RE = re.compile(r"^\s*(\d+)\s*([a-zA-Z]?)\s*$")


def _to_line_label(line: str | None) -> str:
    """Normalize schedule_c_line to the human label (e.g., '24b').

    Your DB stores the *choice value* (e.g., 'meals'), but sorting and routing
    want the human label (e.g., '24b').
    """
    if not line:
        return ""
    if LINE_RE.match(line):
        return line.strip()

    try:
        return Category.ScheduleCLine(line).label
    except Exception:
        return str(line).strip()


def schedule_c_sort_key(line: str | None) -> tuple[int, int]:
    """Sort Schedule C lines in IRS-friendly order.

    Accepts either:
    - label forms: '1', '16a', '24b'
    - stored values: 'gross_receipts', 'interest_other', 'meals', etc.
    """
    label = _to_line_label(line)

    if not label:
        return (9999, 0)

    m = LINE_RE.match(label)
    if not m:
        return (9999, 0)

    num = int(m.group(1))
    suffix = (m.group(2) or "").lower()

    # a/b ordering within same line number
    suffix_rank = 0
    if suffix:
        suffix_rank = 1 + (ord(suffix) - ord("a"))

    return (num, suffix_rank)


def route_category_for_report(*, category_name: str, schedule_c_line: str, report_group: str) -> str:
    """Route a Category total into a Schedule C 'Part' bucket.

    We route primarily by Schedule C line label, falling back to report_group.
    """
    label = _to_line_label(schedule_c_line)

    # Part I — Income (lines 1-7)
    if label and label[0].isdigit():
        # crude routing based on line number
        num = schedule_c_sort_key(label)[0]
        if 1 <= num <= 7:
            return "Part I"

        # Part II — Expenses
        if 8 <= num <= 29:
            return "Part II"

    # Fallback buckets if needed
    if report_group:
        return report_group

    return "Part II"


def route_subcategory_for_report(*, category_name: str, schedule_c_line: str, default_group: str) -> str:
    """Route SubCategory detail for certain categories into Part V."""
    label = _to_line_label(schedule_c_line)

    # If category maps to 'Other Expenses (27b)', details go to Part V
    if label == "27b":
        return "Part V"

    return default_group or route_category_for_report(
        category_name=category_name,
        schedule_c_line=schedule_c_line,
        report_group=default_group,
    )
