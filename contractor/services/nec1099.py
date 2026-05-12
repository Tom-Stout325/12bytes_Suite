from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional

from django.db.models import Sum
from django.utils import timezone

from accounts.models import CompanyProfile
from ledger.models import Contact, Transaction


@dataclass(frozen=True)
class NEC1099Totals:
    year: int
    contact: Contact
    total: Decimal


def default_tax_year() -> int:
    # Default to prior calendar year (typical 1099 workflow)
    return timezone.localdate().year - 1


def nec_total_for_contact(*, business_id: int, contact_id: int, year: int) -> Decimal:
    row = Transaction.objects.filter(
        business_id=business_id,
        contact_id=contact_id,
        trans_type=Transaction.TransactionType.EXPENSE,
        date__year=year,
        is_refund=False,
        subcategory__is_1099_reportable_default=True,
    ).aggregate(total=Sum("amount"))

    return row["total"] or Decimal("0.00")


def nec_totals_for_year(*, business_id: int, year: int) -> list[NEC1099Totals]:
    contacts = (
        Contact.objects.filter(business_id=business_id, is_contractor=True, is_vendor=True, is_active=True)
        .order_by("display_name")
    )

    out: list[NEC1099Totals] = []
    for c in contacts:
        if not c.is_1099_eligible:
            continue
        total = nec_total_for_contact(business_id=business_id, contact_id=c.id, year=year)
        out.append(NEC1099Totals(year=year, contact=c, total=total))
    return out


def payer_block_for_business(*, business) -> str:
    # Prefer CompanyProfile (MoneyPro already has this tied to business)
    cp: Optional[CompanyProfile] = getattr(business, "company_profile", None)
    if not cp:
        return business.name

    lines: list[str] = [cp.company_name or business.name]
    if cp.address_line1:
        lines.append(cp.address_line1)
    if cp.address_line2:
        lines.append(cp.address_line2)
    city_state_zip = " ".join([x for x in [cp.city, cp.state, cp.postal_code] if x])
    if city_state_zip:
        lines.append(city_state_zip)
    return "\n".join(lines)


def payer_tin_for_business(*, business) -> str:
    cp: Optional[CompanyProfile] = getattr(business, "company_profile", None)
    return (cp.ein or "").strip() if cp else ""


def recipient_name(contact: Contact) -> str:
    return (contact.legal_name or contact.business_name or contact.display_name or "").strip()


def recipient_address_lines(contact: Contact) -> tuple[str, str]:
    street = (contact.address1 or "").strip()
    city_state_zip = " ".join([x for x in [contact.city, contact.state, contact.zip_code] if x]).strip()
    return street, city_state_zip


def masked_recipient_tin(contact: Contact) -> str:
    last4 = (contact.tin_last4 or "").strip()
    if not last4:
        return ""
    return f"****{last4}"
