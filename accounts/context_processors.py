from core.models import BusinessMembership

def company_context(request):
    """Provide active_business and company_profile to templates."""
    if not request.user.is_authenticated:
        return {"active_business": None, "company_profile": None}

    membership = (
        BusinessMembership.objects.filter(user=request.user, is_active=True)
        .select_related("business")
        .first()
    )
    if not membership:
        return {"active_business": None, "company_profile": None}

    business = membership.business
    return {
        "active_business": business,
        "company_profile": getattr(business, "company_profile", None),
    }
