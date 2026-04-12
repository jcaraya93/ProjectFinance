# Import Pipeline Benchmark — After Optimization

**Date:** 2026-04-12
**File:** Credit-2918/2025-05.csv (10 KB, 157 lines, 147 transactions)
**Database:** PostgreSQL 17 (Docker)
**Runtime:** Python 3.12, Django 6.0.3
**Rules:** 256 classification rules, 39 categories

## Results

| Metric              | Before    | After     | Improvement |
|---------------------|-----------|-----------|-------------|
| **Total time**      | 2,045ms   | 280ms     | **7.3x faster** |
| DB queries (est)    | ~1,033    | ~10       | **99% fewer** |
| Classified          | 147/147   | 147/147   | Same |
| Converted           | 147/147   | 147/147   | Same |

## Changes Made

1. **Service layer** — Extracted `import_service.py` from the view (~140 lines → thin view + testable service)
2. **`bulk_create()`** — RawTransaction and LogicalTransaction written in 2 queries instead of 296
3. **In-memory classification** — Rules matched on objects before DB write, no per-txn UPDATE
4. **In-memory conversion** — Currency converted before DB write, no re-query + per-txn UPDATE
5. **Pre-fetched rates** — Exchange rates loaded into a dict before the transaction loop
6. **`transaction.atomic()`** — Each file import rolls back cleanly on error
