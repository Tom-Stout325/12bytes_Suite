from decimal import Decimal
from datetime import date
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import CompanyProfile
from core.models import Business, BusinessMembership
from ledger.models import Category, Contact, SubCategory, Transaction

from .models import Invoice, InvoiceItem


class InvoiceDetailExpenseBreakdownTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Invoice Detail Business")
        self.user = get_user_model().objects.create_user(
            "invoice-detail-user",
            password="test-password",
        )
        BusinessMembership.objects.create(business=self.business, user=self.user)
        CompanyProfile.objects.create(
            business=self.business,
            company_name="Invoice Detail Business",
        )
        self.contact = Contact.objects.create(
            business=self.business,
            display_name="Invoice Customer",
            is_customer=True,
        )
        self.invoice = Invoice.objects.create(
            business=self.business,
            contact=self.contact,
            invoice_number="260101",
            issue_date=date(2026, 1, 15),
            subtotal=Decimal("500.00"),
            total=Decimal("500.00"),
        )
        InvoiceItem.objects.create(
            business=self.business,
            invoice=self.invoice,
            description="Drone Services",
            qty=Decimal("1.00"),
            unit_price=Decimal("500.00"),
        )
        self.travel = Category.objects.create(
            business=self.business,
            name="Travel",
            category_type=Category.CategoryType.EXPENSE,
        )
        self.meals = SubCategory.objects.create(
            business=self.business,
            category=self.travel,
            name="Meals",
            account_type=SubCategory.AccountType.EXPENSE,
            deduction_rule=SubCategory.DeductionRule.MEALS_50,
        )
        self.hotels = SubCategory.objects.create(
            business=self.business,
            category=self.travel,
            name="Hotels",
            account_type=SubCategory.AccountType.EXPENSE,
        )
        self.client.force_login(self.user)

    def expense(self, subcategory, amount, description="Expense", *, business=None):
        return Transaction.objects.create(
            business=business or self.business,
            date=self.invoice.issue_date,
            amount=Decimal(amount),
            description=description,
            subcategory=subcategory,
            invoice_number=self.invoice.invoice_number,
        )

    def detail(self):
        return self.client.get(reverse("invoices:invoice_detail", args=[self.invoice.pk]))

    def test_expenses_group_by_category_and_subcategory_and_reconcile(self):
        self.expense(self.meals, "25.00", "Lunch")
        self.expense(self.meals, "35.00", "Dinner")
        self.expense(self.hotels, "200.00", "Hotel")

        response = self.detail()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["expense_breakdown"],
            [
                {"label": "Travel: Hotels", "amount": Decimal("200.00")},
                {"label": "Travel: Meals", "amount": Decimal("60.00")},
            ],
        )
        self.assertEqual(response.context["expense_total"], Decimal("260.00"))
        self.assertEqual(
            sum(row["amount"] for row in response.context["expense_breakdown"]),
            response.context["expense_total"],
        )
        self.assertContains(response, "Income")
        self.assertContains(response, "Drone Services")
        self.assertContains(response, "Travel: Meals")
        self.assertContains(response, "Travel: Hotels")
        self.assertContains(response, "Actual Expenses Total")
        self.assertContains(response, "$260.00")

    def test_actual_breakdown_does_not_use_taxable_expense_amount(self):
        self.expense(self.meals, "100.00")

        response = self.detail()

        self.assertEqual(response.context["expense_total"], Decimal("100.00"))
        self.assertEqual(response.context["taxable_expense_total"], Decimal("50.00"))
        self.assertEqual(
            response.context["expense_breakdown"],
            [{"label": "Travel: Meals", "amount": Decimal("100.00")}],
        )

    def test_prequalified_subcategory_label_is_not_duplicated(self):
        nested_category = Category.objects.create(
            business=self.business,
            name="Travel & Meals: Travel",
            category_type=Category.CategoryType.EXPENSE,
        )
        gas = SubCategory.objects.create(
            business=self.business,
            category=nested_category,
            name="Travel: Gas",
            account_type=SubCategory.AccountType.EXPENSE,
        )
        self.expense(gas, "45.00")

        response = self.detail()

        self.assertEqual(
            response.context["expense_breakdown"],
            [{"label": "Travel: Gas", "amount": Decimal("45.00")}],
        )
        self.assertNotContains(response, "Travel &amp; Meals: Travel: Travel: Gas")

    def test_invoice_without_expenses_omits_expense_breakdown(self):
        response = self.detail()

        self.assertEqual(response.context["expense_breakdown"], [])
        self.assertNotContains(response, "Actual Expenses Total")
        self.assertNotContains(response, "Travel: Meals")

    def test_expenses_are_business_isolated_and_invoice_totals_are_unchanged(self):
        other_business = Business.objects.create(name="Other Invoice Business")
        other_category = Category.objects.create(
            business=other_business,
            name="Private Category",
            category_type=Category.CategoryType.EXPENSE,
        )
        other_subcategory = SubCategory.objects.create(
            business=other_business,
            category=other_category,
            name="Private Expense",
            account_type=SubCategory.AccountType.EXPENSE,
        )
        self.expense(other_subcategory, "999.00", business=other_business)
        self.expense(self.hotels, "75.00")

        response = self.detail()
        self.invoice.refresh_from_db()

        self.assertEqual(response.context["expense_total"], Decimal("75.00"))
        self.assertNotContains(response, "Private Category")
        self.assertEqual(self.invoice.subtotal, Decimal("500.00"))
        self.assertEqual(self.invoice.total, Decimal("500.00"))

    def test_invoice_review_uses_authoritative_items_when_cached_totals_are_zero(self):
        Invoice.objects.filter(pk=self.invoice.pk).update(
            subtotal=Decimal("0.00"), total=Decimal("0.00")
        )

        response = self.detail()

        self.assertEqual(response.context["invoice"].subtotal, Decimal("500.00"))
        self.assertEqual(response.context["invoice"].total, Decimal("500.00"))
        self.assertContains(response, "$500.00")

    def test_repair_invoice_totals_updates_cache_but_does_not_fabricate_missing_items(self):
        Invoice.objects.filter(pk=self.invoice.pk).update(
            subtotal=Decimal("0.00"), total=Decimal("0.00")
        )
        missing = Invoice.objects.create(
            business=self.business,
            contact=self.contact,
            invoice_number="260102",
            issue_date=date(2026, 1, 20),
            subtotal=Decimal("700.00"),
            total=Decimal("700.00"),
        )
        output = StringIO()

        call_command(
            "repair_invoice_totals",
            business=str(self.business.pk),
            apply=True,
            stdout=output,
        )

        self.invoice.refresh_from_db()
        missing.refresh_from_db()
        self.assertEqual(self.invoice.total, Decimal("500.00"))
        self.assertEqual(missing.total, Decimal("700.00"))
        self.assertIn("missing_items=1", output.getvalue())
