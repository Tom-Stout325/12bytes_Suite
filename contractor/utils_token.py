from __future__ import annotations

from typing import Any, TypedDict

from django.core import signing
from django.http import HttpRequest
from django.urls import reverse


W9_SALT = "moneypro.contractor.w9"
W9_MAX_AGE_SECONDS = 60 * 60 * 24 * 14  # 14 days


class VerifiedToken(TypedDict):
    business_id: int
    contact_id: int


def issue_portal_token(*, business_id: int, contact_id: int) -> str:
    signer = signing.TimestampSigner(salt=W9_SALT)
    payload = {"b": int(business_id), "c": int(contact_id)}
    return signer.sign_object(payload)


def verify_portal_token(token: str) -> VerifiedToken | None:
    signer = signing.TimestampSigner(salt=W9_SALT)
    try:
        payload: Any = signer.unsign_object(token, max_age=W9_MAX_AGE_SECONDS)
    except Exception:
        return None

    try:
        business_id = int(payload.get("b"))
        contact_id = int(payload.get("c"))
        return {"business_id": business_id, "contact_id": contact_id}
    except Exception:
        return None


def build_portal_url(request: HttpRequest, token: str) -> str:
    path = reverse("contractor:w9_portal", kwargs={"token": token})
    return request.build_absolute_uri(path)
