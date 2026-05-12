from django.shortcuts import redirect


def home(request):
    """Route users to the correct Suite landing page.

    Public visitors should see the allauth login page. Authenticated users should
    land on the Suite dashboard after the standard business/onboarding checks.
    """
    if not request.user.is_authenticated:
        return redirect("account_login")

    business = getattr(request, "business", None)
    if not business:
        return redirect("accounts:onboarding")

    profile = getattr(business, "company_profile", None)
    if not profile or not profile.is_complete:
        return redirect("accounts:onboarding")

    return redirect("dashboard:home")
