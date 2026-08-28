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
from ledger.models import Category, SubCategory, Transaction


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
