# Codebase Improvements

Full codebase scan identifying improvements across models/DB, views, services, security, and testing.

---

## 🔴 Critical — Performance & Bugs

### 1. N+1 Query Problems

- **`core/views/categories.py:23-26`** — `category_list` runs separate `.count()` per category (100+ queries). Fix: use `annotate(Count())`.
- **`core/views/statements.py:102-114`** — `statement_list` runs separate queries per ledger. Fix: annotate counts in queryset.
- **`core/views/transactions.py:187-195`** — Metadata filter loads all JSON into memory. Fix: limit to recent 1000 or cache.

### 2. Missing Database Indexes

- `LogicalTransaction`: add `db_index=True` on `user`, `date`, `classification_method`, `category`, `matched_rule`.
- `RawTransaction`: add `db_index=True` on `user`, `date`.
- Add composite indexes: `(user, -date)`, `(user, classification_method)`, `(user, category)`.

### 3. Dead Code / Bug in forms.py

- **`core/forms.py:83-94`** — `get_group_categories_json()` has duplicate unreachable code after `return`.

### 4. AI Classifier Missing Timeout

- **`core/services/ai_classifier.py:57`** — `model.generate_content(prompt)` has no timeout; can block indefinitely.

### 5. Import Service Missing Parser Error Handling

- **`core/services/import_service.py:144-158`** — Parser called without try/except; malformed CSV crashes entire import.

---

## 🟠 High — Security

### 6. CSRF Cookie Not HttpOnly

- **`config/settings.py`** — Missing `CSRF_COOKIE_HTTPONLY = True` (defaults to False).

### 7. Unvalidated Metadata Input

- **`core/views/rules.py:172-175`** — Metadata keys from form stored without sanitization; potential XSS if rendered.

### 8. Dependency Pinning

- **`requirements.txt`** — Uses ranges not exact versions; no hash verification.

### 9. Bare Exception Catches

- **`core/services/ai_classifier.py:59-66`** — Catches `Exception` too broadly.
- **`core/views/statements.py:102-106`** — `except Exception` on YAML parse, no logging.
- **`core/views/transactions.py:117-126`** — Silent `pass` on amount filter errors; user thinks filter is applied.

---

## 🟡 Medium — Code Quality

### 10. Code Duplication

- Category group prefetch pattern repeated 3+ times across views. Extract to `_helpers.py`.
- Hardcoded category names in dashboards (`CAR_CATEGORIES`, `SALARY_CATEGORIES`, `EXCLUDED_INCOME`).

### 11. Missing Pagination

- **`core/views/rules.py:83-106`** — Rule list loads all rules into memory, sorts in Python.
- **`core/views/categories.py:17-52`** — No pagination for categories.

### 12. Exchange Rates — No Retry/Rate-Limit Handling

- **`core/services/exchange_rates.py:42-52`** — No retry on transient failures (429, 503).

### 13. Classification Not Atomic

- **`core/services/yaml_classifier.py:244-281`** — `classify_transactions_yaml()` saves each txn in separate DB transaction.

### 14. Hardcoded Values

- **`core/services/ai_classifier.py:57`** — Gemini model name `gemini-2.5-flash` hardcoded; should be in settings.
- **`core/views/dashboards.py:411-412`** — `CAR_CATEGORIES` and `SALARY_CATEGORIES` hardcoded; dashboards break silently if categories are renamed.
- **`core/services/stats.py:50`** — `EXCLUDED_INCOME` list hardcoded.

### 15. Missing Field Validators

- `Category.color` — No hex color validation. Add `RegexValidator(r'^#[0-9a-fA-F]{6}$')`.
- `DebitAccount.iban` — No IBAN format validation.

### 16. Duplicate Imports in stats.py

- Same modules re-imported inside functions (lines 49, 87, 132, 209). Move to top of file.

---

## 🟢 Low — Testing & Tooling

### 17. No CI/CD Pipeline

- `.github/workflows/` directory exists but is empty. Add GitHub Actions with tests, lint, type-check.

### 18. No View Tests

- 92 test cases exist but cover only services/parsers. Zero view tests.

### 19. Missing Dev Tooling

- No linter (`black`/`flake8`), no type checker (`mypy`), no coverage (`pytest-cov`) in `requirements-dev.txt`.

### 20. Minimal Type Hints

- Only parsers and services use type hints (~5% of codebase).

### 21. Template Accessibility

- Missing `aria-label` on SVG icons, `aria-expanded` on dropdowns, `scope="col"` on table headers.

### 22. Docker Improvements

- No `HEALTHCHECK` in Dockerfile.
- No resource limits in docker-compose.
- `collectstatic` silently fails (`2>/dev/null || true`).
