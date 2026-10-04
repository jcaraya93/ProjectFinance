import json
import time
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import TruncMonth, TruncWeek, TruncDay, TruncQuarter, Abs

from core.models import CategoryNode, Transaction
from core.instrumentation import tracer, dashboard_duration


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def category_depths(user):
    """Return ({node_id: node}, {node_id: depth}) for the user's category tree; top level is depth 1."""
    nodes = {n.id: n for n in CategoryNode.objects.filter(user=user).select_related('group')}
    depths = {}

    def depth(node):
        if node.id not in depths:
            depths[node.id] = 1 if node.parent_id is None else depth(nodes[node.parent_id]) + 1
        return depths[node.id]

    for n in nodes.values():
        depth(n)
    return nodes, depths


def category_at_level(node, nodes, depths, level):
    while node is not None and level and depths[node.id] > level:
        node = nodes[node.parent_id]
    return node


def category_breakdown(qs, group_slug, amount_field, nodes, depths, level=None, limit=None):
    """Totals per category for one group, rolled up to `level` (None = as assigned).

    Nodes deeper than `level` are folded into their ancestor at that level; shallower ones stay as they are.
    """
    rows = (qs.filter(category_v2__group__slug=group_slug)
            .values('category_v2_id').annotate(abs_total=Sum(Abs(amount_field))))
    totals = {}
    for r in rows:
        node = category_at_level(nodes.get(r['category_v2_id']), nodes, depths, level)
        key = node.id if node else None
        totals[key] = totals.get(key, 0) + float(r['abs_total'] or 0)

    ordered = sorted(totals.items(), key=lambda kv: -kv[1])
    if limit:
        ordered = ordered[:limit]
    data = {'labels': [], 'values': [], 'colors': [], 'ids': []}
    for key, total in ordered:
        node = nodes.get(key)
        data['ids'].append(key)
        data['labels'].append(node.name if node else 'Uncategorized')
        data['values'].append(total)
        data['colors'].append((node.color if node else None) or '#6c757d')
    return data


def category_drilldown(qs, group_slug, amount_field, nodes, depths, max_level=None):
    """For every node with children: its children's subtree totals, keyed by node id.

    With max_level, only nodes above that level can be drilled into, so children never go deeper than it.

    Amounts assigned directly to the parent appear as an extra "<name> (direct)" entry with id None.
    """
    own = {}
    for r in (qs.filter(category_v2__group__slug=group_slug)
              .values('category_v2_id').annotate(t=Sum(Abs(amount_field)))):
        own[r['category_v2_id']] = float(r['t'] or 0)
    subtree = {}
    for node_id, total in own.items():
        node = nodes.get(node_id)
        while node is not None:
            subtree[node.id] = subtree.get(node.id, 0) + total
            node = nodes.get(node.parent_id) if node.parent_id else None

    children = {}
    for n in nodes.values():
        if n.parent_id:
            children.setdefault(n.parent_id, []).append(n)

    drill = {}
    for parent_id, kids in children.items():
        parent = nodes[parent_id]
        if parent.group.slug != group_slug or not subtree.get(parent_id):
            continue
        if max_level and depths[parent_id] >= max_level:
            continue
        items = [(k.id, k.name, k.color, subtree.get(k.id, 0)) for k in kids if subtree.get(k.id, 0)]
        if own.get(parent_id):
            items.append((None, f'{parent.name} (direct)', parent.color, own[parent_id]))
        items.sort(key=lambda it: -it[3])
        drill[parent_id] = {
            'name': parent.name,
            'ids': [i[0] for i in items],
            'labels': [i[1] for i in items],
            'colors': [i[2] or '#6c757d' for i in items],
            'values': [i[3] for i in items],
        }
    return drill


def expense_composition_timeline(user, display_currency, start_date, end_date, period_type, level, selected_categories=None):
    """Expense amounts by period and category, including empty intervals."""
    return category_composition_timeline(
        user, display_currency, start_date, end_date, period_type, level,
        group_slug='expense', selected_categories=selected_categories,
    )


