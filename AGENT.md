# Agent Instructions — ProjectFinance

## Project Overview

ProjectFinance is a personal finance web application for importing Costa Rican bank statements (credit & debit CSV), classifying transactions via rules, and visualizing spending patterns. Built for a single-user/small-team workflow.

**Tech stack:** Django 6+ · Python 3.12+ · PostgreSQL 17 (production) · SQLite (dev/test) · Bootstrap 5 (Flatly/Bootswatch) · Chart.js 4 · OpenTelemetry · Gunicorn · WhiteNoise · Docker Compose.

---

## Quick Reference

| What | Command |
|------|---------|
| **Run tests** | `pytest core/tests/ -v --tb=short` |
| **Run a single test file** | `pytest core/tests/test_parsers.py -v` |
| **Dev server (Local)** | `python manage.py runserver --settings=config.settings_local` |
| **Dev server (Docker)** | `docker compose up -d --build` |
| **Migrations** | `python manage.py makemigrations core && python manage.py migrate` |
| **Seed categories** | `python manage.py seed_categories` |

### Environment Variables (tests)

Tests require these env vars (CI sets them automatically):

```
DJANGO_SETTINGS_MODULE=config.settings_test
DJANGO_SECRET_KEY=test-secret-key-for-ci
OTEL_EXPORTER=console
```

---

## Project Structure

```
ProjectFinance/
├── config/                     # Django project settings
│   ├── settings.py             # Production settings (PostgreSQL)
│   ├── settings_local.py       # Local settings (SQLite file)
│   ├── settings_test.py        # Test settings (SQLite :memory:)
│   ├── urls.py                 # Root URL conf (auth + core)
│   ├── observability.py        # OpenTelemetry bootstrap
│   └── logging_fmt.py          # Log formatter with trace context
├── core/                       # Main (and only) Django app
│   ├── models.py               # All data models (see below)
│   ├── views/                  # View modules
│   │   ├── __init__.py         # Re-exports all views
│   │   ├── dashboards.py       # 11 dashboard views
│   │   ├── transactions.py     # Transaction CRUD + bulk ops
│   │   ├── statements.py       # Statement import + list
│   │   ├── categories.py       # Category management
│   │   ├── rules.py            # Classification rule CRUD
│   │   ├── account.py          # Account page, data export/import
│   │   └── _helpers.py         # Shared utilities (safe redirect, category prefetch)
│   ├── forms.py                # Django forms (upload, category, rule)
│   ├── filters.py              # django-filter FilterSet for transactions
│   ├── urls.py                 # ~50 URL routes (app_name = 'core')
│   ├── admin.py                # Django admin registrations
│   ├── auth_views.py           # Login, register, logout views
│   ├── auth_urls.py            # Auth URL routing
│   ├── backends.py             # Email-based auth backend
│   ├── ratelimit.py            # Decorator-based rate limiter using Django cache
│   ├── instrumentation.py      # OTel tracer, meter, and metric definitions
│   ├── parsers/
│   │   ├── base.py             # ParsedTransaction, ParsedLedger, ParsedStatement, BaseParser
│   │   ├── credit_card.py      # Credit card CSV parser
│   │   └── debit_card.py       # Debit card CSV parser
│   ├── services/
│   │   ├── classifier.py       # Thin façade → yaml_classifier
│   │   ├── yaml_classifier.py  # Rule matching engine (reads ClassificationRule from DB)
│   │   ├── ai_classifier.py    # Google Gemini AI classification
│   │   ├── import_service.py   # Statement import orchestration (parse → classify → bulk write)
│   │   ├── exchange_rates.py   # CRC↔USD via Frankfurter API
│   │   ├── stats.py            # Dashboard aggregation queries
│   │   └── user_data_io.py     # Full data export/import (JSON)
│   ├── management/commands/    # seed_categories, ai_classify
│   ├── templates/core/         # HTML templates (Bootstrap 5)
│   ├── static/core/            # CSS + JS assets
│   ├── templatetags/           # Custom template filters
│   └── tests/
│       ├── conftest.py         # Shared pytest fixtures
│       ├── factories.py        # factory_boy model factories
│       ├── fixtures/           # Sample CSV files for parser tests
│       └── test_*.py           # Test modules by feature area
├── docker/                     # Docker entrypoint scripts
├── docs/                       # Architecture and deployment docs
│   ├── infrastructure/         # Deployment guides (Local, Docker, Azure)
│   ├── future-ideas/           # Enhancement proposals
│   └── benchmark/              # Import performance benchmarks
├── infra/                      # Azure Bicep templates
├── .github/workflows/
│   ├── tests.yml               # CI: pytest on push/PR to main
│   └── deploy.yml              # CD: deployment workflow
├── requirements.txt            # Production dependencies
├── requirements-dev.txt        # Dev/test dependencies (pytest, factory-boy)
└── pytest.ini                  # Pytest config → settings_test
```

