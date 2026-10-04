# Project Finance

A personal finance web application for importing bank statements, classifying transactions, and visualizing spending patterns. Built with Django, PostgreSQL, Bootstrap 5, and Chart.js.

## Features

- **Statement Import** — Upload credit card (Credit-2918) and debit card (Debit-2651) CSV files with auto-detection, SHA-256 duplicate prevention, and multi-file upload support.
- **Dual Currency** — Handles CRC (Costa Rican Colón) and USD with automatic exchange rate conversion.
- **Transaction Management** — List, filter, sort, search, edit, split/unsplit transactions. Bulk category assignment. Inline category editing.
- **Rule-Based Classification** — Auto-classify transactions by description keywords, account type, metadata fields, and amount ranges. Rules stored in the database.
- The Rules V2 page includes **Apply Rules to Unclassified**, which runs all saved rules only on unclassified transactions, preserves existing rule/manual classifications, and returns to the selected category.
- Selecting a category on Rules V2 shows only rules assigned directly to it, not rules of its children. All rules remains the combined view.
- Matching rules copy their Note into empty transaction notes during imports and rule application. Existing notes and manually classified transactions are preserved; dry runs do not save notes.
- **Dashboards** — Spending/income trends, category breakdowns, car cost analysis (gas, parking), salary tracking, and chart comparisons.
- Dashboard values, chart labels, and tooltips are always visible; the former hide-values toggle and stored privacy preference are no longer used.
- Category dashboards (Car, Car Gas, and Car Parking) share calendar and rolling period filters, defaulting to Last 12 Months. The selected dates apply to all summaries, charts, tables, salary comparisons, and transaction links and persist when switching currencies.
- **Expense Comparisons** — Compare a selected month, quarter, semester, or year with the median expense total across all calendar periods of that size with data, including partial and selected periods. Rolling Last 3/6/12 Months options use the median calendar quarter/semester/year respectively; All Time has no median comparison.
- **Dashboard navigation** — The Dashboards dropdown has a dedicated Expense section, landing on Expense Composition. Its sidebar contains Composition, Time Composition, and Range, separate from Overview. Income lands on Income Composition, with Composition and Time Composition replacing its old Overview; Salary, Bonuses, Reimbursement, and Bank Income remain available. Category contains Car, Car Gas, and Car Parking. Existing dashboard URLs are unchanged.
- **Expense Composition charts** — Both charts start at Level 1, rolling descendant spending into top-level categories; there is no Level selector, and legacy Level query parameters cannot enable Level 2. Category drill-down remains available. Both charts show the top 10 categories at each drill-down level, without an Others entry. Tooltip percentages use the full expense total at that level; donut slices represent the displayed categories.
- **Expense Time Composition** — A separate dashboard in the Expense sidebar, with its own period and currency controls. Stacked amounts in the selected currency, grouped into seven-day intervals starting on day 1 for calendar months and calendar months for longer periods or All Time. Includes empty intervals and exact selected date boundaries. Shows only Level 1 categories, rolling all descendant spending into the top-level category, with no category limit or Remaining categories. Chart All/None buttons show or hide all displayed categories; individual legend items toggle one category without changing the category filter. Defaults to Last 12 Months through today; explicit period selections override the default. Legacy Level query parameters cannot enable Level 2.
- The timeline includes all top-level expense categories with spending. Use the chart's All/None buttons and individual legend toggles to control visibility; there is no Categories dropdown in the filter section.
- Click a timeline segment to open its category's transactions (including descendants) within that interval, clipped to the selected period's boundaries. The transaction back link restores the timeline's period and currency.
- **Income Composition** — Matches the Expense composition dashboards using all income categories, including bonuses and Unclassified. Both Income dashboards offer Level 1 and Level 2 (default), rolling deeper descendants into the selected level; shallower categories remain visible. Composition defaults to the latest month with income and provides top-10 charts, category drill-down, median calendar-period comparisons, and transaction links. Time Composition defaults to Last 12 Months through today, with stacked currency amounts for every category at the selected level (no top-10 cap or Remaining group), empty intervals, chart All/None and legend controls, and exact-interval transaction links. Both support calendar and rolling periods and CRC/USD, preserving the level across filters and transaction return links; there is no Categories filter dropdown. Expense remains fixed at Level 1.
- **Rename-safe income dashboards** — Specialized Salary, Bonuses, Reimbursement, and Bank Income dashboards use saved category roles rather than category names. Set an Income dashboard role when adding/editing an income category; descendants inherit the nearest assigned ancestor's role. Assignments survive renames and backup export/import. Migration initializes known original categories and the renamed Work children, Reimbursement, and Bank roots. Unmapped dashboards show a warning instead of linking to all income transactions. Overview income exclusions, car salary comparisons, and transfer-flow reimbursement/bank grouping use these same assignments.
- **Salary filters** — A joined calendar/rolling period toolbar and Bi-weekly/Monthly grouping controls filter the entire Salary page. Defaults to Last 12 Months through today and Bi-weekly grouping (days 1–14 and 15–month end). Charts, selected-period total, latest salary month, monthly average/median, and transaction links use exact selected boundaries. Monthly statistics use months with salary data; partial boundary months are included. Currency, period, and grouping links preserve the other selections, and the Transactions back link restores them.
- **Bonuses filters** — Defaults to All Time, with Month, Quarter, Semester, Year, and rolling Last 3/6/12 Months options. The selected period filters all role totals, the chart, event table, and transaction link. Currency switches preserve the selected period, and returning from Transactions restores the filters.
- **Reimbursement and Bank Income filters** — Both default to Last 12 Months through today and offer the same calendar/rolling period controls. All summary statistics, category charts, individual-transaction charts, counts, and transaction links use the selected date boundaries. The latest-month card uses the latest month with data within that selection. Currency switches and transaction return links preserve the period.
- **Expense Range** — View monthly expense ranges over All Time, a calendar Year, or Last 12 Months through today (the default), with a selected month for comparison. Select category Level 1 (top level, the default) or Level 2; deeper categories roll up before monthly statistics are calculated. Rolling ranges include only transactions within their date boundaries, including partial months.
- **Categories & Rules CRUD** — Full management interface for category groups, categories, and classification rules. Category rows show clickable counts of directly assigned logical transactions (including split transactions), opening the Transactions page with that category selected and subcategories excluded. The Transactions page can toggle direct-only filtering inside the Categories dropdown; All/None changes category selections without changing this toggle. Ordinary category filters still include subcategories.
- **Category group tabs** — Categories shows one group tree at a time using Expense, Income, Transfer, and Unclassified tabs. Existing add/edit, sibling selection, move/group/delete, and direct transaction-count actions remain available. The active tab is remembered per user within the browser tab and included in transaction return links.
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
| `/expense-composition-over-time/` | Expense Time Composition dashboard |
| `/chart-comparison/` | Chart comparison tool |
| `/car/` | Car costs overview |
| `/car/gas/` | Gas expenses dashboard |
| `/car/parking/` | Parking expenses dashboard |
| `/income/salary/` | Salary income dashboard |
| `/income/` | Income Composition dashboard (replaces Income Overview) |
| `/income/composition-over-time/` | Income Time Composition dashboard |
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
