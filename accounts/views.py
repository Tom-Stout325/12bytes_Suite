from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import UpdateView
from django.db import transaction

from .forms import CompanyProfileForm, UserInfoForm, BusinessEmailSettingsForm
from .models import CompanyProfile, Invitation
from .adapters import InviteOnlyAccountAdapter
from core.models import Business, BusinessMembership, get_or_create_business_email_settings
from ledger.models import Category, Transaction


def _get_active_membership(user):
    return (
        BusinessMembership.objects.filter(user=user, is_active=True)
        .select_related("business")
        .first()
    )



class OnboardingView(LoginRequiredMixin, UpdateView):
    """Business onboarding (single funnel).

    Ensures:
    - user has an active BusinessMembership
    - Business has a CompanyProfile (NOT complete by default)

    After POST success:
    - sync Business.name to CompanyProfile.company_name
    - redirect to dashboard
    """
    model = CompanyProfile
    form_class = CompanyProfileForm
    template_name = "accounts/onboarding.html"
    success_url = reverse_lazy("dashboard:home")

    def get_object(self, queryset=None):
        with transaction.atomic():
            membership = (
                BusinessMembership.objects.select_for_update()
                .filter(user=self.request.user, is_active=True)
                .select_related("business")
                .first()
            )

            if not membership:
                default_name = (getattr(settings, "DEFAULT_COMPANY_NAME", "") or "").strip() or "Your Business"
                business = Business.objects.create(name=default_name)
                membership = BusinessMembership.objects.create(
                    business=business,
                    user=self.request.user,
                    role=BusinessMembership.Role.OWNER,
                    is_active=True,
                )

            business = membership.business

            # IMPORTANT: do NOT mark profile "complete" on GET by copying business.name
            profile, _ = CompanyProfile.objects.get_or_create(
                business=business,
                defaults={
                    "created_by": self.request.user,
                    "company_name": "",  # force the user to enter a real business name
                },
            )
            return profile

    def form_valid(self, form):
        self.object = form.save()

        # Keep Business name aligned to submitted CompanyProfile name
        business = self.object.business
        new_name = (self.object.company_name or "").strip()
        if new_name and business.name != new_name:
            business.name = new_name
            business.save(update_fields=["name"])

        messages.success(self.request, "Company setup saved.")
        return redirect("dashboard:home") 


class SettingsView(LoginRequiredMixin, View):
    template_name = "accounts/settings.html"

    def _get_profile_or_redirect(self, request):
        membership = _get_active_membership(request.user)
        if not membership:
            return None, redirect("accounts:onboarding")

        business = membership.business
        profile, _ = CompanyProfile.objects.get_or_create(
            business=business,
            defaults={"created_by": request.user, "company_name": business.name},
        )
        return profile, None

    def dispatch(self, request, *args, **kwargs):
        profile, resp = self._get_profile_or_redirect(request)
        if resp:
            return resp
        if not profile.is_complete:
            return redirect("accounts:onboarding")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        profile, resp = self._get_profile_or_redirect(request)
        if resp:
            return resp

        business = profile.business

        email_settings = get_or_create_business_email_settings(business=business, owner_user=request.user)

        return render(
            request,
            self.template_name,
            {
                "company_form": CompanyProfileForm(instance=profile, prefix="company"),
                "user_form": UserInfoForm(instance=request.user, prefix="user"),
                "email_form": BusinessEmailSettingsForm(instance=email_settings, prefix="email"),
                "email_settings": email_settings,
                "has_seeded": Category.objects.filter(business=business).exists(),
                "can_rebuild": not Transaction.objects.filter(business=business).exists(),
            },
        )

    def post(self, request):
        profile, resp = self._get_profile_or_redirect(request)
        if resp:
            return resp

        business = profile.business

        form_id = request.POST.get("form_id", "")

        email_settings = get_or_create_business_email_settings(business=business, owner_user=request.user)

        company_form = CompanyProfileForm(instance=profile, prefix="company")
        user_form = UserInfoForm(instance=request.user, prefix="user")
        email_form = BusinessEmailSettingsForm(instance=email_settings, prefix="email")

        if form_id == "company":
            company_form = CompanyProfileForm(request.POST, request.FILES, instance=profile, prefix="company")
            if company_form.is_valid():
                obj = company_form.save()

                name = (obj.company_name or "").strip()
                if name and obj.business.name != name:
                    obj.business.name = name
                    obj.business.save(update_fields=["name"])

                messages.success(request, "Company settings saved.")
                return redirect("accounts:settings")

        elif form_id == "user":
            user_form = UserInfoForm(request.POST, instance=request.user, prefix="user")
            if user_form.is_valid():
                user_form.save()
                messages.success(request, "User info updated.")
                return redirect("accounts:settings")

        elif form_id == "email":
            email_form = BusinessEmailSettingsForm(request.POST, instance=email_settings, prefix="email")
            if email_form.is_valid():
                email_form.save()
                messages.success(request, "Email settings saved.")
                return redirect("accounts:settings")

        else:
            messages.error(request, "Invalid form submission.")

        return render(
            request,
            self.template_name,
            {
                "company_form": company_form,
                "user_form": user_form,
                "email_form": email_form,
                "email_settings": email_settings,
                "has_seeded": Category.objects.filter(business=business).exists(),
                "can_rebuild": not Transaction.objects.filter(business=business).exists(),
            },
        )


def invite_start(request, token: str):
    """Validate an invitation token and redirect the user to allauth signup.

    This stores the invitation token + email into session so the allauth
    adapter can allow signup and lock the email address.
    """

    try:
        inv = Invitation.objects.select_related("invited_by").get(token=token)
    except Invitation.DoesNotExist:
        raise Http404("Invalid invitation link.")

    if inv.is_expired:
        raise Http404("Invitation expired.")

    if inv.is_used:
        raise Http404("Invitation already used.")

    adapter = InviteOnlyAccountAdapter()
    request.session[adapter.SESSION_INVITE_TOKEN_KEY] = inv.token
    request.session[adapter.SESSION_INVITE_EMAIL_KEY] = (inv.email or "").strip().lower()

    messages.info(request, "Your invite has been validated. Create your account to continue.")
    return redirect("account_signup")
