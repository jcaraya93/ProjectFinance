# Future Feature Ideas

Ideas for new functionality based on a full codebase analysis, grouped by area.

---

## Analytics & Insights

### Budget Tracking

Set monthly spending limits per category, track actual vs budget, and alert when approaching the limit.

**Why:** Core personal finance feature — importing and classifying transactions is valuable, but the real payoff is knowing whether spending stays within targets.

**Scope:**
- New model: `Budget` (user, category, month, amount_limit, currency)
- Monthly budget vs actual comparison view
- Summary cards on main dashboard showing over/under per category
- Optional email/in-app alerts when a category crosses 80% or 100% of budget
- Support both CRC and USD budgets with conversion

---

### Recurring Transaction Detection

Automatically identify subscriptions and recurring charges (Netflix, gym, insurance, etc.) by analyzing transaction patterns.

**Why:** Helps users find forgotten subscriptions and understand fixed vs variable spending.

**Scope:**
- Service that groups transactions by description similarity + regular interval
- Confidence score (weekly, biweekly, monthly, annual patterns)
- Dedicated "Subscriptions" view listing detected recurring charges with total monthly cost
- Manual confirm/dismiss to refine detection
- Alert when a new recurring pattern is detected

---

### Spending Forecasting

Use historical trends to predict next month's expenses per category.

**Why:** Adds predictive value beyond historical reporting — users can plan ahead.

**Scope:**
- Simple moving-average or linear-regression forecast per category
- "Projected" column on spending dashboard
- Visual overlay on monthly trend charts (actual vs projected)
- Account for seasonal patterns (December spending spikes, etc.)

---

### Year-over-Year Comparison

Compare the same month across multiple years to show growth or decline in spending and income.

**Why:** Useful for long-term financial planning and understanding lifestyle cost changes.

**Scope:**
- New dashboard view with side-by-side bar charts per category
- Percentage change indicators (↑12% vs last year)
- Filter by category group, individual category, or all

---

### Savings Goals

Set savings targets with deadlines, track progress, and visualize the timeline to goal.

**Why:** Motivational feature that ties income/expense tracking to concrete objectives.

**Scope:**
- New model: `SavingsGoal` (user, name, target_amount, currency, deadline, current_amount)
- Progress bar visualization on dashboard
- Auto-calculate "on track" status based on savings rate
- Monthly contribution tracking

---

## Import & Data

### Multi-Bank Parser Registry

Plugin system to add new bank formats without modifying core code.

**Why:** Currently adding a new bank requires creating a parser class, adding detection logic to `import_service.py`, and updating `detect_card_type()`. A registry pattern would make this self-contained.

**Scope:**
- `ParserRegistry` class with `register(bank_code, parser_class)` and `detect_and_parse(file_content)`
- Each parser implements a `can_parse(file_content) → bool` class method
- Auto-discovery via Django app registry or entry points
- Extract shared utilities (`_parse_decimal`, `_parse_date`, `_clean_description`) into `BaseParser`

---

### Transaction Deduplication UI

Surface potential duplicate transactions across overlapping statement imports and let users merge or dismiss them.

**Why:** SHA-256 file-hash prevents re-importing the same file, but overlapping statement periods from different files can introduce duplicate transactions.

**Scope:**
- Service that finds transactions with matching (date, amount, description) across different statements
- Dedicated "Potential Duplicates" view with side-by-side comparison
- Merge (keep one, delete other) or dismiss (mark as not duplicate) actions
- Dashboard widget showing duplicate count

---

### CSV / Excel Export

Export filtered transaction data to CSV or Excel for tax preparation, accountant handoff, or personal records.

**Why:** High practical utility — users need to share data outside the app.

**Scope:**
- "Export" button on transaction list that respects current filters
- CSV and XLSX format options
- Include all visible columns plus category, group, account, currency
- Respect column visibility preferences

---

### OFX / QIF Import Support

Support standard financial file formats (OFX, QIF, QFX) in addition to bank-specific CSVs.

**Why:** Broader compatibility with banks that offer standard export formats.

**Scope:**
- New parsers for OFX (XML-based) and QIF (text-based) formats
- Register in parser registry (see Multi-Bank Parser Registry above)
- Map OFX fields to existing `RawTransaction` model
- Handle OFX's built-in duplicate detection (FITID field)

---

## Classification & Rules

### Rule Testing / Preview UI

Before saving a classification rule, show a preview of which existing transactions would match.

**Why:** Prevents mistakes — users currently save a rule and hope it matches the right transactions. A preview eliminates trial-and-error.

**Scope:**
- AJAX endpoint: `/rules/preview/` accepts rule conditions, returns matching transaction count + sample list
- "Preview Matches" button on rule add/edit form
- Show count, sample descriptions, and amounts
- Highlight transactions that would change category (currently classified differently)

