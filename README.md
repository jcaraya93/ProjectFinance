# Project Finance

A personal finance web application for importing bank statements, classifying transactions, and visualizing spending patterns. Built with Django, PostgreSQL, Bootstrap 5, and Chart.js.

## Features

- **Statement Import** — Upload credit card (Credit-2918) and debit card (Debit-2651) CSV files with auto-detection, SHA-256 duplicate prevention, and multi-file upload support.
- **Dual Currency** — Handles CRC (Costa Rican Colón) and USD with automatic exchange rate conversion.
- **Transaction Management** — List, filter, sort, search, edit, split/unsplit transactions. Bulk category assignment. Inline category editing.
- **Rule-Based Classification** — Auto-classify transactions by description keywords, account type, metadata fields, and amount ranges. Rules stored in the database.
- **Dashboards** — Spending/income trends, category breakdowns, car cost analysis (gas, parking), salary tracking, and chart comparisons.
- **Expense Comparisons** — Compare a selected month, quarter, semester, or year with the median expense total across all calendar periods of that size with data, including partial and selected periods. Rolling Last 3/6/12 Months options use the median calendar quarter/semester/year respectively; All Time has no median comparison.
- **Expense Range** — View monthly expense ranges over All Time, a calendar Year, or Last 12 Months through today (the default), with a selected month for comparison. Select category Level 1 (top level, the default) or Level 2; deeper categories roll up before monthly statistics are calculated. Rolling ranges include only transactions within their date boundaries, including partial months.
- **Categories & Rules CRUD** — Full management interface for category groups, categories, and classification rules. Category rows show clickable counts of directly assigned logical transactions (including split transactions), opening the Transactions page with that category selected and subcategories excluded. The Transactions page can toggle direct-only filtering inside the Categories dropdown; All/None changes category selections without changing this toggle. Ordinary category filters still include subcategories.
- **Transaction drill-down navigation** — Filtered transaction links from dashboards, Categories, and Statements include a named back link that restores the originating page and its filters. The link survives transaction filtering, sorting, pagination, and clearing filters; direct visits to Transactions show no back link.

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
│   │   ├── import_service.py         # Statement import orchestration
│   │   ├── exchange_rates.py         # CRC↔USD rate fetching & conversion
│   │   └── stats.py                  # Dashboard aggregation queries
│   ├── management/commands/
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
└── CategoryNode                                           ├── amount, amount_crc, amount_usd
    ├── user, name, color                                  ├── category_v2 → CategoryNode
    ├── parent → CategoryNode (any depth)                  ├── classification_method_v2
    └── ClassificationRuleV2                               └── matched_rule_v2 → ClassificationRuleV2
        ├── description (keyword match)
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
- **LogicalTransaction** — Mutable, derived record for classification and analysis. One raw transaction can have multiple logical transactions (splits). This is the main model used for filtering, dashboards, and reporting. It carries the classification fields `category_v2` → `CategoryNode`, `matched_rule_v2` → `ClassificationRuleV2` and `classification_method_v2`.
- **CategoryNode** — Hierarchical category. Parent and child nodes share a user and group; names are unique per user and group. Managed from the Categories page (`/categories-v2/`). Each group has a protected top-level `Unclassified` node. New users are loaded with the starter tree in `core/data/default_categories.json`.
- **ClassificationRuleV2** — Conditions (description substring, account type, metadata key-value, amount range) that map to a `CategoryNode`; phase ordering is transfer → specific → Unclassified. `core/services/rules_v2.py` provides `find_matching_rule` and `classify_transactions_v2`; `python manage.py classify_v2 <email> [--dry-run]` applies rules, skipping `manual` transactions.

### Classification Lifecycle

Each `LogicalTransaction` has a `classification_method_v2` field:

| Method | Meaning |
|--------|---------|
| `unclassified` | No classification applied. Category is Unclassified. |
| `rule` | Auto-classified by a matching `ClassificationRuleV2`. `matched_rule_v2` is set. |
| `manual` | Manually assigned by user. `matched_rule_v2` is cleared. |

**Transitions:**
- On import → `unclassified`
- After rule engine runs → `rule` (if matched)
- User changes category (single or bulk) → `manual`
- Rule conditions edited → linked transactions reset to `unclassified`
- Rule target category changed → linked transactions move to new category
- Rule deleted → linked transactions reset to `unclassified`

## Setup

The project supports three operation modes:

| Mode | Database | Settings | Use case |
|------|----------|----------|----------|
| **Local** | SQLite | `config.settings_local` | Fast dev — no Docker needed |
| **Docker** | PostgreSQL 17 | `config.settings` | Full-stack local environment |
| **Production** | PostgreSQL 17 | `config.settings` | VM or Azure Container Apps deployment |

### Prerequisites (all modes)

- Python 3.12+ and a `.env` file in the project root (see [Environment Variables](docs/infrastructure/deploy-docker/README.md#environment-variables))

### Local (SQLite)

No Docker required. Uses Django's dev server and a file-based SQLite database. For full details see [docs/infrastructure/deploy-local/README.md](docs/infrastructure/deploy-local/README.md).

```bash
# Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Run migrations and start the dev server
python manage.py migrate --settings=config.settings_local
python manage.py runserver --settings=config.settings_local
```

Or set the env var once per shell session to skip the `--settings` flag:

```bash
set DJANGO_SETTINGS_MODULE=config.settings_local        # Windows
# export DJANGO_SETTINGS_MODULE=config.settings_local   # macOS/Linux
python manage.py migrate
python manage.py runserver
```

The app will be available at `http://localhost:8000/`.

### Docker (PostgreSQL)

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine + Compose plugin).

```bash
# Start the full stack (builds the Django image on first run)
docker compose up -d --build

# Verify both services are running
docker compose ps
```

The entrypoint script automatically waits for PostgreSQL, runs migrations, collects static files, and starts Gunicorn.

The app will be available at `http://localhost:8000/`.

### Common Commands

```bash
# View logs
docker compose logs -f web

# Create a superuser
docker compose exec web python manage.py createsuperuser              # Docker
python manage.py createsuperuser --settings=config.settings_local     # Local

# Run any management command
docker compose exec web python manage.py <command>

# Rebuild after code changes
docker compose up -d --build

# Reset the database
docker compose down -v && docker compose up -d                        # Docker
del db.sqlite3 && python manage.py migrate --settings=config.settings_local  # Local (Windows)
```

For full deployment details see:
- [Local (SQLite)](docs/infrastructure/deploy-local/README.md)
- [Docker (PostgreSQL)](docs/infrastructure/deploy-docker/README.md)
- [Azure Simple (Single VM)](docs/infrastructure/deploy-azure-simple/azure-deploy-simple.md)
- [Azure Complex (Container Apps)](docs/infrastructure/deploy-azure-complex/azure-deploy-complex.md)

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
| `python manage.py rename_app_prep` | Migration helper to update `django_migrations` table after the app rename from `transactions` to `core`. |

## URL Routes

### Dashboards
| URL | Description |
|-----|-------------|
| `/` | Main dashboard |
| `/spending-income/` | Expense Composition dashboard |
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