def category_composition_timeline(user, display_currency, start_date, end_date, period_type, level,
                                  group_slug, selected_categories=None):
    """Amounts for one category group, including empty intervals and exact date bounds."""
    from django.db.models import Min, Max

    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    qs = Transaction.objects.filter(user=user, category_v2__group__slug=group_slug)
    if start_date:
        qs = qs.filter(date__gte=start_date)
    if end_date:
        qs = qs.filter(date__lte=end_date)
    weekly = period_type == 'month'
    data = {'labels': [], 'series': [], 'colors': [], 'category_ids': [], 'intervals': [],
            'grouping': 'Weekly' if weekly else 'Monthly'}
    bounds = qs.aggregate(first=Min('date'), last=Max('date'))
    start = start_date or bounds['first']
    end = end_date or bounds['last']
    if start is None or end is None:
        return data

    buckets = []
    cursor = start if weekly else start.replace(day=1)
    while cursor <= end:
        buckets.append(cursor)
        interval_start = max(cursor, start)
        if weekly:
            finish = min(cursor + timedelta(days=6), end)
            data['labels'].append(f'{cursor:%b} {cursor.day}-{finish.day}')
            cursor += timedelta(days=7)
        else:
            data['labels'].append(cursor.strftime('%b %Y'))
            cursor = date(cursor.year + cursor.month // 12, cursor.month % 12 + 1, 1)
            finish = min(cursor - timedelta(days=1), end)
        data['intervals'].append({'start': interval_start.isoformat(), 'end': finish.isoformat()})

    nodes, depths = category_depths(user)
    values = {}
    trunc = TruncDay if weekly else TruncMonth
    rows = (qs.annotate(interval=trunc('date')).values('interval', 'category_v2_id')
            .annotate(total=Sum(Abs(amount_field))).order_by())
    for row in rows:
        node = category_at_level(nodes[row['category_v2_id']], nodes, depths, level)
        if selected_categories is not None and node.id not in selected_categories:
            continue
        if not row['total']:
            continue
        interval = row['interval']
        if weekly:
            bucket = (interval - start).days // 7
        else:
            bucket = (interval.year - start.year) * 12 + interval.month - start.month
        amounts = values.setdefault(node.id, [Decimal(0) for _ in buckets])
        amounts[bucket] += row['total']

    ordered = sorted(values, key=lambda key: (-sum(values[key]), key))
    capped = group_slug == 'expense' and level == 2 and selected_categories is None
    visible = ordered[:10] if capped else ordered
    for key in visible:
        data['category_ids'].append(key)
        data['series'].append({'name': nodes[key].name, 'data': values[key]})
        data['colors'].append(nodes[key].color or '#6c757d')
    if capped and len(ordered) > 10:
        data['category_ids'].append(None)
        remaining = [sum((values[key][i] for key in ordered[10:]), Decimal(0)) for i in range(len(buckets))]
        data['series'].append({'name': 'Remaining categories', 'data': remaining})
        data['colors'].append('#adb5bd')
    return data


def get_dashboard_stats(user, start_date=None, end_date=None, display_currency='CRC', wallet_filter=None, groups=None, categories=None, time_group='monthly', category_level=None):
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
        qs = qs.filter(category_v2__group__slug__in=groups)
    if categories:
        qs = qs.filter(category_v2_id__in=categories)

    # ── Summary cards (using converted amounts) ───────────────
    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'

    # Monthly average
    from django.db.models.functions import TruncMonth as _TM
    from .income_categories import income_category_roles
    income_roles = income_category_roles(user)
    excluded_income_ids = income_roles['association'] | income_roles['bonus'] | income_roles['government']
    excluded_income_ids.update(CategoryNode.objects.filter(user=user, group__slug='income', name='Unclassified').values_list('pk', flat=True))
    income_filter = dict(category_v2__group__slug='income')
    income_exclude = dict(category_v2_id__in=excluded_income_ids)

    income_by_month = (
        qs.filter(**income_filter).exclude(**income_exclude)
        .annotate(month=_TM('date'))
        .values('month')
        .annotate(total=Sum(Abs(amount_field)))
    )
    expense_by_month = (
        qs.filter(category_v2__group__slug='expense')
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

    # Last statement period (based on most recent uploaded statement)
    from core.models import StatementImport, LogicalTransaction
    from datetime import date as date_cls, timedelta
    today = date_cls.today()

    latest_stmt = (
        StatementImport.objects.filter(user=user)
        .order_by('-statement_date')
        .first()
    )

    if latest_stmt and latest_stmt.statement_date:
        # Use the statement's month as the "last period"
        stmt_date = latest_stmt.statement_date
        last_period_start = stmt_date.replace(day=1)
        # End of that month
        if stmt_date.month == 12:
            last_period_end = stmt_date.replace(year=stmt_date.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            last_period_end = stmt_date.replace(month=stmt_date.month + 1, day=1) - timedelta(days=1)
        last_period_name = f"{last_period_start.strftime('%b %Y')} (latest statement)"
    else:
        # Fallback to calendar last month
        last_period_end = today.replace(day=1) - timedelta(days=1)
        last_period_start = last_period_end.replace(day=1)
        last_period_name = last_period_start.strftime('%b %Y')

    last_month_income = (
        qs.filter(**income_filter, date__gte=last_period_start, date__lte=last_period_end)
        .exclude(**income_exclude)
        .aggregate(total=Sum(Abs(amount_field)))['total']
        or Decimal('0')
    )
    last_month_expenses = (
        qs.filter(category_v2__group__slug='expense', date__gte=last_period_start, date__lte=last_period_end)
        .aggregate(total=Sum(Abs(amount_field)))['total']
        or Decimal('0')
    )
    last_month_cashflow = last_month_income - last_month_expenses
    last_month_name = last_period_name

    total_transfers = qs.filter(category_v2__group__slug='transfer').count()

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
        qs.filter(category_v2__group__slug='expense')
        .annotate(month=_TM('date'))
        .values('month', 'category_v2__name', 'category_v2__color')
        .annotate(total=Sum(Abs(amount_field)))
        .order_by('category_v2__name', 'month')
    )
    # Build {cat: [monthly totals]} and collect colors
    cat_months = _defaultdict(list)
    cat_colors_map = {}
    for r in expense_cat_monthly:
        cat = r['category_v2__name']
        cat_months[cat].append(float(r['total'] or 0))
        cat_colors_map[cat] = r['category_v2__color'] or '#6c757d'

    # Last month per category
    last_month_by_cat = {}
    lm_cats = (
        qs.filter(category_v2__group__slug='expense',
                  date__gte=last_period_start, date__lte=last_period_end)
        .values('category_v2__name')
        .annotate(total=Sum(Abs(amount_field)))
    )
    for r in lm_cats:
        last_month_by_cat[r['category_v2__name']] = float(r['total'] or 0)

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
        cat = r['category_v2__name']
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

        expense_txns = qs.filter(category_v2__group__slug='expense').values_list('date', amount_field)
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
            qs.filter(category_v2__group__slug='expense')
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
            'income': [float(income_lookup.get(p) or 0) for p in periods_set],
            'expenses': [float(expense_lookup.get(p) or 0) for p in periods_set],
        }

    # ── Category breakdowns (doughnuts and top-10 bars) at the chosen level ──
    nodes, depths = category_depths(user)
    expense_category_data = category_breakdown(qs, 'expense', amount_field, nodes, depths, category_level)
    income_category_data = category_breakdown(qs, 'income', amount_field, nodes, depths, category_level)
    top_categories_data = category_breakdown(qs, 'expense', amount_field, nodes, depths, category_level, limit=10)
    expense_drill_data = category_drilldown(qs, 'expense', amount_field, nodes, depths, max_level=category_level and 2)
    income_drill_data = category_drilldown(qs, 'income', amount_field, nodes, depths, max_level=category_level and 2)
    top_income_data = category_breakdown(qs, 'income', amount_field, nodes, depths, category_level, limit=10)

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
        'expense_drill_data': json.dumps(expense_drill_data, cls=DecimalEncoder),
        'income_category_data': json.dumps(income_category_data, cls=DecimalEncoder),
        'income_drill_data': json.dumps(income_drill_data, cls=DecimalEncoder),
        'top_categories_data': json.dumps(top_categories_data, cls=DecimalEncoder),
        'top_income_data': json.dumps(top_income_data, cls=DecimalEncoder),
        'trend_data': json.dumps(trend_data, cls=DecimalEncoder),
        'cat_comparison_data': json.dumps(cat_comparison_data, cls=DecimalEncoder),
        'cat_timeline_data': json.dumps(cat_timeline_data, cls=DecimalEncoder),
    }
