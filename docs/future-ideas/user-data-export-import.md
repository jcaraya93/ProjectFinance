# User Data Export / Import — Design Document

## Overview

Management commands to export and import all data belonging to a single user. Produces a single portable JSON file that can be stored in Dropbox, OneDrive, or any file system and later restored into a fresh or existing database — regardless of whether the target runs SQLite (local-lite) or PostgreSQL (Docker/production).

## Goals

- Export all data for one user into a single self-contained file
- Import that file into any instance of the app (SQLite or PostgreSQL)
- Safe to run while the server is running
- No PK conflicts — uses natural keys for identity
- All-or-nothing import via a single database transaction

## Commands

### Export

```bash
python manage.py export_user_data user@email.com -o backup.json
```

Writes a JSON file containing every record owned by the user.

### Import

```bash
python manage.py import_user_data backup.json
```

Reads the JSON file and creates all records in the target database. Wrapped in a single transaction — rolls back on any error.

## Data Scope

All user-owned data is included. Shared/global data (CategoryGroup, ExchangeRate) is handled specially.

### Exported (user-scoped)

| Model | Natural key | Notes |
|-------|------------|-------|
| **User** | `email` | Password hash included so login works after restore |
| **UserPreference** | (one per user) | Column visibility settings |
| **Category** | `name` + `group.slug` | Includes color |
| **ClassificationRule** | `category` + `description` + `account_type` | All rule conditions |
| **Account** | `account_type` discriminator | Base fields |
| **CreditAccount** | `card_number_hash` | Extends Account |
| **DebitAccount** | `iban` | Extends Account |
| **StatementImport** | `file_hash` | Duplicate detection on import |
| **CurrencyLedger** | `statement_import` + `currency` | Balances |
| **RawTransaction** | `ledger` + `date` + `description` + `amount` | Immutable bank records |
| **LogicalTransaction** | `raw_transaction` ref + `description` + `amount` | Classification state |

### Not exported (global/shared)

| Model | Reason | Handled on import |
|-------|--------|-------------------|
| **CategoryGroup** | 4 fixed slugs (expense, income, transfer, unclassified) | Auto-created via `CategoryGroup.get_group()` |
| **ExchangeRate** | Global table, not user-specific | Optionally included (rates referenced by user's transactions) |

## JSON File Structure

```json
{
  "version": 1,
  "exported_at": "2026-04-22T01:30:00Z",
  "user": {
    "email": "user@example.com",
    "password": "<hashed>",
    "is_active": true,
    "is_staff": false
  },
  "preferences": {
    "transaction_columns": {"1": true, "2": false}
  },
  "categories": [
    {
      "name": "Groceries",
      "group_slug": "expense",
      "color": "#28a745"
    }
  ],
  "classification_rules": [
    {
      "category_name": "Groceries",
      "category_group_slug": "expense",
      "description": "WALMART",
      "account_type": "",
      "amount_min": null,
      "amount_max": null,
      "metadata": {},
      "detail": "Walmart purchases"
    }
  ],
  "accounts": [
    {
      "account_type": "credit_account",
      "card_holder": "John Doe",
      "nickname": "Credit 2918",
      "credit_account": {
        "card_number_hash": "abc123...",
        "card_number_last4": "2918"
      }
    },
    {
      "account_type": "debit_account",
      "card_holder": "John Doe",
      "nickname": "Debit 2651",
      "debit_account": {
        "iban": "CR12345678901234567890",
        "client_number": "12345"
      }
    }
  ],
  "statement_imports": [
    {
      "account_ref": "abc123...",
      "filename": "statement_jan.csv",
      "file_hash": "sha256...",
      "statement_date": "2026-01-31",
      "points_assigned": 100,
      "points_redeemable": 50,
      "ledgers": [
        {
          "currency": "CRC",
          "previous_balance": "150000.00",
          "balance_at_cutoff": "200000.00",
          "raw_transactions": [
            {
              "date": "2026-01-15",
              "description": "WALMART ESCAZU",
              "amount": "-25000.00",
              "account_metadata": {"transaction_code": "CO"},
              "logical_transactions": [
                {
                  "description": "WALMART ESCAZU",
                  "amount": "-25000.00",
                  "amount_crc": "-25000.00",
                  "amount_usd": "-38.46",
                  "category_name": "Groceries",
                  "category_group_slug": "expense",
                  "classification_method": "rule",
                  "matched_rule_description": "WALMART"
                }
              ]
            }
          ]
        }
      ]
    }
  ],
  "exchange_rates": [
    {
      "date": "2026-01-15",
      "usd_to_crc": "650.0000"
    }
  ]
}
```

### Design Decisions

**Nested structure** — Statement imports contain their ledgers, which contain raw transactions, which contain logical transactions. This preserves the natural hierarchy and makes the file human-readable. References between objects use natural keys (e.g., `account_ref` is the credit card hash or IBAN).

**`account_ref`** — For credit accounts this is `card_number_hash`; for debit accounts it is `iban`. Used to link statement imports to their account on import.

**`matched_rule_description`** — Instead of a rule PK, the logical transaction stores enough info to re-link to the correct rule after import (category + description match).

**`version`** — Schema version number for forward compatibility. Import validates the version before proceeding.

## Import Behavior

### Identity Resolution

On import, records are matched by natural key to determine create-or-skip:

| Model | Match by | If exists |
|-------|---------|-----------|
| User | `email` | Error (or `--merge` flag to update) |
| Category | `name` + `group_slug` + `user` | Skip |
| ClassificationRule | `category` + `description` + `account_type` | Skip |
| CreditAccount | `card_number_hash` | Reuse existing |
| DebitAccount | `iban` | Reuse existing |
| StatementImport | `file_hash` | Skip (duplicate) |
| ExchangeRate | `date` | Skip if exists |

### Import Order

1. Create User (or validate existing)
2. Ensure CategoryGroups exist (4 fixed slugs)
3. Create Categories
4. Create ClassificationRules (references categories)
5. Create Accounts (Credit/Debit)
6. Create StatementImports → CurrencyLedgers → RawTransactions → LogicalTransactions
7. Import ExchangeRates
8. Commit transaction

### Error Handling

- Entire import wrapped in `transaction.atomic()`
- Any error → full rollback, no partial data
- Validation errors reported with context (which record, which field)

## File Size Estimates

| Transactions | Approx. file size |
|-------------|-------------------|
| 1,000 | ~500 KB |
| 5,000 | ~2–3 MB |
| 20,000 | ~10–15 MB |

## Files to Create

```
core/management/commands/
├── export_user_data.py     # Export command
└── import_user_data.py     # Import command
core/tests/
└── test_user_data_io.py    # Round-trip test (export → import → verify)
```

## Future Considerations

- **`--merge` flag** — Allow importing into a database that already has the user, merging new data.
- **Date range filter** — `--from` / `--to` to export a subset of transactions.
- **Encryption** — Optional password-protected export for sensitive financial data.
- **API endpoint** — Expose export/import via HTTP for a future frontend feature.
