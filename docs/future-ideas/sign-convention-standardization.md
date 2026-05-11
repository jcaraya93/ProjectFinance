# Proposal: Standardize Transaction Sign Convention

## Status: In Progress

## Problem

Credit card transactions use the opposite sign convention from debit accounts:

| Account | Expense (money out) | Income (money in) |
|---------|-------------------|------------------|
| **Debit** | negative ✅ | positive ✅ |
| **Credit** | positive ⚠️ | negative ⚠️ |

This forces `if is_credit` / `hasattr(acct, 'creditaccount')` branches throughout the codebase (~15 locations).

## Approach

### New fields:
- **`Account.sign_factor`** — `1` for debit, `-1` for credit
- **`RawTransaction.normalized_amount`** — `amount × sign_factor`, computed at import time

### Key Invariants:
```
RawTransaction.amount              = original bank value (immutable)
RawTransaction.normalized_amount   = amount × Account.sign_factor (standardized)
LogicalTransaction.amount          = RawTransaction.normalized_amount (for non-split)
sum(split_amounts)                 = RawTransaction.normalized_amount (for splits)
```

### Convention:
- **Negative = money out** (expenses, payments, transfers out)
- **Positive = money in** (income, refunds, transfers in)
- Applies to ALL accounts uniformly

### Why this approach:
- RawTransaction preserves original bank data in `amount`
- Normalized value lives at the source — no mismatch between raw and logical
- Split/unsplit validation uses `raw.normalized_amount` — no special cases
- `amount_crc` and `amount_usd` on LogicalTransaction derive from `normalized_amount`
- All `is_credit` sign-flip branches can be removed

## Implementation Phases

### Phase 1: Add sign_factor to Account + normalized_amount to RawTransaction
- Add `sign_factor` SmallIntegerField to Account (default=1)
- Add `normalized_amount` DecimalField to RawTransaction
- CreditAccount.save() defaults sign_factor=-1
- Data migration: backfill sign_factor=-1 on CreditAccount, compute normalized_amount for all RawTransactions

### Phase 2: Data migration — flip LogicalTransaction signs
- For all LogicalTransactions linked to credit accounts:
  set amount/amount_crc/amount_usd = current value × -1
- This makes them consistent with normalized_amount
- Clear and re-run TransactionPair matching after flip

### Phase 3: Import pipeline
- Update import_service.py:
  - Compute `raw.normalized_amount = raw.amount * account.sign_factor` at import
  - Create LogicalTransaction with `amount = raw.normalized_amount`
  - Currency conversion derives from normalized_amount

### Phase 4: Split/unsplit fix
- Update transactions.py split validation:
  `if total != raw.normalized_amount: error`
- Unsplit: `first.amount = raw.normalized_amount`
- Edit template JS: validate against normalized_amount

### Phase 5: Dashboard cleanup
- Remove all `is_credit` / `hasattr(acct, 'creditaccount')` sign-flip branches
- dashboards.py: ~10 locations
- pair_matcher.py: simplify to always use opposite-sign matching
- Templates: remove account-type sign checks

### Phase 6: Tests
- Update test assertions for credit card sign convention
- Add regression test verifying normalized_amount consistency

## Affected Files

### Must Change ❌

| File | What |
|------|------|
| `core/models.py` | Add sign_factor to Account, normalized_amount to RawTransaction |
| `core/services/import_service.py` | Compute normalized_amount, use it for LogicalTransaction |
| `core/services/pair_matcher.py` | Remove same-sign credit matching special case |
| `core/views/dashboards.py` (~10 locations) | Remove `is_credit` sign branches |
| `core/views/transactions.py` | Split/unsplit uses normalized_amount |
| `core/templates/core/edit_transaction.html` | JS split validation |

### Likely Safe ✅

| File | Why safe |
|------|---------|
| `core/services/stats.py` | Uses `Abs(amount_field)` everywhere |
| `core/filters.py` | Sign-agnostic range filters |
| `core/services/user_data_io.py` | Stores DB values |
| `core/parsers/` | Produce RawTransaction.amount (unchanged) |

### Needs Review ⚠️

| File | Concern |
|------|---------|
| `core/templates/core/dashboard.html` | Summary card color logic |
| `core/templates/core/statement_list.html` | Balance display |
| `core/services/yaml_classifier.py` | Amount-based rule matching |
| Tests | Sign assertions need updating |

## Risk Mitigation

- `RawTransaction.amount` stays untouched — original bank data preserved
- `normalized_amount` is computed from `amount × sign_factor` — deterministic and reversible
- Run full test suite after each phase
- Test on SQLite dev DB first, verify all dashboards

## Decision Log

- 2026-05-10: Decided to standardize on negative=out, positive=in for all accounts
- 2026-05-10: RawTransaction stays immutable — only add normalized_amount alongside original
- 2026-05-10: sign_factor on Account model — convention is account-level
- 2026-05-10: Changed approach from flipping LogicalTransaction directly to adding normalized_amount on RawTransaction — cleaner, no raw↔logical mismatch
