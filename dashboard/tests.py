from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import CompanyProfile
from core.models import Business, BusinessMembership
from flightlogs.models import FlightLog
from ledger.models import Category, Job, SubCategory, Transaction
from operations.models import OpsPlan


class DashboardHomeTests(TestCase):
    today = date(2026, 8, 12)

    def setUp(self):
        self.business = Business.objects.create(name="Current Business")
        self.other_business = Business.objects.create(name="Other Business")
        self.user = get_user_model().objects.create_user("owner", password="test-password")
        BusinessMembership.objects.create(
            business=self.business,
            user=self.user,
            role=BusinessMembership.Role.OWNER,
        )
        CompanyProfile.objects.create(
            business=self.business,
            created_by=self.user,
            company_name="Current Business",
        )
        self.url = reverse("dashboard:home")

        self.income_subcategory = self._subcategory(
            self.business, "Income", SubCategory.AccountType.INCOME
        )
        self.expense_subcategory = self._subcategory(
            self.business, "Expenses", SubCategory.AccountType.EXPENSE
        )
        self.other_income_subcategory = self._subcategory(
            self.other_business, "Other Income", SubCategory.AccountType.INCOME
        )

    def _subcategory(self, business, name, account_type):
        category = Category.objects.create(
            business=business,
            name=name,
            category_type=account_type,
        )
        return SubCategory.objects.create(
            business=business,
            category=category,
            name=name,
            account_type=account_type,
        )

    def _transaction(self, business, subcategory, amount, transaction_date, *, is_refund=False):
        return Transaction.objects.create(
            business=business,
            subcategory=subcategory,
            amount=Decimal(amount),
            date=transaction_date,
            description="Dashboard test",
            is_refund=is_refund,
        )

    def _ops_plan(self, business, *, year, status, waivers_required=True, suffix="plan"):
        sequence = Job.objects.filter(business=business, job_year=year).count() + 1
        job = Job.objects.create(
            business=business,
            label=f"{suffix}-{business.pk}-{year}-{status}",
            job_year=year,
            job_seq=sequence,
            job_number=f"TEST-{year}-{sequence}",
        )
        return OpsPlan.objects.create(
            business=business,
            job=job,
            plan_year=year,
            status=status,
            waivers_required=waivers_required,
        )

    def _get_dashboard(self):
        self.client.force_login(self.user)
        with mock.patch("dashboard.views.timezone.localdate", return_value=self.today):
            return self.client.get(self.url)

    def test_dashboard_requires_authentication(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account_login"), response.url)

    def test_finance_cards_are_month_business_and_refund_scoped(self):
        self._transaction(self.business, self.income_subcategory, "12450.00", self.today)
        self._transaction(self.business, self.income_subcategory, "450.00", self.today, is_refund=True)
        self._transaction(self.business, self.expense_subcategory, "4825.15", self.today)
        self._transaction(self.business, self.income_subcategory, "999.00", date(2026, 7, 31))
        self._transaction(
            self.other_business, self.other_income_subcategory, "777.00", self.today
        )

        response = self._get_dashboard()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["monthly_income"], Decimal("12000.00"))
        self.assertEqual(response.context["monthly_expenses"], Decimal("4825.15"))
        self.assertEqual(response.context["mtd_income"], 12000.0)
        self.assertContains(response, "$12,000.00")
        self.assertContains(response, "$4,825.15")

    def test_flight_operations_are_month_and_business_scoped(self):
        FlightLog.objects.create(
            business=self.business,
            flight_date=self.today,
            air_time=timedelta(hours=1, minutes=30),
            takeoff_address="100 Main St",
        )
        FlightLog.objects.create(
            business=self.business,
            flight_date=self.today,
            air_time=timedelta(minutes=45),
            takeoff_address="100 Main St",
        )
        FlightLog.objects.create(
            business=self.business,
            flight_date=self.today,
            air_time=None,
            takeoff_address="",
        )
        FlightLog.objects.create(
            business=self.business,
            flight_date=date(2026, 7, 31),
            air_time=timedelta(hours=10),
            takeoff_address="Old Location",
        )
        FlightLog.objects.create(
            business=self.other_business,
            flight_date=self.today,
            air_time=timedelta(hours=10),
            takeoff_address="Other Business",
        )

        response = self._get_dashboard()

        self.assertEqual(response.context["flights_mtd"], 3)
        self.assertEqual(response.context["flight_hours_mtd"], 2.25)
        self.assertEqual(response.context["flight_locations_mtd"], 1)
        self.assertContains(response, "2.3")

    def test_airspace_waivers_are_year_status_and_business_scoped(self):
        self._ops_plan(self.business, year=2026, status=OpsPlan.APPROVED, suffix="approved")
        self._ops_plan(self.business, year=2026, status=OpsPlan.DRAFT, suffix="draft")
        self._ops_plan(self.business, year=2026, status=OpsPlan.IN_REVIEW, suffix="review")
        self._ops_plan(self.business, year=2026, status=OpsPlan.ARCHIVED, suffix="archived")
        self._ops_plan(
            self.business,
            year=2026,
            status=OpsPlan.DRAFT,
            waivers_required=False,
            suffix="not-waiver",
        )
        self._ops_plan(self.business, year=2025, status=OpsPlan.APPROVED, suffix="prior")
        self._ops_plan(self.business, year=2027, status=OpsPlan.DRAFT, suffix="future")
        self._ops_plan(self.other_business, year=2026, status=OpsPlan.APPROVED, suffix="other")

        response = self._get_dashboard()

        self.assertEqual(response.context["approved_waivers_ytd"], 1)
        self.assertEqual(response.context["pending_waivers_ytd"], 2)
        self.assertEqual(response.context["current_year"], 2026)

    def test_dashboard_renders_summary_cards(self):
        response = self._get_dashboard()

        self.assertContains(response, "Finance Overview")
        self.assertContains(response, "Flight Operations")
        self.assertContains(response, "Airspace Management")
        self.assertContains(response, "August 2026")
        self.assertContains(response, "Equipment")
        self.assertContains(response, "Active Equipment")
