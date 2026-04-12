import json
import time
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import TruncMonth, TruncWeek, TruncDay, TruncQuarter, Abs

from transactions.models import Transaction
from transactions.instrumentation import tracer, dashboard_duration


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def get_dashboard_stats(user, start_date=None, end_date=None, display_currency='CRC', wallet_filter=None, groups=None, categories=None, time_group='monthly'):
    """Return all dashboard statistics."""
    with tracer.start_as_current_span("stats.get_dashboard_stats") as span:
        t0 = time.monotonic()
        span.set_attribute("dashboard.user_id", user.id)
        span.set_attribute("dashboard.display_currency", display_currency)
        span.set_attribute("dashboard.time_group", time_group)
        if start_date:
            span.set_attribute("dashboard.start_date", str(start_date))
        if end_date:
            span.set_attribute("dashboard.end_date", str(end_date))

        qs = Transaction.objects.filter(user=user)

    if start_date:
        qs = qs.filter(date__gte=start_date)
    if end_date:
        qs = qs.filter(date__lte=end_date)
    if wallet_filter:
        qs = qs.filter(wallet_filter)
    if groups:
        qs = qs.filter(category__group__slug__in=groups)
    if categories:
        qs = qs.filter(category_id__in=categories)

    # ── Summary cards (using converted amounts) ───────────────
    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'

    # Monthly average
    from django.db.models.functions import TruncMonth as _TM
    EXCLUDED_INCOME = ['CDP Interest', 'Non-recurring', 'Salary Bonuses', 'Default']
    income_filter = dict(category__group__slug='income')
    income_exclude = dict(category__name__in=EXCLUDED_INCOME)

    income_by_month = (
        qs.filter(**income_filter).exclude(**income_exclude)
        .annotate(month=_TM('date'))
        .values('month')
        .annotate(total=Sum(Abs(amount_field)))
    )
    expense_by_month = (
        qs.filter(category__group__slug='expense')
        .annotate(month=_TM('date'))
        .values('month')
        .annotate(total=Sum(Abs(amount_field)))
    )

    income_months = sorted([r['total'] for r in income_by_month if r['total']])
    expense_months = sorted([r['total'] for r in expense_by_month if r['total']])

    def _median(values):
        if not values:
            return Decimal('0')
        n = len(values)
        if n % 2 == 1:
            return values[n // 2]
        return (values[n // 2 - 1] + values[n // 2]) / 2

    median_income = _median(income_months)
    median_expenses = _median(expense_months)
    median_cashflow = median_income - median_expenses

    avg_income = sum(income_months) / len(income_months) if income_months else Decimal('0')
    avg_expenses = sum(expense_months) / len(expense_months) if expense_months else Decimal('0')
    avg_cashflow = avg_income - avg_expenses

    # Last complete month
    from datetime import date as date_cls
    today = date_cls.today()
    last_month_end = today.replace(day=1) - __import__('datetime').timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    last_month_income = (
        qs.filter(**income_filter, date__gte=last_month_start, date__lte=last_month_end)
        .exclude(**income_exclude)
        .aggregate(total=Sum(Abs(amount_field)))['total']
        or Decimal('0')
    )
    last_month_expenses = (
        qs.filter(category__group__slug='expense', date__gte=last_month_start, date__lte=last_month_end)
        .aggregate(total=Sum(Abs(amount_field)))['total']
        or Decimal('0')
    )
    last_month_cashflow = last_month_income - last_month_expenses
    last_month_name = last_month_start.strftime('%b %Y')

    total_transfers = qs.filter(category__group__slug='transaction').count()

    def _pct_change(current, median):
        if not median:
            return 0
        return float((current - median) / median * 100)

    summary = {
        'median_income': median_income,
        'median_expenses': median_expenses,
        'median_cashflow': median_cashflow,
        'avg_income': avg_income,
        'avg_expenses': avg_expenses,
        'avg_cashflow': avg_cashflow,
        'last_month_income': last_month_income,
        'last_month_expenses': last_month_expenses,
        'last_month_cashflow': last_month_cashflow,
        'last_month_name': last_month_name,
        'income_pct': _pct_change(last_month_income, median_income),
        'expenses_pct': _pct_change(last_month_expenses, median_expenses),
        'cashflow_pct': _pct_change(last_month_cashflow, median_cashflow) if median_cashflow else 0,
        'total_transfers': total_transfers,
        'currency_symbol': currency_symbol,
    }

    # ── Per-category: last month vs median (horizontal grouped bar) ──
    from collections import defaultdict as _defaultdict
    expense_cat_monthly = (
        qs.filter(category__group__slug='expense')
        .annotate(month=_TM('date'))
        .values('month', 'category__name', 'category__color')
        .annotate(total=Sum(Abs(amount_field)))
        .order_by('category__name', 'month')
    )
    # Build {cat: [monthly totals]} and collect colors
    cat_months = _defaultdict(list)
    cat_colors_map = {}
    for r in expense_cat_monthly:
        cat = r['category__name']
        cat_months[cat].append(float(r['total'] or 0))
        cat_colors_map[cat] = r['category__color'] or '#6c757d'

    # Last month per category
    last_month_by_cat = {}
    lm_cats = (
        qs.filter(category__group__slug='expense',
                  date__gte=last_month_start, date__lte=last_month_end)
        .values('category__name')
        .annotate(total=Sum(Abs(amount_field)))
    )
    for r in lm_cats:
        last_month_by_cat[r['category__name']] = float(r['total'] or 0)

    # Build sorted list by median descending
    cat_comparison = []
    for cat, totals in cat_months.items():
        sorted_t = sorted(totals)
        n = len(sorted_t)
        med = sorted_t[n // 2] if n % 2 else (sorted_t[n // 2 - 1] + sorted_t[n // 2]) / 2
        lm = last_month_by_cat.get(cat, 0)
        cat_comparison.append({
            'name': cat,
            'median': med,
            'last_month': lm,
            'color': cat_colors_map.get(cat, '#6c757d'),
        })
    cat_comparison.sort(key=lambda x: x['median'], reverse=True)

    cat_comparison_data = {
        'labels': [c['name'] for c in cat_comparison],
        'median': [c['median'] for c in cat_comparison],
        'last_month': [c['last_month'] for c in cat_comparison],
        'colors': [c['color'] for c in cat_comparison],
    }

    # ── Per-category monthly time series (for over-time charts) ──
    cat_monthly_map = _defaultdict(dict)  # {cat: {month_str: total}}
    all_months_set = set()
    for r in expense_cat_monthly:
        cat = r['category__name']
        m = r['month'].strftime('%Y-%m')
        cat_monthly_map[cat][m] = float(r['total'] or 0)
        all_months_set.add(m)
    all_months_sorted = sorted(all_months_set)

    # Sort categories by total spend descending, take top 10
    cat_totals_sorted = sorted(cat_months.keys(), key=lambda c: sum(cat_months[c]), reverse=True)
    top_cats_timeline = cat_totals_sorted[:10]

    cat_timeline_data = {
        'months': all_months_sorted,
        'categories': [],
    }
    for cat in top_cats_timeline:
        cat_timeline_data['categories'].append({
            'name': cat,
            'color': cat_colors_map.get(cat, '#6c757d'),
            'data': [cat_monthly_map[cat].get(m, 0) for m in all_months_sorted],
        })

    # ── Time-grouped income vs expenses (bar chart) ──────────
    if time_group == 'biweekly':
        # Semi-monthly: group by 1st and 15th of each month
        from collections import defaultdict
        from datetime import date as _date

        def _semi_month_key(d):
            return _date(d.year, d.month, 1 if d.day < 15 else 15)

        expense_txns = qs.filter(category__group__slug='expense').values_list('date', amount_field)
        income_txns = qs.filter(**income_filter).exclude(**income_exclude).values_list('date', amount_field)

        expense_semi = defaultdict(Decimal)
        for d, amt in expense_txns:
            if amt:
                expense_semi[_semi_month_key(d)] += abs(amt)

        income_semi = defaultdict(Decimal)
        for d, amt in income_txns:
            if amt:
                income_semi[_semi_month_key(d)] += abs(amt)

        periods_set = sorted(set(list(expense_semi.keys()) + list(income_semi.keys())))
        monthly_data = {
            'labels': [p.strftime('%Y-%m-%d') for p in periods_set],
            'income': [float(income_semi.get(p, 0)) for p in periods_set],
            'expenses': [float(expense_semi.get(p, 0)) for p in periods_set],
        }
    else:
        trunc_map = {
            'daily': (TruncDay, '%Y-%m-%d'),
            'weekly': (TruncWeek, '%Y-%m-%d'),
            'monthly': (TruncMonth, '%Y-%m'),
            'quarterly': (TruncQuarter, None),
        }
        trunc_func, date_fmt = trunc_map.get(time_group, (TruncMonth, '%Y-%m'))

        if time_group == 'quarterly':
            date_fmt_fn = lambda d: f"{d.year}-Q{(d.month - 1) // 3 + 1}"
        else:
            date_fmt_fn = lambda d: d.strftime(date_fmt)

        expense_grouped = (
            qs.filter(category__group__slug='expense')
            .annotate(period=trunc_func('date'))
            .values('period')
            .annotate(total=Sum(Abs(amount_field)))
            .order_by('period')
        )
        income_grouped = (
            qs.filter(**income_filter).exclude(**income_exclude)
            .annotate(period=trunc_func('date'))
            .values('period')
            .annotate(total=Sum(Abs(amount_field)))
            .order_by('period')
        )

        periods_set = sorted(set(
            [r['period'] for r in expense_grouped]
            + [r['period'] for r in income_grouped]
        ))
        expense_lookup = {r['period']: r['total'] for r in expense_grouped}
        income_lookup = {r['period']: r['total'] for r in income_grouped}

        monthly_data = {
            'labels': [date_fmt_fn(p) for p in periods_set],
            'income': [float(income_lookup.get(p, 0)) for p in periods_set],
            'expenses': [float(expense_lookup.get(p, 0)) for p in periods_set],
        }

    # ── Expense category breakdown (doughnut) ─────────────────
    expense_cats = (
        qs.filter(category__group__slug='expense')
        .values('category__name', 'category__color')
        .annotate(abs_total=Sum(Abs(amount_field)))
        .order_by('-abs_total')
    )
    expense_category_data = {'labels': [], 'values': [], 'colors': []}
    for r in expense_cats:
        expense_category_data['labels'].append(r['category__name'] or 'Uncategorized')
        expense_category_data['values'].append(float(r['abs_total'] or 0))
        expense_category_data['colors'].append(r['category__color'] or '#6c757d')

    # ── Income category breakdown (doughnut) ──────────────────
    income_cats = (
        qs.filter(category__group__slug='income')
        .values('category__name', 'category__color')
        .annotate(abs_total=Sum(Abs(amount_field)))
        .order_by('-abs_total')
    )
    income_category_data = {'labels': [], 'values': [], 'colors': []}
    for r in income_cats:
        income_category_data['labels'].append(r['category__name'] or 'Uncategorized')
        income_category_data['values'].append(float(r['abs_total'] or 0))
        income_category_data['colors'].append(r['category__color'] or '#6c757d')

    # ── Top spending categories (horizontal bar, top 10) ──────
    top_cats = (
        qs.filter(category__group__slug='expense')
        .values('category__name', 'category__color')
        .annotate(abs_total=Sum(Abs(amount_field)))
        .order_by('-abs_total')[:10]
    )
    top_categories_data = {'labels': [], 'values': [], 'colors': []}
    for r in top_cats:
        top_categories_data['labels'].append(r['category__name'] or 'Uncategorized')
        top_categories_data['values'].append(float(r['abs_total'] or 0))
        top_categories_data['colors'].append(r['category__color'] or '#6c757d')

    # ── Top income categories (horizontal bar, top 10) ────────
    top_income_cats = (
        qs.filter(category__group__slug='income')
        .values('category__name', 'category__color')
        .annotate(abs_total=Sum(Abs(amount_field)))
        .order_by('-abs_total')[:10]
    )
    top_income_data = {'labels': [], 'values': [], 'colors': []}
    for r in top_income_cats:
        top_income_data['labels'].append(r['category__name'] or 'Uncategorized')
        top_income_data['values'].append(float(r['abs_total'] or 0))
        top_income_data['colors'].append(r['category__color'] or '#6c757d')

    # ── Monthly trend (dual line) ─────────────────────────────
    trend_data = {
        'labels': monthly_data['labels'],
        'income': monthly_data['income'],
        'expenses': monthly_data['expenses'],
    }

    elapsed_ms = (time.monotonic() - t0) * 1000
    dashboard_duration.record(elapsed_ms, {"dashboard": "overview"})
    span.set_attribute("dashboard.duration_ms", elapsed_ms)

    return {
        'summary': summary,
        'monthly_data': json.dumps(monthly_data, cls=DecimalEncoder),
        'expense_category_data': json.dumps(expense_category_data, cls=DecimalEncoder),
        'income_category_data': json.dumps(income_category_data, cls=DecimalEncoder),
        'top_categories_data': json.dumps(top_categories_data, cls=DecimalEncoder),
        'top_income_data': json.dumps(top_income_data, cls=DecimalEncoder),
        'trend_data': json.dumps(trend_data, cls=DecimalEncoder),
        'cat_comparison_data': json.dumps(cat_comparison_data, cls=DecimalEncoder),
        'cat_timeline_data': json.dumps(cat_timeline_data, cls=DecimalEncoder),
    }
