from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import CompanyProfile
from assets.models import Asset, AssetType
from core.models import Business, BusinessMembership
from ledger.models import Category, Job, SubCategory, Transaction


class JobOrderingTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Jobs Business")
        self.user = get_user_model().objects.create_user("jobs-owner", password="test-password")
        BusinessMembership.objects.create(
            business=self.business,
            user=self.user,
            role=BusinessMembership.Role.OWNER,
        )
        CompanyProfile.objects.create(
            business=self.business,
            created_by=self.user,
            company_name="Jobs Business",
        )
        self.client.force_login(self.user)

    def create_job(self, *, seq, year=2026, active=True, label=None, prefix="CLIENT"):
        job = Job.objects.create(
            business=self.business,
            label=label or f"Job {seq}",
            job_year=year,
            is_active=active,
        )
        Job.objects.filter(pk=job.pk).update(
            job_seq=seq,
            job_number=f"{prefix}-{str(year)[-2:]}{seq:04d}" if seq else f"GENERAL-{year}",
        )
        job.refresh_from_db()
        return job

    def response_job_numbers(self, response):
        return [job.job_number for job in response.context["jobs"]]

    def test_list_orders_by_status_year_and_descending_sequence(self):
        seq_16 = self.create_job(seq=16, prefix="ZZZ")
        seq_18 = self.create_job(seq=18, prefix="AAA")
        seq_17 = self.create_job(seq=17, prefix="MMM")
        general = self.create_job(seq=0, label="General")
        older = self.create_job(seq=99, year=2025)
        inactive = self.create_job(seq=100, active=False)

        response = self.client.get(reverse("ledger:job_list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["jobs"]),
            [seq_18, seq_17, seq_16, general, older, inactive],
        )

    def test_search_and_filters_preserve_newest_first_ordering(self):
        seq_16 = self.create_job(seq=16, label="Survey", prefix="ZZZ")
        seq_18 = self.create_job(seq=18, label="Survey", prefix="AAA")
        seq_17 = self.create_job(seq=17, label="Survey", prefix="MMM")
        Job.objects.filter(pk__in=[seq_16.pk, seq_17.pk, seq_18.pk]).update(
            job_type=Job.JobType.MAPPING
        )
        self.create_job(seq=19, label="Photography")

        response = self.client.get(
            reverse("ledger:job_list"),
            {"q": "Survey", "job_type": Job.JobType.MAPPING, "active": "1"},
        )

        self.assertEqual(
            self.response_job_numbers(response),
            [seq_18.job_number, seq_17.job_number, seq_16.job_number],
        )

    def test_pagination_preserves_newest_first_ordering(self):
        for seq in range(1, 28):
            self.create_job(seq=seq)

        first_page = self.client.get(reverse("ledger:job_list"))
        second_page = self.client.get(reverse("ledger:job_list"), {"page": 2})

        self.assertEqual(self.response_job_numbers(first_page)[0], "CLIENT-260027")
        self.assertEqual(self.response_job_numbers(first_page)[-1], "CLIENT-260003")
        self.assertEqual(
            self.response_job_numbers(second_page),
            ["CLIENT-260002", "CLIENT-260001"],
        )

    def test_csv_export_uses_newest_first_ordering(self):
        self.create_job(seq=16, prefix="ZZZ")
        self.create_job(seq=18, prefix="AAA")
        self.create_job(seq=17, prefix="MMM")

        response = self.client.get(reverse("exports:jobs_csv"))
        rows = response.content.decode("utf-8").splitlines()

        self.assertEqual(response.status_code, 200)
        self.assertIn("AAA-260018", rows[1])
        self.assertIn("MMM-260017", rows[2])
        self.assertIn("ZZZ-260016", rows[3])


class TransactionAssetCreationTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Ledger Business")
        self.other_business = Business.objects.create(name="Other Ledger Business")
        self.user = get_user_model().objects.create_user("ledger-owner", password="test-password")
        BusinessMembership.objects.create(business=self.business, user=self.user, role=BusinessMembership.Role.OWNER)
        CompanyProfile.objects.create(business=self.business, created_by=self.user, company_name="Ledger Business")
        self.category = Category.objects.create(
            business=self.business, name="Equipment", category_type=Category.CategoryType.EXPENSE
        )
        self.equipment = SubCategory.objects.create(
            business=self.business, category=self.category, name="Equipment purchases",
            account_type=SubCategory.AccountType.EXPENSE, is_capitalizable=True,
        )
        self.office = SubCategory.objects.create(
            business=self.business, category=self.category, name="Office supplies",
            account_type=SubCategory.AccountType.EXPENSE,
        )
        self.asset_type = AssetType.objects.create(business=self.business, name="Equipment", slug="equipment")
        self.other_asset_type = AssetType.objects.create(
            business=self.other_business, name="Equipment", slug="equipment"
        )
        self.client.force_login(self.user)

    def transaction_data(self, subcategory=None, **overrides):
        data = {
            "date": "2026-08-27", "amount": "49.95",
            "subcategory": (subcategory or self.equipment).pk,
            "description": "Propeller adapters", "transport_type": "", "notes": "",
        }
        data.update(overrides)
        return data

    def test_equipment_expense_does_not_create_asset_without_opt_in(self):
        response = self.client.post(reverse("ledger:transaction_create"), self.transaction_data())
        self.assertRedirects(response, reverse("ledger:transaction_list"))
        ledger_transaction = Transaction.objects.get()
        self.assertEqual(ledger_transaction.trans_type, Transaction.TransactionType.EXPENSE)
        self.assertIsNone(ledger_transaction.asset_id)
        self.assertFalse(Asset.objects.exists())

    def test_explicit_fixed_asset_choice_creates_scoped_asset(self):
        response = self.client.post(
            reverse("ledger:transaction_create"),
            self.transaction_data(description="Camera drone", amount="1299.00",
                                  create_equipment_asset="on", equipment_asset_type=self.asset_type.pk),
        )
        self.assertRedirects(response, reverse("ledger:transaction_list"))
        asset = Transaction.objects.select_related("asset").get().asset
        self.assertEqual(asset.business, self.business)
        self.assertEqual(asset.asset_type, self.asset_type)
        self.assertEqual(asset.purchase_date, date(2026, 8, 27))
        self.assertEqual(asset.placed_in_service_date, date(2026, 8, 27))
        self.assertEqual(asset.purchase_price, Decimal("1299.00"))

    def test_normal_non_equipment_expense_does_not_create_asset(self):
        response = self.client.post(
            reverse("ledger:transaction_create"), self.transaction_data(self.office, description="Printer paper")
        )
        self.assertRedirects(response, reverse("ledger:transaction_list"))
        self.assertFalse(Asset.objects.exists())

    def test_editing_existing_transaction_does_not_duplicate_asset(self):
        self.client.post(
            reverse("ledger:transaction_create"),
            self.transaction_data(create_equipment_asset="on", equipment_asset_type=self.asset_type.pk),
        )
        ledger_transaction = Transaction.objects.get()
        response = self.client.post(
            reverse("ledger:transaction_update", args=[ledger_transaction.pk]),
            self.transaction_data(description="Updated equipment", asset=ledger_transaction.asset_id),
        )
        self.assertRedirects(response, reverse("ledger:transaction_list"))
        self.assertEqual(Asset.objects.count(), 1)
        ledger_transaction.refresh_from_db()
        self.assertEqual(ledger_transaction.asset_id, Asset.objects.get().pk)

    def test_other_business_asset_type_is_rejected(self):
        response = self.client.post(
            reverse("ledger:transaction_create"),
            self.transaction_data(create_equipment_asset="on", equipment_asset_type=self.other_asset_type.pk),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(Asset.objects.exists())

    def test_asset_failure_rolls_back_transaction(self):
        with patch("ledger.services.Asset.full_clean", side_effect=ValidationError("bad asset")):
            with self.assertRaises(ValidationError):
                self.client.post(
                    reverse("ledger:transaction_create"),
                    self.transaction_data(create_equipment_asset="on", equipment_asset_type=self.asset_type.pk),
                )
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(Asset.objects.exists())
