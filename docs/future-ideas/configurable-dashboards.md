# Configurable Dashboards

## Problem

The Car, Car Gas, Car Parking, and Income Salary dashboards hardcode category names (e.g. `Car Gas`, `Salary Main`). New users with different category names see empty dashboards. These dashboards should work for any user with any category naming.

## Approach

Replace hardcoded category dashboards with user-configurable custom dashboards. Users can create dashboards, pick a visualization type, and assign their own categories.

## Data Model

### New model: `CustomDashboard`

```python
class CustomDashboard(models.Model):
    DASHBOARD_TYPES = [
        ('category_trend', 'Category Trend'),
        ('category_detail', 'Category Detail'),
        ('income_tracking', 'Income Tracking'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='custom_dashboards')
    name = models.CharField(max_length=100)
    dashboard_type = models.CharField(max_length=20, choices=DASHBOARD_TYPES)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
```

### New model: `DashboardCategory`

Through model to assign categories with optional roles:

```python
class DashboardCategory(models.Model):
    ROLE_CHOICES = [
        ('', 'Default'),
        ('primary', 'Primary'),
        ('secondary', 'Secondary'),
    ]

    dashboard = models.ForeignKey(CustomDashboard, on_delete=models.CASCADE, related_name='dashboard_categories')
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, blank=True, choices=ROLE_CHOICES)
```

Roles are only meaningful for `income_tracking` type. For other types, all categories are treated equally.

## Dashboard Types

### 1. Category Trend (`category_trend`)

Replaces: Car dashboard.

- **Summary cards:** Total spend, monthly average, last month total, % of income
- **Chart 1:** Monthly stacked/grouped bar or line chart — one series per category
- **Chart 2:** Category breakdown doughnut
- All assigned categories used equally

### 2. Category Detail (`category_detail`)

Replaces: Car Gas, Car Parking dashboards.

- **Summary cards:** Total, monthly average, frequency (transaction count), last month
- **Chart 1:** Monthly spend bar chart
- **Chart 2:** Transaction frequency per month
- **Chart 3:** Amount distribution bands
- Uses first assigned category (or aggregates all)

### 3. Income Tracking (`income_tracking`)

Replaces: Salary dashboard.

- **Primary section:** Summary cards (last month, average, median, 12-month total), monthly trend bar with average line
- **Secondary section:** Bonus totals, event timeline table
- Categories with `role='primary'` → main income section
- Categories with `role='secondary'` → bonuses section

## UI Changes

### Sidebar

- **Financial section:** Keep Overview, Spending & Income (these use group-level queries, no changes needed)
- **Custom section:** Dynamic list of user's custom dashboards, with "Manage" link
- Remove hardcoded Car, Car Gas, Car Parking, Income Salary links

### Dashboard Management Page (`/dashboards/`)

- List user's custom dashboards with reorder support
- "Add Dashboard" button → form with:
  - Name (text input)
  - Type (dropdown: Category Trend / Category Detail / Income Tracking)
  - Categories (multi-select grouped by CategoryGroup)
  - For income_tracking: ability to mark categories as primary vs secondary
- Edit / Delete existing dashboards

### Custom Dashboard View (`/dashboards/<id>/`)

- Single view that renders differently based on `dashboard_type`
- Three templates: `dashboard_custom_trend.html`, `dashboard_custom_detail.html`, `dashboard_custom_income.html`

## URL Routes

```
/dashboards/                  → dashboard management (list, add)
/dashboards/<id>/             → view a custom dashboard
/dashboards/<id>/edit/        → edit dashboard config
/dashboards/<id>/delete/      → delete dashboard (POST)
```

## Migration Strategy

### Seed default dashboards

A data migration or management command creates default `CustomDashboard` entries for existing users based on current hardcoded dashboards:

- "Car Costs" → `category_trend` with [Car Gas, Car Insurance, Car Maintenance, Car Parking & Toll, Car Tax, Car Wash]
- "Car Gas" → `category_detail` with [Car Gas]
- "Car Parking" → `category_detail` with [Car Parking & Toll]
- "Income Salary" → `income_tracking` with primary=[Salary Main], secondary=[Salary Bonuses, Non-recurring]

Only creates these if the user has matching categories.

### Cleanup

- Remove old hardcoded views, URL routes, and templates
- Update sidebar to use dynamic dashboard list

## Implementation Order

1. Create models + migration
2. Dashboard management UI (list, add, edit, delete)
3. Category Trend view + template
4. Category Detail view + template
5. Income Tracking view + template
6. Dynamic sidebar
7. Seed defaults for existing users
8. Remove old hardcoded dashboards
9. Apply privacy mode toggle to all custom dashboard types
