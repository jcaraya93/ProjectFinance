# HTMX + Alpine.js Enhancement

Enhance the MPA frontend with HTMX for partial page updates and Alpine.js for client-side reactivity — eliminating full-page reloads and bespoke vanilla JS without converting to an SPA.

## Problem

The transaction list page is an exploratory, iterative workflow (filter → sort → re-filter → bulk edit). Every interaction triggers a full-page reload which:

- Resets scroll position
- Flashes white while the browser rebuilds the entire DOM
- Re-initializes ~180 lines of JavaScript event listeners
- Loses ephemeral UI state (open dropdowns, checkbox selections)

The current templates fight against the reload model with bespoke JS glue: auto-submit on dropdown close, manual `fetch()` for column preferences, 45 lines of vanilla JS for bulk selection.

Other pages (upload, category CRUD, rule editing, dashboards) are simple form submissions where a full reload is natural and fine.

## Proposal

### HTMX (14KB) — Server Communication

Replace full-page reloads with partial HTML fragment swaps using HTML attributes.

**What changes:**

- Filter submission → `hx-get` with `hx-target="#results"` swaps just the table
- Sorting → column header links become `hx-get` with `hx-push-url="true"`
- Pagination → same pattern, swap table only
- Inline category edit → `hx-post` + `hx-swap="outerHTML"` replaces the cell
- Bulk category assign → `hx-post` + `hx-include` for selected checkboxes

**Server-side requirement:** Create partial templates (e.g., `_transaction_table.html`) that views return for HTMX requests (detected via `HX-Request` header). Full-page requests still get the complete template.

### Alpine.js (15KB) — Client-Side State

Replace vanilla JS for UI-only interactions using declarative HTML attributes.

**What changes:**

- Bulk select/deselect → `x-data`, `x-model`, `x-show` (replaces 45 lines of JS)
- Column visibility toggles → `x-data` with `x-model` on checkboxes
- Date preset buttons → `@click` handlers with `x-data` state
- Advanced search toggle → `x-show` with transition

### How They Complement Each Other

| Need                    | Tool       |
|-------------------------|------------|
| Fetch data from server  | HTMX       |
| Toggle UI state         | Alpine.js  |
| Client-side validation  | Alpine.js  |
| Animated transitions    | Alpine.js  |
| Persist data to server  | HTMX       |

## Integration

Add two `<script>` tags to `base.html`:

```html
<script src="https://unpkg.com/htmx.org@2"></script>
<script src="https://unpkg.com/alpinejs@3" defer></script>
```

No build step, no bundler, no Node.js required.

## Migration Strategy

Migrate incrementally, page by page:

1. **Transaction list** — highest impact, most interactive (start here)
2. **Category list** — inline editing benefits from partial swaps
3. **Rule list** — reclassify/delete actions benefit from partial updates
4. **Statement list** — wallet switcher can swap table without reload
5. **Dashboards** — lower priority, mostly read-only

Each page can be migrated independently. Unmigrated pages continue working as-is.

## Alternatives Considered

| Alternative            | Why not                                                                 |
|------------------------|-------------------------------------------------------------------------|
| **Full SPA (React/Vue)** | Requires API layer, doubles codebase, solves a problem HTMX handles with HTML attributes |
| **Unpoly**             | Similar to HTMX but more opinionated; HTMX has larger community and Django ecosystem support |
| **Turbo/Hotwire**      | Designed for Rails; usable with Django but less natural fit              |
| **Django Unicorn**     | Server-rendered reactive components via WebSocket; adds deployment complexity (ASGI) for marginal gain over HTMX |
| **Vanilla JS (current)** | Works but growing pile of bespoke glue code for each new interaction   |

## Expected Outcome

- ~150 lines of inline JS eliminated from transaction list
- No full-page reloads on the most-used page
- Scroll position, filter state, and selections preserved across interactions
- Combined JS overhead: ~30KB (vs 200KB+ for an SPA framework)
- Django views and templates remain the source of truth — server-side MVC unchanged
