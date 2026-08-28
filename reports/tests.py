from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import CompanyProfile
from core.business_features import BusinessFeature
from core.models import Business, BusinessMembership
from invoices.models import Invoice, InvoiceItem, InvoicePayment
from ledger.models import Category, Contact, Job, SubCategory, Transaction


class TravelExpenseInvoiceRevenueTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Travel Report Business")
        self.other_business = Business.objects.create(name="Other Travel Business")
        self.user = get_user_model().objects.create_user("travel-report-user", password="test-password")
        BusinessMembership.objects.create(business=self.business, user=self.user)
        CompanyProfile.objects.create(business=self.business, company_name="Travel Report Business")
        BusinessFeature.objects.create(business=self.business, code="TRAVEL_EXPENSE_REPORT")
        self.contact = Contact.objects.create(
            business=self.business, display_name="Customer", is_customer=True
        )
        self.job = Job.objects.create(business=self.business, label="Norwalk", client=self.contact)
        income_category = Category.objects.create(
            business=self.business, name="Sales", category_type=Category.CategoryType.INCOME
        )
        self.income_subcategory = SubCategory.objects.create(
            business=self.business, category=income_category, name="Services",
            account_type=SubCategory.AccountType.INCOME,
        )
        self.client.force_login(self.user)

    def invoice(self, number, amount, *, status=Invoice.Status.PAID, stored_total=None):
        amount = Decimal(amount)
        stored_total = amount if stored_total is None else Decimal(stored_total)
        invoice = Invoice.objects.create(
            business=self.business, contact=self.contact, job=self.job,
            invoice_number=number, issue_date=date(2026, 5, 1), status=status,
            subtotal=stored_total, total=stored_total,
        )
        InvoiceItem.objects.create(
            business=self.business, invoice=invoice, description="Flight services",
            subcategory=self.income_subcategory, qty=Decimal("1.00"), unit_price=amount,
        )
        return invoice

    def income_transaction(self, invoice, amount):
        return Transaction.objects.create(
            business=invoice.business, date=invoice.issue_date, amount=Decimal(amount),
            description=f"Payment {invoice.invoice_number}", subcategory=self.income_subcategory,
            invoice_number=invoice.invoice_number,
        )

    def rows(self):
        response = self.client.get(reverse("reports:travel_expense_summary"), {"year": 2026})
        self.assertEqual(response.status_code, 200)
        return {row["invoice"].invoice_number: row for row in response.context["rows"]}

    def test_paid_invoice_with_line_items_and_linked_transaction(self):
        invoice = self.invoice("260101", "3700.00")
        invoice.income_transaction = self.income_transaction(invoice, "3700.00")
        invoice.save(update_fields=["income_transaction"])

        row = self.rows()[invoice.invoice_number]
        self.assertEqual(row["invoice_total"], Decimal("3700.00"))
        self.assertEqual(row["invoice_amount"], Decimal("3700.00"))

    def test_stale_zero_invoice_total_does_not_hide_legacy_payment_transaction(self):
        invoice = self.invoice("260102", "2500.00", stored_total="0.00")
        self.income_transaction(invoice, "2500.00")

        row = self.rows()[invoice.invoice_number]
        self.assertEqual(row["invoice_total"], Decimal("2500.00"))
        self.assertEqual(row["invoice_amount"], Decimal("2500.00"))

    def test_partial_payment_records_are_summed(self):
        invoice = self.invoice("260103", "1000.00", status=Invoice.Status.SENT)
        InvoicePayment.objects.create(
            business=self.business, invoice=invoice, date=date(2026, 5, 2), amount=Decimal("300.00")
        )
        InvoicePayment.objects.create(
            business=self.business, invoice=invoice, date=date(2026, 5, 3), amount=Decimal("200.00")
        )

        self.assertEqual(self.rows()[invoice.invoice_number]["invoice_amount"], Decimal("500.00"))

    def test_unpaid_invoice_reports_zero_received(self):
        invoice = self.invoice("260104", "900.00", status=Invoice.Status.SENT)

        row = self.rows()[invoice.invoice_number]
        self.assertEqual(row["invoice_total"], Decimal("900.00"))
        self.assertEqual(row["invoice_amount"], Decimal("0.00"))

    def test_tenant_isolation_excludes_other_business_invoice_and_transaction(self):
        other_contact = Contact.objects.create(
            business=self.other_business, display_name="Other Customer", is_customer=True
        )
        other_invoice = Invoice.objects.create(
            business=self.other_business, contact=other_contact, invoice_number="260105",
            issue_date=date(2026, 5, 1), status=Invoice.Status.PAID,
        )
        other_category = Category.objects.create(
            business=self.other_business, name="Sales", category_type=Category.CategoryType.INCOME
        )
        other_subcategory = SubCategory.objects.create(
            business=self.other_business, category=other_category, name="Services",
            account_type=SubCategory.AccountType.INCOME,
        )
        Transaction.objects.create(
            business=self.other_business, date=date(2026, 5, 1), amount=Decimal("9999.00"),
            description="Private payment", subcategory=other_subcategory,
            invoice_number=other_invoice.invoice_number,
        )

        self.assertNotIn(other_invoice.invoice_number, self.rows())
