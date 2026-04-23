# ApexCharts Migration

## Problem

All dashboards use Chart.js 4 for rendering charts. Chart.js works well for basic rendering but lacks built-in support for zoom/pan, chart export, synced hover across charts, annotation lines, and advanced chart types like range areas. Adding these features requires plugins or custom code.

## Proposal

Replace Chart.js with ApexCharts across all dashboard templates. ApexCharts provides these features natively with minimal configuration.

## Features Gained

| Feature | Chart.js (current) | ApexCharts |
|---------|-------------------|------------|
| Zoom & pan | Requires `chartjs-plugin-zoom` | Built-in |
| Export to PNG/SVG | Not built-in | Built-in toolbar button |
| Click-to-drill | Clunky event API | Clean `events.click` handler |
| Synced hover across charts | Not supported | `chart.group` property |
| Annotation lines (averages, targets) | Requires `chartjs-plugin-annotation` | Built-in `annotations` config |
| Shared crosshair tooltip | Basic, one series | Multi-series with crosshair |
| Range area (min–max bands) | Not supported | `rangeArea` chart type |
| Responsive breakpoints | Basic `responsive: true` | Per-breakpoint config overrides |
| Bundle size | ~65KB | ~125KB |

## Scope

### Templates to migrate (23 Chart.js instances)

| Template | Charts | Types |
|----------|--------|-------|
| `dashboard.html` | 2 | area, bar |
| `dashboard_spending_income.html` | 4 | donut ×2, horizontal bar ×2 |
| `dashboard_car.html` | 3 | stacked bar, donut, stacked bar |
| `dashboard_car_gas.html` | 3 | bar, stacked bar, range area (band) |
| `dashboard_car_parking.html` | 2 | stacked bar ×2 |
| `dashboard_income_salary.html` | 1 | bar with annotation |
| `dashboard_transaction_health.html` | 3 | donut, stacked bar, horizontal stacked bar |
| `dashboard_rule_matching.html` | 2 | donut, bar |
| `dashboard_default_buckets.html` | 2 | donut, stacked bar |
| `chart_comparison.html` | 3 | stacked area, sparklines, stacked bar |

### Migration per chart

1. Replace `<canvas id="..."></canvas>` with `<div id="..."></div>`
2. Replace `new Chart(el, config)` with `new ApexCharts(el, config).render()`
3. Convert config format (datasets → series, scales → xaxis/yaxis, plugins → top-level)

### Enhancements to add during migration

- **Overview dashboard:** Sync hover between Income vs Expenses and Net Cash Flow charts using `chart.group`
- **Overview dashboard:** Average annotation line on Net Cash Flow bar chart
- **Car Gas dashboard:** Average annotation line on Monthly Gas Spend; use `rangeArea` type for Cost per Fill-up band chart
- **Income Salary dashboard:** Replace average line series with `annotations.yaxis` (cleaner, not in legend)
- **Horizontal bar charts:** Ensure `yaxis.labels.show: true` for category names; number formatter goes on `xaxis.labels` not `yaxis.labels`
- **All charts:** Add `chart.animations: { enabled: true, easing: 'easeinout', speed: 600 }` and `grid: { borderColor: '#f1f1f1', strokeDashArray: 4 }`

## Setup

Add ApexCharts CDN to `base.html` (no build step required):

```html
<script src="https://cdn.jsdelivr.net/npm/apexcharts"></script>
```

Chart.js CDN can be removed once all templates are migrated.

## Migration Order

Recommended: migrate one dashboard at a time, verify visually, then proceed.

1. `dashboard.html` (overview — most visited, 2 charts)
2. `dashboard_spending_income.html` (4 charts including horizontal bars)
3. `dashboard_car_gas.html` (3 charts including range area)
4. `dashboard_car.html` + `dashboard_car_parking.html` (5 charts, similar patterns)
5. `dashboard_income_salary.html` (1 chart)
6. `dashboard_transaction_health.html` + `dashboard_rule_matching.html` + `dashboard_default_buckets.html` (7 charts, data quality group)
7. `chart_comparison.html` (3 charts including sparklines)
8. Remove Chart.js CDN from `base.html`

## Gotchas

- **Horizontal bar axis swap:** In ApexCharts horizontal bars, `xaxis` is the value axis and `yaxis` is the category axis — opposite of vertical bars. Category labels go on `xaxis.categories` but display on the y-axis. Number formatters go on `xaxis.labels`, not `yaxis.labels`.
- **Donut vs doughnut:** Chart.js uses `type: 'doughnut'`, ApexCharts uses `type: 'donut'`. Data goes in `series` (flat array), not `datasets[0].data`.
- **Privacy mode:** Charts using privacy mode need `tooltip: { enabled: !isPrivacy }` and `yaxis: { labels: { show: !isPrivacy } }`. Annotations should be wrapped: `annotations: isPrivacy ? {} : { yaxis: [...] }`.
- **HTMX interaction:** If HTMX is added later, ApexCharts instances inside swapped DOM fragments need to be destroyed before swap. Recommend doing HTMX migration first (see `htmx-alpine-enhancement.md`), then migrating charts inside the partial templates.
