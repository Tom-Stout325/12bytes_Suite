# core/views.py
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

@login_required
def business_onboarding(request):
    # Single onboarding funnel lives in accounts.
    return redirect("accounts:onboarding")
