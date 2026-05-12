from __future__ import annotations

from django.utils import timezone
from allauth.account.adapter import DefaultAccountAdapter

from core.models import Business, BusinessMembership
from .models import Invitation



class InviteOnlyAccountAdapter(DefaultAccountAdapter):
    """Invite-only signup adapter.

    - Public signup is disabled.
    - Signup is allowed only when a valid Invitation token has been placed
      into the session (via `accounts.views.invite_start`).

    Notes:
    - We DO create a placeholder Business + active membership at signup
      (Option B), but we do NOT mark the company profile complete here.
      Onboarding remains the step that makes the business "real".
    """

    SESSION_INVITE_TOKEN_KEY = "invite_token"
    SESSION_INVITE_EMAIL_KEY = "invite_email"

    def _get_invitation_from_session(self, request):
        if request is None:
            return None

        token = (request.session.get(self.SESSION_INVITE_TOKEN_KEY) or "").strip()
        if not token:
            token = (request.GET.get("invite") or "").strip()
        if not token:
            return None

        try:
            inv = Invitation.objects.get(token=token)
        except Invitation.DoesNotExist:
            return None

        if inv.is_expired or inv.is_used:
            return None

        # Persist into session so subsequent steps (including allauth internal
        # redirects) keep the invite context.
        request.session[self.SESSION_INVITE_TOKEN_KEY] = inv.token
        request.session[self.SESSION_INVITE_EMAIL_KEY] = (inv.email or "").strip().lower()
        request.session.modified = True
        return inv

    def is_open_for_signup(self, request):
        """Gate signup: only allow when the session contains a valid invite."""
        return self._get_invitation_from_session(request) is not None

    def save_user(self, request, user, form, commit=True):
        """On successful signup:
        - Create a placeholder Business + active membership if none exists (Option B).
        - Mark the invite as accepted.
        - Lock the user's email to the invited email.
        """
        user = super().save_user(request, user, form, commit=commit)

        inv = self._get_invitation_from_session(request)
        if inv is None:
            # Should not happen because is_open_for_signup gates it, but keep safe.
            return user

        invited_email = (inv.email or "").strip().lower()
        if invited_email:
            user.email = invited_email
            if commit:
                user.save(update_fields=["email"])

        if commit:
            # Option B: create placeholder business/membership at signup
            if not BusinessMembership.objects.filter(user=user, is_active=True).exists():
                biz = Business.objects.create(name="Your Business")
                BusinessMembership.objects.create(
                    user=user,
                    business=biz,
                    role=BusinessMembership.Role.OWNER,
                    is_active=True,
                )

            inv.accepted_user = user
            inv.accepted_at = timezone.now()
            inv.save(update_fields=["accepted_user", "accepted_at"])

            # Clear invite context from session
            try:
                request.session.pop(self.SESSION_INVITE_TOKEN_KEY, None)
                request.session.pop(self.SESSION_INVITE_EMAIL_KEY, None)
                request.session.modified = True
            except Exception:
                pass

        return user