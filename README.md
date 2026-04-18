# Project Finance

A personal finance web application for importing bank statements, classifying transactions, and visualizing spending patterns. Built with Django, PostgreSQL, Bootstrap 5, and Chart.js.

## Features

- **Statement Import** — Upload credit card (Credit-2918) and debit card (Debit-2651) CSV files with auto-detection, SHA-256 duplicate prevention, and multi-file upload support.
- **Dual Currency** — Handles CRC (Costa Rican Colón) and USD with automatic exchange rate conversion.
- **Transaction Management** — List, filter, sort, search, edit, split/unsplit transactions. Bulk category assignment. Inline category editing.
- **Rule-Based Classification** — Auto-classify transactions by description keywords, account type, metadata fields, and amount ranges. Rules stored in the database with YAML sync.
- **Dashboards** — Spending/income trends, category breakdowns, car cost analysis (gas, parking), salary tracking, and chart comparisons.
- **Categories & Rules CRUD** — Full management interface for category groups, categories, and classification rules.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Django 6+ / Python 3 |
| Database | PostgreSQL 17 |
| Frontend | Bootstrap 5 (Flatly theme) + Chart.js 4 |
| CSS | Custom stylesheet + Bootswatch |

## Project Structure

```
ProjectFinance/
├── config/                           # Django project configuration
│   ├── settings.py
│   ├── settings_test.py
│   ├── urls.py
│   ├── wsgi.py
│   ├── asgi.py
│   ├── observability.py              # OpenTelemetry bootstrap
│   └── logging_fmt.py
├── core/                             # Main Django app
│   ├── models.py                     # 13 data models
│   ├── views/                        # View modules (~2,130 lines total)
│   │   ├── dashboards.py             # Dashboard views
│   │   ├── transactions.py           # Transaction CRUD views
│   │   ├── rules.py                  # Classification rule views
│   │   ├── categories.py             # Category management views
│   │   ├── statements.py             # Statement import/list views
│   │   └── _helpers.py               # Shared view utilities
│   ├── forms.py                      # Upload, category, rule forms
│   ├── urls.py                       # 33 URL routes
│   ├── admin.py                      # Django admin registration
│   ├── auth_views.py                 # Login, register, logout views
│   ├── auth_urls.py                  # Authentication URL routing
│   ├── backends.py                   # Authentication backends
│   ├── parsers/
│   │   ├── base.py                   # Base parser interface & data classes
│   │   ├── credit_card.py            # Credit-2918 CSV parser
│   │   └── debit_card.py             # Debit-2651 CSV parser
│   ├── services/
│   │   ├── classifier.py             # Classification entry point
│   │   ├── yaml_classifier.py        # Rule matching engine (reads from DB)
│   │   ├── ai_classifier.py          # AI-assisted classification
│   │   ├── import_service.py         # Statement import orchestration
│   │   ├── exchange_rates.py         # CRC↔USD rate fetching & conversion
│   │   └── stats.py                  # Dashboard aggregation queries
│   ├── management/commands/
│   │   ├── seed_categories.py        # Import categories & rules from YAML
│   │   ├── export_rules.py           # Export DB rules back to YAML
│   │   ├── ai_classify.py            # AI-assisted bulk classification
│   │   └── rename_app_prep.py        # Migration helper (transactions → core)
│   ├── templates/core/               # 22 HTML templates (+ 2 auth templates)
│   ├── static/core/                  # CSS and JS assets
│   └── templatetags/
│       └── finance_filters.py        # Custom template filters
├── docker/                           # Docker entrypoint and scripts
├── docs/                             # Architecture and deployment docs
├── infra/                            # Infrastructure configuration
├── requirements.txt
├── manage.py
└── .gitignore
```

## Data Model

```
User (custom, email-based)            Account (base)
├── email                             ├── CreditAccount (card_number)
└── UserPreference                    └── DebitAccount (iban)
    └── transaction_columns                └── StatementImport
                                               └── CurrencyLedger (CRC|USD)
CategoryGroup                                      └── RawTransaction (immutable)
├── slug: expense|income|                              └── LogicalTransaction (1:N)
│         transfer|unclassified                            ├── description
└── Category                                               ├── amount, amount_crc, amount_usd
    ├── name, color                                        ├── category → Category
    └── ClassificationRule                                 ├── classification_method
        ├── description (keyword match)                    └── matched_rule → ClassificationRule
        ├── account_type
        ├── metadata (JSON conditions)
        ├── amount_min/max
        └── detail

ExchangeRate
├── date
└── usd_to_crc
```

### Key Model Relationships

- **RawTransaction** — Immutable record imported from the bank statement. Never modified after import.
- **LogicalTransaction** — Mutable, derived record for classification and analysis. One raw transaction can have multiple logical transactions (splits). This is the main model used for filtering, dashboards, and reporting.
- **ClassificationRule** — Defines conditions (description substring, account type, metadata key-value, amount range) that map to a target category. Used by the rule engine to auto-classify transactions.

### Classification Lifecycle

Each `LogicalTransaction` has a `classification_method` field:

| Method | Meaning |
|--------|---------|
| `unclassified` | No classification applied. Category is Default. |
| `rule` | Auto-classified by a matching `ClassificationRule`. `matched_rule` is set. |
| `manual` | Manually assigned by user. `matched_rule` is cleared. |

