from __future__ import annotations

from django.contrib import admin, messages
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import BadHeaderError
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse

from .models import CompanyProfile, Invitation
from .services import send_invitation_email


class OwnedOneToOneAdminMixin:
    def get_queryset(self, request) -> QuerySet:
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs.select_related("user")
        return qs.select_related("user").filter(user=request.user)

    def save_model(self, request, obj, form, change):
        if not change and not getattr(obj, "user_id", None):
            obj.user = request.user
        super().save_model(request, obj, form, change)

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser or obj is None:
            return True
        return obj.user_id == request.user.id

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser or obj is None:
            return True
        return obj.user_id == request.user.id

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser or obj is None:
            return True
        return obj.user_id == request.user.id


@admin.register(CompanyProfile)
class CompanyProfileAdmin(admin.ModelAdmin):
    list_display = (
        "company_name",
        "business",
        "billing_email",
        "phone_display",
        "timezone",
        "currency",
        "created_at",
    )
    search_fields = (
        "company_name",
        "legal_name",
        "ein",
        "billing_email",
        "business__name",
        "created_by__email",
        "created_by__username",
    )
    list_filter = ("timezone", "currency", "country")
    readonly_fields = ("created_at", "updated_at", "phone_display")
    list_select_related = ("business", "created_by")

    fieldsets = (
        ("Business", {
            "fields": ("business", "created_by"),
        }),
        ("Identity", {
            "fields": ("company_name", "legal_name", "ein"),
        }),
        ("Contact", {
            "fields": ("phone", "phone_display", "billing_email", "website"),
        }),
        ("Address", {
            "fields": ("address_line1", "address_line2", "city", "state", "postal_code", "country"),
        }),
        ("Branding", {
            "fields": ("logo",),
        }),
        ("Locale / Formatting", {
            "fields": ("timezone", "currency"),
        }),
        ("Status", {
            "fields": ("created_at", "updated_at"),
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("business", "created_by")

    def get_form(self, request, obj=None, **kwargs):
        """
        Lock business field for non-superusers to prevent cross-business assignment.
        """
        form = super().get_form(request, obj, **kwargs)
        if not request.user.is_superuser and "business" in form.base_fields:
            form.base_fields["business"].disabled = True
        if not request.user.is_superuser and "created_by" in form.base_fields:
            form.base_fields["created_by"].disabled = True
        return form


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "invited_by",
        "created_at",
        "expires_at",
        "accepted_at",
        "is_used",
        "is_expired",
    )
    list_filter = ("accepted_at",)
    search_fields = ("email", "invited_by__email", "invited_by__username")
    readonly_fields = ("token", "created_at", "accepted_at", "accepted_user")

    actions = ["send_invite_email"]
    change_form_template = "admin/accounts/invitation/change_form.html"

    @admin.action(description="Send invite email")
    def send_invite_email(self, request, queryset):
        sent = 0
        renewed = 0

        try:
            for inv in queryset:
                inv_to_send = inv
                if inv.is_expired or inv.is_used:
                    inv_to_send = Invitation.objects.create(
                        email=inv.email,
                        invited_by=inv.invited_by,
                    )
                    renewed += 1

                self._send_invite(request, inv_to_send)
                sent += 1

        except (ImproperlyConfigured, BadHeaderError, ValueError) as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return
        except Exception as exc:
            self.message_user(
                request,
                f"Invite email could not be sent: {exc}",
                level=messages.ERROR,
            )
            return

        messages.success(
            request,
            f"Sent {sent} invite email(s)."
            + (f" ({renewed} renewed)" if renewed else ""),
        )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/resend/",
                self.admin_site.admin_view(self.resend_invite_view),
                name="accounts_invitation_resend",
            ),
        ]
        return custom + urls

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        if obj and obj.pk:
            context["resend_invite_url"] = reverse(
                "admin:accounts_invitation_resend",
                args=[obj.pk],
            )
            context["show_resend_invite"] = True
            context["resend_invite_label"] = "Resend invite"
        return super().render_change_form(request, context, add, change, form_url, obj)

    def resend_invite_view(self, request, object_id):
        inv = get_object_or_404(Invitation, pk=object_id)

        inv_to_send = inv
        renewed = False

        if inv.is_expired or inv.is_used:
            inv_to_send = Invitation.objects.create(
                email=inv.email,
                invited_by=inv.invited_by,
            )
            renewed = True

        try:
            self._send_invite(request, inv_to_send)
        except (ImproperlyConfigured, BadHeaderError, ValueError) as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return redirect(reverse("admin:accounts_invitation_change", args=[inv.pk]))
        except Exception as exc:
            self.message_user(
                request,
                f"Invite email could not be sent: {exc}",
                level=messages.ERROR,
            )
            return redirect(reverse("admin:accounts_invitation_change", args=[inv.pk]))

        if renewed:
            messages.success(request, f"Invite was renewed and sent to {inv_to_send.email}.")
            return redirect(reverse("admin:accounts_invitation_change", args=[inv_to_send.pk]))

        messages.success(request, f"Invite re-sent to {inv_to_send.email}.")
        return redirect("../")

    def _send_invite(self, request, inv: Invitation) -> None:
        send_invitation_email(invitation=inv, request_obj=request)