---

## Data Model

The central design revolves around an **immutable/mutable split**:

- **`RawTransaction`** — Immutable record imported directly from the bank CSV. Never modified after import.
- **`LogicalTransaction`** — Mutable, derived record used for classification and analysis. One raw transaction can produce multiple logical transactions (splits). **This is the primary model for queries, dashboards, and filtering.**

### Key Models

| Model | Purpose |
|-------|---------|
| `User` | Custom user model (email-based auth, no username) |
| `CategoryGroup` | Fixed slugs: `expense`, `income`, `transaction`, `unclassified` |
| `Category` | User-scoped. Each group has a protected `Default` category |
| `Account` → `CreditAccount` / `DebitAccount` | Bank accounts (MTI pattern) |
| `StatementImport` | One per uploaded CSV file. SHA-256 duplicate detection |
| `CurrencyLedger` | Links statement to currency (`CRC` or `USD`) with balances |
| `RawTransaction` | Immutable bank data. Has `account_metadata` JSONField |
| `LogicalTransaction` | Mutable. Has `category`, `classification_method`, `matched_rule` |
| `ClassificationRule` | Conditions → target category. Supports description, account_type, metadata, amount range |
| `ExchangeRate` | Daily USD→CRC rates from Frankfurter API |
| `UserPreference` | One-to-one with User. Stores column visibility as JSON |

### Multi-tenancy

Every model with user data has a `user` ForeignKey. **Always filter by user** in queries:

```python
LogicalTransaction.objects.filter(user=request.user)
```

### Classification Lifecycle

| Method | Meaning |
|--------|---------|
| `unclassified` | No classification. Category = Default |
| `rule` | Auto-classified by `ClassificationRule`. `matched_rule` is set |
| `manual` | User manually assigned. `matched_rule` is cleared |

Rules never override `manual` classifications. When a rule is deleted, its linked transactions reset to `unclassified`.

---

## Architecture Patterns

### Views

- All views are **function-based** with `@login_required` decorator.
- Views are organized into modules under `core/views/` and re-exported via `__init__.py`.
- Each module defines an `__all__` list.
- Use `django.contrib.messages` for user-facing feedback.
- Dangerous actions (purge, delete) use `@require_POST`.
- Rate limiting via the `@ratelimit` decorator from `core/ratelimit.py`.

### Services

Business logic lives in `core/services/`, not in views. Views are thin orchestrators.

- **`import_service.py`** — Handles the full import pipeline: detect card type → parse → duplicate check → exchange rate fetch → atomic bulk write.
- **`yaml_classifier.py`** — Rule matching engine with phase ordering (transfers → specific → fallback). Caches rules in memory.
- **`classifier.py`** — Thin façade that delegates to `yaml_classifier`.
- **`exchange_rates.py`** — Fetches from Frankfurter API, caches in `ExchangeRate` model.
- **`stats.py`** — Dashboard aggregation queries.

### Parsers

- Inherit from `BaseParser` in `core/parsers/base.py`.
- Return `ParsedStatement` dataclass containing `ParsedLedger` → `ParsedTransaction`.
- Auto-detection in `import_service.detect_card_type()`.

### Instrumentation

All OpenTelemetry metrics and the shared tracer/meter are defined in `core/instrumentation.py`. Import from there:

```python
from core.instrumentation import tracer, dashboard_duration
```

### Templates

- Bootstrap 5 with the Flatly Bootswatch theme.
- Chart.js 4 for data visualization.
- Templates live in `core/templates/core/`.
- Custom filters in `core/templatetags/finance_filters.py`.

---

## Testing

### Stack

