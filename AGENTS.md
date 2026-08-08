# Suite — Codex Project Instructions

## Project identity

Suite is a Django 5.2 application for small-business operations and drone-business workflows. It combines accounting/business features with FlightPlan-related flight logging, pilot, and operations features.

- Python: 3.12
- Django: 5.2.x
- Django project package: `project`
- Development settings: `project.settings.dev`
- Production settings: `project.settings.prod`
- Root templates: `templates/`
- Root static files: `static/`
- Database: SQLite is supported for local development; production uses `DATABASE_URL` (PostgreSQL).
- UI: mobile-first Bootstrap 5.
- Default timezone: `America/Indiana/Indianapolis`.

## Important architecture

The application is multi-tenant. Business-owned records must stay scoped to the active business.

- `core.Business` and `core.BusinessMembership` define business tenancy.
- `core.BusinessOwnedModelMixin` is the normal ownership base for business data.
- `core.middleware.ActiveBusinessMiddleware` establishes the active business for requests.
- Never weaken or bypass business scoping to make a query, view, form, export, import, API endpoint, or test pass.
- When adding a business-owned model, prefer the existing ownership patterns unless there is a documented reason not to.

Current local apps include:

- `accounts` — invitation/auth/company profile behavior
- `core` — businesses, memberships, shared services, exports, backups
- `dashboard` — primary dashboard/navigation
- `ledger` — transactions, jobs, contacts, teams, recurring expenses
- `reports` — financial/tax reporting
- `invoices` — invoicing and PDF/email workflows
- `vehicles` — vehicles and mileage/reporting
- `contractor` — contractors, W-9, 1099 workflows
- `assets` — business assets
- `documents` — document management
- `flightlogs` — flight log storage/import functionality
- `pilot` — pilot profile/training data
- `operations` — flight/operations planning
- `helpcenter` — help content

## Development rules

1. Follow Django 5.2 conventions and prefer Django-native solutions over custom framework-like abstractions.
2. Keep changes focused. Do not refactor unrelated code while implementing a feature or bug fix.
3. Preserve existing URL names, model field semantics, migrations, and template contracts unless the task specifically requires changing them.
4. Never edit an existing applied migration to represent a new schema change. Create a new migration.
5. Before creating a migration, run `python manage.py makemigrations --check --dry-run` when practical to understand pending model changes.
6. Use `select_related` / `prefetch_related` for list/detail views when they clearly prevent N+1 queries.
7. Validate ownership on object lookups. Do not fetch a business-owned object by primary key alone when a business-scoped lookup is required.
8. Use Django forms/model forms and server-side validation for user input. Client-side validation may supplement but not replace it.
9. Use Django's escaping and CSRF protections. Do not introduce `mark_safe`, raw HTML, or disabled CSRF checks without a documented necessity.
10. Keep secrets and credentials out of source, tests, logs, prompts, fixtures, documentation, and generated patches.

## FlightPlan / flight log rules

Flight log data is important historical user data.

- Preserve CSV import compatibility unless a task explicitly replaces it.
- New DJI log ingestion/decryption support must feed the same normalized flight-log domain model where practical rather than creating parallel duplicate flight records.
- Imports should be idempotent where possible and should detect duplicates safely.
- Never silently discard source fields during import work. If fields cannot be mapped, document or retain them where the existing design permits.
- Keep import/decryption concerns separated from normalized persistence and reporting logic.
- Never embed DJI developer credentials or per-file decryption keys in the repository.

## UI rules

- Design mobile-first, then enhance for tablet/desktop.
- Continue using Bootstrap 5 and the existing global templates/styles unless the task calls for a redesign.
- Reuse existing shared partials and icon conventions before creating duplicates.
- Maintain accessible labels, useful button text, keyboard-friendly controls, and meaningful empty/error states.
- Avoid fixed-width layouts that break on phones.

## Testing and verification

For code changes, run the narrowest relevant tests first, then broader checks when practical.

Typical commands:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test <app_name>
python manage.py test
```

For syntax-only validation when dependencies or services are unavailable:

```bash
python -m compileall -q .
```

If a command cannot run because of unavailable credentials, network access, external services, or missing local dependencies, report that explicitly; do not fake a passing result.

## Database and data safety

- Do not delete, truncate, rewrite, or bulk-modify production/user data unless the user explicitly requests that data operation.
- Treat imports, migrations, finance records, invoices, flight logs, and uploaded documents as potentially production-sensitive.
- Prefer reversible migrations and transactional data operations.
- Never use the bundled local database or dumps as a reason to infer production data behavior.

## Files Codex should not treat as authoritative project guidance

Some historical notes under `docs/` describe older MoneyPro states and may be stale. Use the current code, migrations, settings, and this `AGENTS.md` as the source of truth. Historical notes can be consulted for background only.

In particular, do not copy credentials or deployment secrets found in historical documents. If secrets are encountered, flag them without reproducing their values.

## Code review rules

When reviewing changes, prioritize:

1. Cross-business data exposure or missing business scoping.
2. Destructive migrations or data-loss risks.
3. Authentication/authorization regressions.
4. Duplicate or corrupt flight-log/import behavior.
5. Financial calculation/reporting regressions.
6. Broken mobile layouts or inaccessible forms.
7. Missing tests for important behavior changes.

Do not focus review comments on cosmetic formatting unless it causes a functional, accessibility, or maintainability problem.

## Working style for Codex

- Inspect relevant models, forms, views, URLs, templates, services, migrations, and tests before editing.
- State assumptions when the code is ambiguous.
- Prefer a small complete patch over a broad speculative rewrite.
- After edits, summarize changed files, tests/checks run, and any remaining risks.
- Do not commit, push, deploy, rotate credentials, or modify production services unless explicitly asked.
