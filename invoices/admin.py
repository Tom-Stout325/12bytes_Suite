from django.contrib import admin

from .models import Invoice, InvoiceCounter, InvoiceItem, InvoicePayment


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "status", "issue_date", "contact", "job", "team", "total")
    list_filter = ("status", "issue_date")
    search_fields = ("invoice_number", "contact__display_name", "job__label", "team__name")
    inlines = [InvoiceItemInline]


admin.site.register(InvoiceCounter)
admin.site.register(InvoicePayment)
