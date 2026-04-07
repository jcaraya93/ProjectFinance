# Project Finance

A personal finance web application for importing bank statements, classifying transactions, and visualizing spending patterns. Built with Django, SQLite, Bootstrap 5, and Chart.js.

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
| Database | SQLite |
| Frontend | Bootstrap 5 (Flatly theme) + Chart.js 4 |
| CSS | Custom stylesheet + Bootswatch |

## Project Structure

```
ProjectFinance/
├── finance/                          # Django project configuration
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── transactions/                     # Main Django app
│   ├── models.py                     # 10 data models
│   ├── views.py                      # All view logic (~1,600 lines)
│   ├── forms.py                      # Upload, category, rule forms
│   ├── urls.py                       # 32 URL routes
│   ├── admin.py                      # Django admin registration
│   ├── parsers/
│   │   ├── base.py                   # Base parser interface & data classes
│   │   ├── credit_card.py            # Credit-2918 CSV parser
│   │   └── debit_card.py             # Debit-2651 CSV parser
│   ├── services/
│   │   ├── classifier.py             # Classification entry point
│   │   ├── yaml_classifier.py        # Rule matching engine (reads from DB)
│   │   ├── ai_classifier.py          # AI-assisted classification
│   │   ├── exchange_rates.py         # CRC↔USD rate fetching & conversion
│   │   └── stats.py                  # Dashboard aggregation queries
│   ├── management/commands/
│   │   ├── seed_categories.py        # Import categories & rules from YAML
│   │   ├── bulk_import.py            # Batch import all Data/ CSV files
│   │   └── export_rules.py           # Export DB rules back to YAML
│   ├── templates/transactions/       # 16 HTML templates
│   ├── static/transactions/          # CSS and JS assets
│   └── templatetags/
│       └── finance_filters.py        # Custom template filters
├── Data/                             # Raw CSV statement files (not in git)
├── classification_rules.yaml         # Rule definitions (synced with DB)
├── db.sqlite3                        # SQLite database (not in git)
├── requirements.txt
├── manage.py
└── .gitignore
```

## Data Model

```
CategoryGroup                         Account
├── slug: expense|income|             ├── CreditAccount (card_number)
│         transfer|unclassified       └── DebitAccount (iban)
└── Category                               └── StatementImport
    ├── name, color                            └── CurrencyLedger (CRC|USD)
    └── ClassificationRule                         └── RawTransaction (immutable)
        ├── description (keyword match)                └── LogicalTransaction (1:N)
        ├── account_type                                   ├── description
        ├── metadata (JSON conditions)                     ├── amount, amount_crc, amount_usd
        ├── amount_min/max                                 ├── category → Category
        └── detail                                         ├── classification_method
                                                           └── matched_rule → ClassificationRule
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

- Python 3.10+
- pip

### Installation

```bash
# Clone the repository
git clone <repo-url>
cd ProjectFinance

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create .env file (optional — for secret key, debug settings)
echo "SECRET_KEY=your-secret-key" > .env
echo "DEBUG=True" >> .env

# Run migrations
python manage.py migrate

# Seed categories and rules from YAML
python manage.py seed_categories

# Start the development server
python manage.py runserver
```

The app will be available at `http://127.0.0.1:8000/`.

### Importing Data

**Via the web UI:**
Navigate to Statements → Import Statement, then upload one or more CSV files. The parser is auto-detected.

**Via management command:**
```bash
# Import all CSV files from Data/ directory
python manage.py bulk_import

# Import from a custom directory
python manage.py bulk_import --data-dir /path/to/csvs
```

The `Data/` directory should be organized as:
```
Data/
├── Credit-XXXX/          # Credit card statements
│   ├── statement1.csv
│   └── statement2.csv
└── Debit-XXXX/           # Debit card statements
    ├── statement1.csv
    └── statement2.csv
```

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
| `python manage.py bulk_import` | Import all CSV files from `Data/` directory. Skips already-imported files. |
| `python manage.py export_rules` | Export current DB rules back to `classification_rules.yaml`. |

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

### Transactions
| URL | Description |
|-----|-------------|
| `/transactions/` | Transaction list with filters, sorting, bulk actions |
| `/transactions/<id>/edit/` | Edit a transaction (description, category, split) |
| `/transactions/<id>/update-category/` | AJAX inline category update |
| `/transactions/bulk-update-category/` | Bulk category assignment |

### Statements
| URL | Description |
|-----|-------------|
| `/upload/` | Upload CSV files |
| `/statements/` | List imported statements |

### Categories & Rules
| URL | Description |
|-----|-------------|
| `/categories/` | Category management |
| `/rules/` | Classification rules list |
| `/rules/add/` | Add a new rule |
| `/rules/<id>/edit/` | Edit a rule |
| `/rules/reclassify/` | Re-run all rules on non-manual transactions |
| `/rules/classify-unclassified/` | Run rules only on unclassified transactions |

## Transaction List Features

- **Filters** — Date range (with presets), account/wallet, classification group & category, method, split status, amount range
- **Advanced search** — Transaction code, reference number, rule ID, statement ID
- **Sorting** — Click column headers to sort by date, account, method, group, category, description, or amount
- **Column visibility** — Toggle columns on/off (persisted in localStorage)
- **Inline category edit** — Click a category name to change it via dropdown
- **Bulk selection** — Checkbox per row + select-all, with sticky bottom bar for bulk category assignment
- **Edit page** — Edit description, category; split into multiple sub-transactions or unsplit back
