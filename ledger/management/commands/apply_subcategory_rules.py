from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ledger.models import SubCategory


DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "data" / "subcategory_rules.json"

RULE_FIELDS = [
    "account_type",
    "requires_asset",
    "requires_receipt",
    "requires_team",
    "requires_job",
    "requires_invoice_number",
    "requires_contact",
    "contact_role",
    "requires_transport",
    "requires_vehicle",
    "is_capitalizable",
]


def _b(value) -> bool:
    return bool(value)


class Command(BaseCommand):
    help = "Apply SubCategory rules from a JSON rules file using slug-only matching."

    def add_arguments(self, parser):
        parser.add_argument("--business-id", type=int, required=True)
        parser.add_argument(
            "--rules",
            type=str,
            default=str(DEFAULT_RULES_PATH),
         help="Path to subcategory_rules.json (defaults to ledger/data/subcategory_rules.json)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show changes without saving.",
        )

    def handle(self, *args, **options):
        business_id: int = options["business_id"]
        rules_path = Path(options["rules"]).expanduser().resolve()
        dry_run: bool = options["dry_run"]

        if not rules_path.exists():
            raise CommandError(f"Rules file not found: {rules_path}")

        try:
            data = json.loads(rules_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON in rules file: {rules_path}") from exc

        rules = data.get("rules")
        if not isinstance(rules, dict) or not rules:
            raise CommandError("Rules JSON must contain a non-empty 'rules' object.")

        business_qs = SubCategory.objects.filter(business_id=business_id)

        updated = 0
        unchanged = 0
        missing: list[str] = []

        with transaction.atomic():
            for rule_slug, rule in sorted(rules.items()):
                if not isinstance(rule, dict):
                    raise CommandError(f"Rule for slug '{rule_slug}' must be an object.")

                sc = business_qs.filter(slug=rule_slug).first()

                if not sc:
                    missing.append(rule_slug)
                    continue

                before = {field: getattr(sc, field, None) for field in RULE_FIELDS}
                update_fields: list[str] = []

                # account_type
                if "account_type" in rule:
                    new_value = str(rule["account_type"]).lower()
                    if sc.account_type != new_value:
                        sc.account_type = new_value
                        update_fields.append("account_type")

                # simple booleans
                boolean_fields = [
                    "requires_asset",
                    "requires_receipt",
                    "requires_team",
                    "requires_job",
                    "requires_invoice_number",
                    "requires_contact",
                    "requires_transport",
                    "requires_vehicle",
                    "is_capitalizable",
                ]
                for field in boolean_fields:
                    if field in rule and hasattr(sc, field):
                        new_value = _b(rule[field])
                        if getattr(sc, field) != new_value:
                            setattr(sc, field, new_value)
                            update_fields.append(field)

                # contact_role
                if "contact_role" in rule and hasattr(sc, "contact_role"):
                    new_value = str(rule["contact_role"]).lower()
                    if sc.contact_role != new_value:
                        sc.contact_role = new_value
                        update_fields.append("contact_role")

                after = {field: getattr(sc, field, None) for field in RULE_FIELDS}

                if not update_fields:
                    unchanged += 1
                    continue

                updated += 1

                if dry_run:
                    self.stdout.write(
                        self.style.WARNING(
                            f"[DRY-RUN] Would update: {sc.name} (slug={sc.slug})\n"
                            f"  {before} -> {after}"
                        )
                    )
                else:
                    sc.full_clean()
                    sc.save(update_fields=update_fields)

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS("\nSubCategory rules applied."))
        self.stdout.write(f"Updated: {updated}")
        self.stdout.write(f"Unchanged: {unchanged}")
        self.stdout.write(f"Missing in DB: {len(missing)}")
        if missing:
            self.stdout.write("  " + ", ".join(missing[:25]) + (" ..." if len(missing) > 25 else ""))