**Transitions:**
- On import → `unclassified`
- After rule engine runs → `rule` (if matched)
- User changes category (single or bulk) → `manual`
- Rule conditions edited → linked transactions reset to `unclassified`
- Rule target category changed → linked transactions move to new category
- Rule deleted → linked transactions reset to `unclassified`

## Setup

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine + Compose plugin)
- A `.env` file in the project root (see [Environment Variables](docs/infrastructure/deploy-local/README.md#environment-variables))

### Quick Start

```bash
# Clone the repository
git clone <repo-url>
cd ProjectFinance

# Start the full stack (builds on first run)
docker compose up -d --build

# Verify both services are running
docker compose ps

# (Optional) Seed categories and classification rules
docker compose exec web python manage.py seed_categories
```

The entrypoint script automatically waits for PostgreSQL, runs migrations, collects static files, and starts Gunicorn.

The app will be available at `http://localhost:8000/`.

### Common Commands

```bash
# View logs
docker compose logs -f web

# Create a superuser
docker compose exec web python manage.py createsuperuser

# Run any management command
docker compose exec web python manage.py <command>

# Rebuild after code changes
docker compose up -d --build

# Reset the database
docker compose down -v && docker compose up -d
```

For full local deployment details see [docs/infrastructure/deploy-local/README.md](docs/infrastructure/deploy-local/README.md).

### Importing Data

Navigate to Statements → Import Statement, then upload one or more CSV files. The parser is auto-detected.

## CSV Formats

### Credit Card

- Row 1-2: Account header (card number, holder, dates)
- Row 3: Transaction column headers
- Row 4: Previous balance
- Row 5: Sub-card balance
- Rows 6+: Transactions (Date, Description, Local CRC amount, USD amount)
- Footer: Interest, rates, points, final balance

Transactions are split by currency: if Local > 0 → CRC transaction; if Dollars > 0 → USD transaction.

### Debit Card

- Header: Client info (IBAN, client number)
- Transactions: Date, Reference, Transaction Code, Description, Debit, Credit, Balance
- Summary section at end

Metadata fields `transaction_code` and `reference_number` are extracted per transaction.

## Management Commands

| Command | Description |
|---------|-------------|
| `python manage.py seed_categories` | Import categories, groups, and rules from `classification_rules.yaml` into the database. Only imports rules if the DB has none. |
| `python manage.py export_rules` | Export current DB rules back to `classification_rules.yaml`. |
| `python manage.py ai_classify` | Classify unclassified transactions using Google Gemini AI. Supports `--dry-run`. |
| `python manage.py rename_app_prep` | Migration helper to update `django_migrations` table after the app rename from `transactions` to `core`. |

## URL Routes

### Dashboards
| URL | Description |
|-----|-------------|
| `/` | Main dashboard |
| `/spending-income/` | Spending vs income dashboard |
| `/chart-comparison/` | Chart comparison tool |
| `/car/` | Car costs overview |
| `/car/gas/` | Gas expenses dashboard |
| `/car/parking/` | Parking expenses dashboard |
| `/income/salary/` | Salary income dashboard |
| `/transaction-health/` | Transaction health dashboard |
| `/rule-matching/` | Rule matching dashboard |
| `/default-buckets/` | Default buckets dashboard |

### Transactions
| URL | Description |
|-----|-------------|
| `/transactions/` | Transaction list with filters, sorting, bulk actions |
| `/transactions/<id>/edit/` | Edit a transaction (description, category, split) |
| `/transactions/<id>/split/` | Split a transaction into sub-transactions |
| `/transactions/<id>/unsplit/` | Unsplit a previously split transaction |
| `/transactions/bulk-update-category/` | Bulk category assignment |

### Statements
| URL | Description |
|-----|-------------|
| `/upload/` | Upload CSV files |
| `/upload/file/` | File upload API endpoint |
| `/statements/` | List imported statements |
| `/statements/purge/` | Purge all imported data |

### Categories & Rules
| URL | Description |
|-----|-------------|
| `/categories/` | Category management |
| `/categories/add/` | Add a new category |
| `/categories/delete/` | Delete a category |
| `/categories/rename/` | Rename a category |
| `/categories/export/` | Export categories |
| `/categories/import/` | Import categories |
| `/rules/` | Classification rules list |
| `/rules/add/` | Add a new rule |
| `/rules/<id>/edit/` | Edit a rule |
| `/rules/<id>/delete/` | Delete a rule |
| `/rules/reclassify/` | Re-run all rules on non-manual transactions |
| `/rules/classify-unclassified/` | Run rules only on unclassified transactions |

### User Preferences
| URL | Description |
|-----|-------------|
| `/preferences/transaction-columns/` | Save transaction column visibility |

### Authentication
| URL | Description |
|-----|-------------|
| `/auth/login/` | User login |
| `/auth/register/` | User registration |
| `/auth/logout/` | User logout |

## Transaction List Features

- **Filters** — Date range (with presets), account/wallet, classification group & category, method, split status, amount range
- **Advanced search** — Transaction code, reference number, rule ID, statement ID
- **Sorting** — Click column headers to sort by date, account, method, group, category, description, or amount
- **Column visibility** — Toggle columns on/off (persisted in localStorage)
- **Inline category edit** — Click a category name to change it via dropdown
- **Bulk selection** — Checkbox per row + select-all, with sticky bottom bar for bulk category assignment
- **Edit page** — Edit description, category; split into multiple sub-transactions or unsplit back
