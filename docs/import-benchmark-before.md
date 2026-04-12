# Import Pipeline Benchmark — Before Optimization

**Date:** 2026-04-12
**File:** Credit-2918/2025-05.csv (10 KB, 157 lines, 147 transactions)
**Database:** PostgreSQL 17 (Docker)
**Runtime:** Python 3.12, Django 6.0.3
**Rules:** 256 classification rules, 39 categories

## Results

| Step          | Time (ms) |    % | Notes                                      |
|---------------|-----------|------|--------------------------------------------|
| Parse         |       8.3 |  0.4 | CSV parsing — fast                         |
| DB Create     |     546.5 | 26.7 | 296 individual INSERTs (2 per txn)         |
| Classify      |     901.7 | 44.1 | 147 classify calls × 256 rules each        |
| Fetch Rates   |       2.7 |  0.1 | Cached (already fetched previously)        |
| Convert       |     586.0 | 28.7 | Re-queries all txns, 147 individual UPDATEs|
| **Total**     | **2,045** |  100 | **~1,033 DB queries for 147 transactions** |

## Bottlenecks

- **Classify (44%):** Dominant cost — loops 256 rules × 147 transactions, plus one UPDATE per match
- **DB Create (27%):** One `INSERT` per RawTransaction + one per LogicalTransaction = 2N queries
- **Convert (29%):** Re-fetches transactions from DB, then one `UPDATE` per transaction
- **Fetch Rates (0.1%):** Negligible when cached; ~1s on first fetch (external API)

## Optimization Targets

- `bulk_create()` for RawTransaction and LogicalTransaction → ~2 queries instead of 296
- Single-pass: classify + convert in memory before writing to DB
- `transaction.atomic()` for rollback safety
- Pre-fetch exchange rates before the transaction loop
- Extract into a service layer for testability
