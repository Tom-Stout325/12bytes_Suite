from django.shortcuts import redirect, render

def home(request):
    if request.user.is_authenticated:
        business = getattr(request, "business", None)
        if not business:
            return redirect("accounts:onboarding")

        profile = getattr(business, "company_profile", None)
        if not profile or not profile.is_complete:
            return redirect("accounts:onboarding")

        return redirect("dashboard:home")

    return render(request, "home.html")
