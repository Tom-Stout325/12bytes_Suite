# core/middleware.py
from django.shortcuts import redirect
from core.models import BusinessMembership

class ActiveBusinessMiddleware:
    ALLOW_PREFIXES = (
        "/admin/",
        "/accounts/",
        "/static/",
        "/media/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or "/"

        request.business = None
        if request.user.is_authenticated:
            membership = (
                BusinessMembership.objects.filter(user=request.user, is_active=True)
                .select_related("business")
                .first()
            )
            if membership:
                request.business = membership.business

        if any(path.startswith(prefix) for prefix in self.ALLOW_PREFIXES):
            return self.get_response(request)

        if request.user.is_authenticated:
            if request.business is None:
                return redirect("accounts:onboarding")

            profile = getattr(request.business, "company_profile", None)
            if not profile or not profile.is_complete:
                return redirect("accounts:onboarding")

        return self.get_response(request)
