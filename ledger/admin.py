# ledger/admin.py
from __future__ import annotations

from django.contrib import admin
from django.db.models import QuerySet

from core.models import BusinessMembership
from .models import Category, Job, Contact, SubCategory, Team, Transaction


class BusinessAdminMixin(admin.ModelAdmin):
    """Scope objects to the user's business in Django Admin (for non-superusers)."""

    def _user_business(self, request):
        membership = (
            BusinessMembership.objects.filter(user=request.user, is_active=True)
            .select_related("business")
            .first()
        )
        return membership.business if membership else None

    def get_queryset(self, request):
        qs: QuerySet = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        biz = self._user_business(request)
        return qs.filter(business=biz) if biz else qs.none()

    def save_model(self, request, obj, form, change):
        if not change and getattr(obj, "business_id", None) is None and not request.user.is_superuser:
            biz = self._user_business(request)
            obj.business = biz
        super().save_model(request, obj, form, change)


@admin.register(Category)
class CategoryAdmin(BusinessAdminMixin):
    list_display = ("name", "category_type", "schedule_c_line", "business")
    list_filter = ("category_type", "business")
    search_fields = ("name",)


@admin.register(SubCategory)
class SubCategoryAdmin(BusinessAdminMixin):
    list_display = ("name", "category", "deduction_rule", "business")
    list_filter = ("deduction_rule", "business")
    search_fields = ("name", "category__name")


@admin.register(Contact)
class ContactAdmin(BusinessAdminMixin):
    list_display = ("display_name", "client_code", "is_vendor", "is_customer", "is_contractor", "business")
    list_filter = ("is_vendor", "is_customer", "is_contractor", "business")
    search_fields = ("display_name", "client_code", "legal_name", "business_name")

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj=obj))
        if obj and obj.pk:
            ro.append("client_code")
        return ro



@admin.register(Job)
class JobAdmin(BusinessAdminMixin):
    list_display = ("job_number", "label", "job_year", "job_type", "client", "is_active", "business")
    list_filter = ("job_type", "is_active", "business")
    search_fields = ("job_number", "label", "client__display_name")


@admin.register(Team)
class TeamAdmin(BusinessAdminMixin):
    list_display = ("name", "is_active", "sort_order", "business")
    list_filter = ("is_active", "business")
    search_fields = ("name",)


@admin.register(Transaction)
class TransactionAdmin(BusinessAdminMixin):
    list_display = ("date", "description", "amount", "category", "subcategory", "business")
    list_filter = ("category", "business")
    search_fields = ("description", "notes", "invoice_number")