- **pytest** + **pytest-django** with in-memory SQLite (`config.settings_test`).
- **factory_boy** for model factories (`core/tests/factories.py`).
- Fixtures in `core/tests/conftest.py` provide `user`, `auth_client`, `sample_data`, `exchange_rates`, etc.
- CSV fixtures in `core/tests/fixtures/`.

### Conventions

- Test files follow `test_<feature>.py` naming under `core/tests/`.
- The `conftest.py` autouse fixture `_mock_yaml_sync` prevents tests from writing to the real `classification_rules.yaml`.
- Use `auth_client` fixture for authenticated view tests.
- Use factories (not raw `Model.objects.create`) when building test data.
- The `sample_data` fixture provides a complete object graph: account → statement → ledger → raw → logical transactions + a rule.

### Running Tests

```bash
# All tests
pytest core/tests/ -v --tb=short

# Single file
pytest core/tests/test_parsers.py -v

# Single test
pytest core/tests/test_classifier.py::test_classify_single -v

# Parallel (if pytest-xdist is installed)
pytest core/tests/ -n auto
```

---

## Coding Conventions

### Python Style

- No class-based views — stick to function-based views with decorators.
- Type hints are used in dataclasses and service signatures but not enforced project-wide.
- Imports are organized: stdlib → Django → third-party → local (`core.*`).
- Use `logging.getLogger(__name__)` per module.
- Decimal for all monetary values. Never use float for money.

### Django Patterns

- Settings hierarchy: `settings.py` (base/prod) → `settings_local.py` (Local/SQLite override) → `settings_test.py` (test/in-memory SQLite override).
- Custom user model: `core.User` (email-based, no username).
- Auth backend: `core.backends.EmailBackend`.
- URL namespace: `core:` (e.g., `reverse('core:dashboard')`).
- All models in a single `models.py` file.
- `Transaction` is kept as an alias for `LogicalTransaction` for migration compatibility.

### Frontend

- Bootstrap 5 classes. No custom CSS framework.
- Form widgets get `form-control` / `form-select` classes applied in form definitions.
- Charts rendered client-side with Chart.js 4.

---

## Common Tasks

### Adding a New View

1. Create the view function in the appropriate module under `core/views/`.
2. Add it to that module's `__all__` list.
3. Add a URL pattern in `core/urls.py`.
4. Create the template in `core/templates/core/`.
5. Add `@login_required` decorator.
6. Write tests in `core/tests/test_views_<area>.py`.

### Adding a New Model

1. Add the model class to `core/models.py`.
2. Include a `user` ForeignKey if it holds user data.
3. Run `python manage.py makemigrations core && python manage.py migrate`.
4. Register in `core/admin.py`.
5. Create a factory in `core/tests/factories.py`.

### Adding a New Parser

1. Create a new file in `core/parsers/`.
2. Inherit from `BaseParser` and implement `parse()` → `ParsedStatement`.
3. Update `detect_card_type()` in `core/services/import_service.py`.
4. Add CSV fixtures in `core/tests/fixtures/` and tests in `test_parsers.py`.

### Adding a New Dashboard

1. Add the view function in `core/views/dashboards.py` and add to `__all__`.
2. Add query logic in `core/services/stats.py` if needed.
3. Create the template with Chart.js visualizations.
4. Add the URL in `core/urls.py`.
5. Instrument with `dashboard_duration` histogram.

---

## Warnings & Gotchas

- **Never modify `RawTransaction` records** after import. All user-facing changes go through `LogicalTransaction`.
- **Always filter by `user`** — this is a multi-tenant app. Forgetting the user filter leaks data.
- **`classification_rules.yaml`** is synced with the database. The DB is the source of truth at runtime; YAML is for import/export.
- **The `Transaction` alias** (`Transaction = LogicalTransaction`) exists for migration compatibility. Prefer `LogicalTransaction` in new code.
- **Exchange rates** come from the Frankfurter API. Tests mock this; the `exchange_rates` fixture seeds static rates.
- **`CategoryGroup` slugs are fixed** (`expense`, `income`, `transaction`, `unclassified`). Don't create new ones.
- **The `Default` category is protected** — it cannot be renamed or deleted. It serves as the fallback for unclassified transactions.
- **Dual currency** — amounts exist in original currency (`amount`), CRC (`amount_crc`), and USD (`amount_usd`). Conversion happens at import time.