---

### Classification Audit Log

Track every classification change — who/what changed a transaction's category and when.

**Why:** Currently manual classifications overwrite previous ones with no history. Users can't see why a transaction was reclassified.

**Scope:**
- New model: `ClassificationLog` (transaction, old_category, new_category, method, changed_by, timestamp)
- Auto-populate on every category change (rule engine, manual, bulk, AI)
- "History" link on transaction detail showing timeline of changes
- Dashboard metric: classification changes per day

---

### Rule Conflict Detection

Warn when two or more rules would match the same transactions with different target categories.

**Why:** As the rule set grows, overlapping rules cause unpredictable classification. Currently the most-specific rule wins silently.

**Scope:**
- Service that cross-checks all rules for overlap (same description substring, overlapping amount ranges, same account type)
- "Conflicts" badge on rules list page
- Detail view showing which rules conflict and sample affected transactions
- Suggestion to merge, prioritize, or narrow rules

---

### Smart Rule Suggestions

Analyze manual classifications to suggest new rules that would automate them.

**Why:** Users manually classify transactions, then forget to create rules. This closes the loop.

**Scope:**
- Service that finds manually-classified transactions with common description patterns
- "Suggested Rules" section on rules page
- One-click "Create Rule" from suggestion
- Confidence score based on pattern frequency

---

## User Experience

### Undo for Bulk Actions

Make bulk category assignment and purge operations reversible with a timeout window.

**Why:** Currently `bulk_update_category` and `purge_all_data` are irreversible. A misclick on 500 transactions has no recovery path.

**Scope:**
- Soft-delete pattern: mark as "pending delete" with 30-second undo window
- Flash message with "Undo" link after bulk operations
- Background task to hard-delete after timeout
- For bulk category: store previous category in `ClassificationLog` (see Audit Log above)

---

### Multi-Currency Dashboard

Side-by-side CRC and USD dashboard views with live exchange rate conversion.

**Why:** The app already handles dual currency, but dashboards show one currency at a time. Users who earn in USD and spend in CRC need both perspectives.

**Scope:**
- Toggle or split-view on main dashboard
- "Show in CRC / Show in USD / Show both" selector
- Apply latest exchange rate for conversion
- Highlight transactions where rate was estimated vs actual

---

### Mobile-Responsive Dashboards

Optimize chart layouts and tables for phone and tablet screens.

**Why:** Currently Chart.js charts and wide tables don't adapt well to small screens. Finance apps are commonly checked on phones.

**Scope:**
- Responsive chart containers (Chart.js `responsive: true` + `maintainAspectRatio: false`)
- Collapsible filter panel on mobile
- Card-based transaction list (instead of table) on narrow screens
- Touch-friendly bulk selection

---

## Security & Auth

### Two-Factor Authentication

TOTP-based 2FA using an authenticator app (Google Authenticator, Authy, etc.).

**Why:** Personal finance data is sensitive. Email + password alone is insufficient for strong security.

**Scope:**
- Use `django-otp` or `django-two-factor-auth` package
- QR code setup flow during registration or settings
- Backup codes for recovery
- Optional per-user (not forced)
- Rate limit on 2FA code attempts

---

## DevOps & Operations

### CI/CD Pipeline

GitHub Actions workflows for automated testing, linting, building, and deployment.

**Why:** Currently missing entirely. No automated quality gates — bugs and regressions can reach production unchecked.

**Scope:**
- `.github/workflows/test.yml` — run pytest + coverage report
- `.github/workflows/lint.yml` — Black, isort, flake8
- `.github/workflows/build.yml` — Docker image build + push to ACR
- `.github/workflows/deploy.yml` — deploy to Azure Container Apps (staging → production)
- `.github/workflows/security.yml` — Trivy image scan, CodeQL analysis

---

### Health Check Endpoint

A `/healthz` endpoint for container orchestration and uptime monitoring.

**Why:** Azure Container Apps and Docker health checks need an HTTP endpoint to verify the app is running and connected to the database.

**Scope:**
- Unauthenticated GET endpoint returning `{"status": "ok", "db": "connected"}`
- Check database connectivity (lightweight query)
- Check cache connectivity (if applicable)
- Return 503 if any dependency is down
- Use in Docker `HEALTHCHECK` and Azure Container Apps probes

---

### Automated Database Backups

Scheduled PostgreSQL backups with retention policy.

**Why:** Financial data is irreplaceable. Currently no backup automation exists.

**Scope:**
- `pg_dump` cron job or Azure Backup integration
- Daily backups with 30-day retention
- Store in Azure Blob Storage or S3
- Backup verification (periodic restore test)
- Management command: `python manage.py backup_db`
