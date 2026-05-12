from __future__ import annotations

from django.contrib import admin
from django.db.models import QuerySet

from core.models import BusinessMembership
from .models import Vehicle, VehicleLoan, VehicleLoanPayment, VehicleMiles, VehicleYear


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

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser:
            biz = self._user_business(request)
            if biz:
                model = db_field.remote_field.model
                if hasattr(model, "business_id"):
                    kwargs["queryset"] = model.objects.filter(business=biz)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Vehicle)
class VehicleAdmin(BusinessAdminMixin):
    list_display = ("label", "year", "make", "model", "plate", "is_active")
    list_filter = ("is_active", "make", "year")
    search_fields = ("label", "make", "model", "plate", "vin_last6")


@admin.register(VehicleYear)
class VehicleYearAdmin(BusinessAdminMixin):
    list_display = (
        "vehicle",
        "year",
        "deduction_method",
        "business_use_pct_display",
        "annual_interest_paid",
        "generated_interest_paid_display",
        "business_interest_amount_display",
        "deduction_amount_display",
        "is_locked",
    )
    list_filter = ("year", "deduction_method", "is_locked")
    search_fields = ("vehicle__label",)
    autocomplete_fields = ("vehicle",)
    readonly_fields = (
        "total_miles_display",
        "business_miles_display",
        "other_miles_display",
        "business_use_pct_display",
        "generated_interest_paid_display",
        "business_interest_amount_display",
        "actual_expenses_with_interest_total_display",
        "deduction_amount_display",
    )
    fieldsets = (
        (None, {"fields": ("vehicle", "year", "deduction_method", "is_locked")}),
        ("Odometer", {"fields": ("odometer_start", "odometer_end", "total_miles_display")}),
        (
            "Interest",
            {
                "fields": (
                    "annual_interest_paid",
                    "generated_interest_paid_display",
                    "business_interest_amount_display",
                    "actual_expenses_with_interest_total_display",
                    "deduction_amount_display",
                )
            },
        ),
        ("Mileage", {"fields": ("business_miles_display", "other_miles_display", "business_use_pct_display", "standard_mileage_rate")}),
    )

    @admin.display(description="Total miles")
    def total_miles_display(self, obj):
        return obj.total_miles

    @admin.display(description="Business miles")
    def business_miles_display(self, obj):
        return obj.business_miles

    @admin.display(description="Other miles")
    def other_miles_display(self, obj):
        return obj.other_miles

    @admin.display(description="Business use %")
    def business_use_pct_display(self, obj):
        return obj.business_use_pct

    @admin.display(description="Generated interest")
    def generated_interest_paid_display(self, obj):
        return obj.generated_interest_paid

    @admin.display(description="Business interest")
    def business_interest_amount_display(self, obj):
        return obj.business_interest_amount

    @admin.display(description="Actual + interest")
    def actual_expenses_with_interest_total_display(self, obj):
        return obj.actual_expenses_with_interest_total

    @admin.display(description="Deduction")
    def deduction_amount_display(self, obj):
        return obj.deduction_amount


@admin.register(VehicleMiles)
class VehicleMilesAdmin(BusinessAdminMixin):
    list_display = ("date", "vehicle", "mileage_type", "begin", "end", "total", "job", "invoice")
    list_filter = ("mileage_type", "date", "vehicle")
    search_fields = ("vehicle__label", "notes")
    autocomplete_fields = ("vehicle", "job", "invoice")
    readonly_fields = ("total",)


class VehicleLoanPaymentInline(admin.TabularInline):
    model = VehicleLoanPayment
    extra = 0
    fields = ("payment_number", "payment_date", "beginning_balance", "payment_amount", "principal_amount", "interest_amount", "ending_balance")
    readonly_fields = fields
    can_delete = False
    show_change_link = False


@admin.register(VehicleLoan)
class VehicleLoanAdmin(BusinessAdminMixin):
    list_display = ("vehicle", "purchase_date", "original_loan_amount", "annual_interest_rate", "number_of_payments", "payment_amount_display")
    search_fields = ("vehicle__label",)
    autocomplete_fields = ("vehicle",)
    inlines = [VehicleLoanPaymentInline]

    @admin.display(description="Monthly payment")
    def payment_amount_display(self, obj):
        return obj.payment_amount


@admin.register(VehicleLoanPayment)
class VehicleLoanPaymentAdmin(BusinessAdminMixin):
    list_display = ("loan", "payment_number", "payment_date", "payment_amount", "principal_amount", "interest_amount", "ending_balance")
    list_filter = ("payment_date",)
    search_fields = ("loan__vehicle__label",)
    autocomplete_fields = ("loan",)
