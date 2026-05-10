# Proposal: Standardize Transaction Sign Convention

## Status: Planning (Not Started)

## Problem

Credit card transactions use the opposite sign convention from debit accounts:

| Account | Expense (money out) | Income (money in) |
|---------|-------------------|------------------|
| **Debit** | negative ✅ | positive ✅ |
| **Credit** | positive ⚠️ | negative ⚠️ |

This forces `if is_credit` / `hasattr(acct, 'creditaccount')` branches throughout the codebase (~15 locations) to handle sign differences. The inconsistency affects dashboards, pair matching, Sankey flows, expense queries, and split/unsplit validation.

## Current Sign Distribution

```
Account    Group           Sign       Count
--------------------------------------------------
credit     expense         positive     1429
credit     income          negative       33
credit     transaction     negative       34
debit      expense         negative      190
debit      income          positive      137
debit      transaction     negative       58
debit      transaction     positive        8
```

## Target Convention

**All LogicalTransactions:** negative = money out, positive = money in, regardless of account type.  
**RawTransactions:** unchanged (immutable bank data).  
**Account.sign_factor:** new field storing the sign flip needed (1 for debit, -1 for credit).

### Key Invariant

```
LogicalTransaction.amount = RawTransaction.amount × Account.sign_factor
```

- `amount_crc` and `amount_usd` follow the same sign as `amount`
- Negative = money out, Positive = money in, for ALL accounts
- Split amounts must sum to `raw.amount * account.sign_factor`

## Proposed Data Model Change

Add `sign_factor` to the `Account` model:

```python
class Account(models.Model):
    ...
    sign_factor = models.SmallIntegerField(
        default=1,
        help_text='1 for debit (standard), -1 for credit (flip signs on LogicalTransaction)'
    )
```

- `CreditAccount` defaults to `sign_factor = -1`
- `DebitAccount` defaults to `sign_factor = 1`
- Accessible via `raw.ledger.statement_import.account.sign_factor`

## Implementation Phases

### Phase 1: Add sign_factor to Account model
- Add `sign_factor` SmallIntegerField to Account (default=1)
- Data migration: set sign_factor=-1 on all CreditAccount records
- Override save() on CreditAccount to default sign_factor=-1

### Phase 2: Data migration — flip LogicalTransaction signs
- For all LogicalTransactions linked to credit accounts:
  multiply `amount`, `amount_crc`, `amount_usd` by -1
- ~1,496 records affected (1,429 expenses + 33 income + 34 transfers)
- Clear and re-run TransactionPair matching after flip

### Phase 3: Import pipeline
- Update `import_service.py` to apply sign_factor when creating LogicalTransaction:
  `txn.amount = raw.amount * account.sign_factor`
- This happens before currency conversion, so `amount_crc`/`amount_usd` get correct signs automatically

### Phase 4: Split/unsplit fix
- Update `transactions.py` to use sign_factor for validation:
  `expected = raw.amount * account.sign_factor`
- Unsplit: `first.amount = raw.amount * account.sign_factor`
- Edit template: JS validation uses sign_factor

### Phase 5: Dashboard cleanup
- Remove all `is_credit` / `hasattr(acct, 'creditaccount')` sign-flip branches
- `dashboards.py`: ~10 locations
- `pair_matcher.py`: simplify to always use opposite-sign matching (no more same-sign credit special case)
- Templates: remove account-type sign checks

### Phase 6: Tests
- Update test assertions for credit card sign convention
- Add regression test verifying sign_factor consistency

## Affected Files

### Must Change ❌

| File | What | Why |
|------|------|-----|
| `core/models.py` | Add `sign_factor` to Account | New field |
| `core/services/import_service.py:187-190` | `amount=raw.amount` copy | Apply sign_factor |
| `core/services/pair_matcher.py:80-180` | Same-sign credit matching | Simplify to opposite-sign only |
| `core/views/dashboards.py` (~10 locations) | `is_credit` + sign checks | Remove branches |
| `core/views/transactions.py:275-338` | Split/unsplit validation | Compare to `raw.amount * sign_factor` |
| `core/templates/core/edit_transaction.html:84-85` | JS split validation | Account for sign_factor |
| `core/templates/core/transaction_list.html:284` | Color by sign | Already correct after flip |

### Likely Safe ✅

| File | What | Why safe |
|------|------|---------|
| `core/services/stats.py` | Uses `Abs(amount_field)` everywhere | Abs hides sign |
| `core/filters.py` | Amount range filters | Sign-agnostic |
| `core/services/user_data_io.py` | Export/import | Stores DB values |
| `core/services/exchange_rates.py` | Conversion logic | Preserves sign |
| `core/parsers/` | CSV parsers | Produce RawTransaction (unchanged) |

### Needs Review ⚠️

| File | What | Concern |
|------|------|---------|
| `core/templates/core/dashboard.html:35-102` | Summary card colors | May assume positive=income |
| `core/templates/core/statement_list.html` | Balance display | May depend on credit sign convention |
| `core/services/yaml_classifier.py` | Amount-based rules | Rules with amount_min/max may break |
| `core/tests/test_views_transactions.py` | Split amount tests | Must follow split logic changes |
| `core/tests/test_user_data_io.py` | Serialized sign checks | If exported signs change |

## Risk Mitigation

- Data migration is reversible (multiply by -1 again)
- Run on SQLite dev DB first, verify all dashboards
- Keep RawTransaction untouched as source of truth
- `sign_factor` on Account makes the convention explicit and queryable
- Run full test suite after each phase

## Decision Log

- 2026-05-10: Decided to standardize on negative=out, positive=in for all accounts
- 2026-05-10: RawTransaction stays immutable — only LogicalTransaction signs change
- 2026-05-10: sign_factor on Account model (not per-transaction) — the convention is account-level
- 2026-05-10: Deferred implementation — needs careful planning due to ~15 file impact
