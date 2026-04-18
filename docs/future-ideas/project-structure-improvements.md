# Project Structure Improvements

Structural refactoring to improve maintainability, testability, and readiness for future features (HTMX, API layer, etc.).

## 1. Split `views.py` into Modules (Highest Priority)

`views.py` is 2,319 lines with 36 view functions serving five unrelated domains. Split into a views package:

```
transactions/
├── views/
│   ├── __init__.py             ← Re-exports all views (urls.py unchanged)
│   ├── dashboards.py           ← 10 dashboard views (~724 lines)
│   ├── transactions.py         ← List, edit, split, bulk (~595 lines)
│   ├── statements.py           ← Upload, list, purge (~316 lines)
│   ├── categories.py           ← CRUD + import/export (~256 lines)
│   └── rules.py                ← CRUD + reclassify (~316 lines)
```

The `__init__.py` re-exports everything so `urls.py` doesn't need changes:

```python
from .dashboards import *
from .transactions import *
from .statements import *
from .categories import *
from .rules import *
```

**Effort:** Low — pure reorganization, no behavior change.

## 2. Add Tests for Core Logic

`tests.py` is empty. The components most in need of coverage:

| Component | Why | Risk without tests |
|---|---|---|
| CSV parsers | Complex format parsing with edge cases (footer detection, currency splitting) | Silent data corruption on import |
| Classification engine | 3-phase rule matching with scoring | Misclassified transactions |
| Import service | Duplicate detection, bulk creation, currency conversion | Duplicate imports, wrong amounts |
| Stats service | Complex aggregation queries | Dashboard showing wrong numbers |

Recommend `pytest-django` over Django's built-in test runner for fixtures, parametrize, and clearer output.

**Effort:** Medium — biggest gap in the project today.

## 3. Extract Inline JavaScript to Static Files

`transaction_list.html` (27KB) contains ~180 lines of inline JS. The upload page has another inline script block. Extract to static files:

```
static/transactions/js/
├── charts.js                ← Exists (113 bytes)
├── transaction-list.js      ← Extract from transaction_list.html
└── upload.js                ← Extract from upload.html
```

Benefits:
- Templates become pure HTML/template logic
- JS files can be cached by the browser
- Easier to migrate to HTMX + Alpine.js later — declarative attributes replace most of this JS

**Effort:** Low — move code, add `<script src>` tags.

## 4. Reduce Dashboard View Boilerplate

The 10 dashboard views repeat the same pattern: parse query params → call service → time the render → record metric → return template. Extract the common boilerplate:

```python
# Current: repeated in every dashboard view
@login_required
def car_gas_dashboard(request):
    with tracer.start_as_current_span("view.car_gas") as span:
        t0 = time.monotonic()
        display_currency = request.GET.get('display_currency', 'CRC')
        time_group = request.GET.get('time_group', 'monthly')
        # ... dashboard-specific logic ...
        elapsed_ms = (time.monotonic() - t0) * 1000
        dashboard_duration.record(elapsed_ms, {"dashboard": "car_gas"})
        return render(request, 'transactions/dashboard_car_gas.html', context)

# Proposed: decorator or helper handles boilerplate
@login_required
@dashboard_view("car_gas", "transactions/dashboard_car_gas.html")
def car_gas_dashboard(request, display_currency, time_group):
    # Only dashboard-specific logic, return context dict
    ...
    return context
```

**Effort:** Low — create a decorator/helper, refactor one dashboard at a time.

## 5. Fix Rate Limiter Process Isolation

`ratelimit.py` tracks request counts in-memory. Gunicorn runs 3 worker processes, each with its own memory — a user hitting different workers can exceed limits by 3×. Switch to Django's cache framework backed by the database or Redis.

**Effort:** Low — change storage backend, not the decorator API.

## 6. Split Into Multiple Django Apps (Low Priority)

The entire application lives in the `transactions` app. If the project grows significantly, consider splitting:

```
accounts/          ← User, auth, preferences
transactions/      ← Models, import, parsing
classification/    ← Rules, classifier, AI
dashboards/        ← Views + templates only
```

**Not recommended now** — Django apps add import complexity and migration coordination. The views split (item 1) gives 80% of the maintainability benefit at 20% of the effort.

## Recommended Order

| # | Improvement | Effort | Impact | Status |
|---|---|---|---|---|
| 1 | ~~Split views.py into modules~~ | Low | Immediate maintainability | ✅ Done |
| 2 | ~~Add tests for parsers + classifier~~ | Medium | Protects core logic correctness | ✅ Done |
| 3 | Extract inline JS to static files | Low | Cleaner templates, easier HTMX migration | |
| 4 | Dashboard view boilerplate reduction | Low | Less repetition across 10 views | |
| 5 | Fix rate limiter process isolation | Low | Correct enforcement under multi-worker | |
| 6 | Split into multiple Django apps | High | Only if project grows significantly | |

Items 1–2 completed. Items 3–5 are independent and set the foundation for HTMX adoption.
