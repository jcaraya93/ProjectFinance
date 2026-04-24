import functools
import json
import time
from datetime import date
from decimal import Decimal

from django.shortcuts import render
from django.contrib.auth.decorators import login_required

from ..models import Transaction, LogicalTransaction, Category, ClassificationRule
from ..services.stats import get_dashboard_stats
from ..instrumentation import tracer, dashboard_duration

__all__ = [
    'dashboard',
    'transaction_health_dashboard',
    'rule_matching_dashboard',
    'default_buckets_dashboard',
    'spending_income_dashboard',
    'chart_comparison',
    'car_dashboard',
    'car_gas_dashboard',
    'car_parking_dashboard',
    'income_salary_dashboard',
]


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


CHART_COLORS = {
    'income': 'rgba(25, 135, 84, 0.8)',
    'expense': 'rgba(220, 53, 69, 0.8)',
    'primary': 'rgba(13, 110, 253, 0.8)',
    'info': 'rgba(13, 202, 240, 0.8)',
    'warning': 'rgba(255, 193, 7, 0.8)',
    'secondary': 'rgba(108, 117, 125, 0.8)',
}


def dashboard_view(name, template, default_time_group='monthly'):
    """Decorator that handles common dashboard boilerplate:
    - login_required
    - OpenTelemetry span + duration metric
    - display_currency and time_group query params
    """
    def decorator(view_func):
        @login_required
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            with tracer.start_as_current_span(f"view.{name}") as span:
                t0 = time.monotonic()
                display_currency = request.GET.get('display_currency', 'CRC')
                time_group = request.GET.get('time_group', default_time_group)

                context = view_func(request, display_currency=display_currency, time_group=time_group, *args, **kwargs)

                context.setdefault('display_currency', display_currency)
                context.setdefault('time_group', time_group)

                elapsed_ms = (time.monotonic() - t0) * 1000
                span.set_attribute("dashboard.type", name)
                dashboard_duration.record(elapsed_ms, {"dashboard": name})
                return render(request, template, context)
        return wrapper
    return decorator


@dashboard_view("overview", "core/dashboard.html", default_time_group="biweekly")
def dashboard(request, display_currency, time_group):
    """Overview dashboard — last 12 months, no user filters."""
    from datetime import timedelta
    today = date.today()
    start_12m = (today.replace(day=1) - timedelta(days=365)).replace(day=1).isoformat()

    context = get_dashboard_stats(request.user,
        start_date=start_12m,
        display_currency=display_currency,
        time_group=time_group,
    )
    return context


@dashboard_view("transaction_health", "core/dashboard_transaction_health.html")
def transaction_health_dashboard(request, display_currency, time_group):
    """Dashboard showing classification health: unclassified %, rule coverage, manual vs auto."""
    from collections import defaultdict
    from django.db.models import Count, Q
    from django.db.models.functions import TruncMonth

    user = request.user
    qs = LogicalTransaction.objects.filter(user=user)

    total = qs.count()
    method_counts = dict(
        qs.values('classification_method')
        .annotate(c=Count('id'))
        .values_list('classification_method', 'c')
    )
    unclassified_count = method_counts.get('unclassified', 0)
    rule_count = method_counts.get('rule', 0)
    manual_count = method_counts.get('manual', 0)

    def _pct(n):
        return (n / total * 100) if total else 0

    # Classification doughnut
    classification_data = {
        'labels': ['Rule', 'Manual', 'Unclassified'],
        'values': [rule_count, manual_count, unclassified_count],
        'colors': [CHART_COLORS['primary'], CHART_COLORS['info'], CHART_COLORS['warning']],
    }

    # Monthly classification trend
    monthly_methods = (
        qs.annotate(month=TruncMonth('date'))
        .values('month', 'classification_method')
        .annotate(c=Count('id'))
        .order_by('month')
    )
    month_set = sorted(set(r['month'] for r in monthly_methods))
    month_map = defaultdict(lambda: defaultdict(int))
    for r in monthly_methods:
        month_map[r['month']][r['classification_method']] = r['c']

    monthly_trend_data = {
        'labels': [m.strftime('%Y-%m') for m in month_set],
        'rule': [month_map[m]['rule'] for m in month_set],
        'manual': [month_map[m]['manual'] for m in month_set],
        'unclassified': [month_map[m]['unclassified'] for m in month_set],
    }

    # Category coverage (how each category is classified)
    cat_methods = (
        qs.exclude(category__isnull=True)
        .values('category__name', 'category__group__slug', 'classification_method')
        .annotate(c=Count('id'))
        .order_by('category__group__slug', 'category__name')
    )
    cat_data = defaultdict(lambda: defaultdict(int))
    for r in cat_methods:
        label = f"{r['category__name']} ({r['category__group__slug']})"
        cat_data[label][r['classification_method']] = r['c']
    # Sort by total count descending
    sorted_cats = sorted(cat_data.keys(), key=lambda c: sum(cat_data[c].values()), reverse=True)
    category_coverage_data = {
        'labels': sorted_cats,
        'rule': [cat_data[c]['rule'] for c in sorted_cats],
        'manual': [cat_data[c]['manual'] for c in sorted_cats],
        'unclassified': [cat_data[c]['unclassified'] for c in sorted_cats],
    }
    category_chart_height = max(300, len(sorted_cats) * 28)

    # Recent unclassified
    recent_unclassified = list(
        qs.filter(classification_method='unclassified')
        .select_related(
            'raw_transaction__ledger__statement_import__account',
        )
        .order_by('-date', '-id')[:20]
    )

    context = {
        'total_transactions': total,
        'unclassified_count': unclassified_count,
        'unclassified_pct': _pct(unclassified_count),
        'rule_count': rule_count,
        'rule_pct': _pct(rule_count),
        'manual_count': manual_count,
        'manual_pct': _pct(manual_count),
        'classification_data': json.dumps(classification_data),
        'monthly_trend_data': json.dumps(monthly_trend_data),
        'category_coverage_data': json.dumps(category_coverage_data),
        'category_chart_height': category_chart_height,
        'recent_unclassified': recent_unclassified,
    }
    return context


@dashboard_view("rule_matching", "core/dashboard_rule_matching.html")
def rule_matching_dashboard(request, display_currency, time_group):
    """Dashboard dedicated to classification rule matching analysis."""
    from collections import defaultdict
    from django.db.models import Count, Q, Max
    from django.db.models.functions import TruncMonth

    user = request.user
    rules_qs = ClassificationRule.objects.filter(user=user)
    txn_qs = LogicalTransaction.objects.filter(user=user)

    # Summary
    total_rules = rules_qs.count()
    rules_with_counts = rules_qs.annotate(match_count=Count('matched_transactions'))
    active_rules = rules_with_counts.filter(match_count__gt=0).count()
    unused_count = total_rules - active_rules
    total_rule_matched = txn_qs.filter(classification_method='rule').count()
    avg_matches = (total_rule_matched / active_rules) if active_rules else 0

    # Rules by group doughnut
    GROUP_LABELS = {'expense': 'Expense', 'income': 'Income', 'transaction': 'Transfer', 'unclassified': 'Unclassified'}
    GROUP_CHART_COLORS = {
        'expense': CHART_COLORS['expense'], 'income': CHART_COLORS['income'],
        'transaction': CHART_COLORS['primary'], 'unclassified': CHART_COLORS['warning'],
    }
    group_counts = (
        rules_qs.values('category__group__slug')
        .annotate(c=Count('id'))
        .order_by('category__group__slug')
    )
    rules_by_group = {
        'labels': [GROUP_LABELS.get(r['category__group__slug'], r['category__group__slug']) for r in group_counts],
        'values': [r['c'] for r in group_counts],
        'colors': [GROUP_CHART_COLORS.get(r['category__group__slug'], CHART_COLORS['secondary']) for r in group_counts],
    }

    # Monthly rule-matched transactions trend
    monthly_rule = (
        txn_qs.filter(classification_method='rule')
        .annotate(month=TruncMonth('date'))
        .values('month')
        .annotate(c=Count('id'))
        .order_by('month')
    )
    rule_activity_data = {
        'labels': [r['month'].strftime('%Y-%m') for r in monthly_rule],
        'values': [r['c'] for r in monthly_rule],
    }

    # Top matching rules
    top_rules = list(
        rules_with_counts
        .filter(match_count__gt=0)
        .order_by('-match_count')
        .values('description', 'category__name', 'category__color', 'category__group__slug', 'match_count')[:20]
    )

    # Unused rules
    unused_rules = list(
        rules_with_counts
        .filter(match_count=0)
        .values('description', 'category__name', 'category__color', 'category__group__slug')
        .order_by('category__group__slug', 'category__name', 'description')
    )

    context = {
        'total_rules': total_rules,
        'active_rules': active_rules,
        'unused_count': unused_count,
        'total_rule_matched': total_rule_matched,
        'avg_matches': avg_matches,
        'rules_by_group_data': json.dumps(rules_by_group, cls=DecimalEncoder),
        'rule_activity_data': json.dumps(rule_activity_data, cls=DecimalEncoder),
        'top_rules': top_rules,
        'unused_rules': unused_rules,
    }
    return context


@dashboard_view("default_buckets", "core/dashboard_default_buckets.html")
def default_buckets_dashboard(request, display_currency, time_group):
    """Dashboard for transactions in the Default category of each group."""
    from collections import defaultdict
    from django.db.models import Count, Sum, Q
    from django.db.models.functions import TruncMonth, Abs

    user = request.user
    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'

    default_name = Category.UNCLASSIFIED_NAME  # 'Default'
    qs = LogicalTransaction.objects.filter(
        user=user, category__name=default_name,
    ).select_related('category__group')

    total_all = LogicalTransaction.objects.filter(user=user).count()
    total_default = qs.count()

    # Per-group summary
    GROUP_LABELS = {'expense': 'Expense', 'income': 'Income', 'transaction': 'Transfer', 'unclassified': 'Unclassified'}
    GROUP_COLORS = {'expense': 'danger', 'income': 'success', 'transaction': 'primary', 'unclassified': 'warning'}
    group_stats_raw = (
        qs.values('category__group__slug')
        .annotate(count=Count('id'), total_amount=Sum(Abs(amount_field)))
        .order_by('category__group__slug')
    )
    group_stats = []
    for r in group_stats_raw:
        slug = r['category__group__slug']
        group_stats.append({
            'slug': slug,
            'label': GROUP_LABELS.get(slug, slug),
            'color': GROUP_COLORS.get(slug, 'secondary'),
            'count': r['count'],
            'amount': r['total_amount'] or Decimal('0'),
        })

    # Doughnut: Default distribution by group
    group_doughnut = {
        'labels': [g['label'] for g in group_stats],
        'values': [g['count'] for g in group_stats],
        'colors': [
            {'expense': 'rgba(220,53,69,0.8)', 'income': 'rgba(25,135,84,0.8)',
             'transaction': 'rgba(13,110,253,0.8)', 'unclassified': 'rgba(255,193,7,0.8)'
             }.get(g['slug'], 'rgba(108,117,125,0.8)')
            for g in group_stats
        ],
    }

    # Monthly trend of Default transactions by group
    monthly_raw = (
        qs.annotate(month=TruncMonth('date'))
        .values('month', 'category__group__slug')
        .annotate(c=Count('id'))
        .order_by('month')
    )
    month_set = sorted(set(r['month'] for r in monthly_raw))
    month_map = defaultdict(lambda: defaultdict(int))
    for r in monthly_raw:
        month_map[r['month']][r['category__group__slug']] = r['c']

    active_slugs = sorted(set(r['category__group__slug'] for r in monthly_raw))
    monthly_trend = {
        'labels': [m.strftime('%Y-%m') for m in month_set],
        'datasets': [
            {
                'slug': slug,
                'label': GROUP_LABELS.get(slug, slug),
                'data': [month_map[m][slug] for m in month_set],
            }
            for slug in active_slugs
        ],
    }

    # Top repeated descriptions in Default (rule candidates)
    desc_counts = (
        qs.values('description', 'category__group__slug')
        .annotate(count=Count('id'), total_amount=Sum(Abs(amount_field)))
        .filter(count__gte=2)
        .order_by('-count')[:20]
    )
    top_descriptions = [
        {
            'description': r['description'],
            'group': GROUP_LABELS.get(r['category__group__slug'], r['category__group__slug']),
            'group_slug': r['category__group__slug'],
            'count': r['count'],
            'total_amount': r['total_amount'] or Decimal('0'),
        }
        for r in desc_counts
    ]

    context = {
        'currency_symbol': currency_symbol,
        'total_all': total_all,
        'total_default': total_default,
        'default_pct': (total_default / total_all * 100) if total_all else 0,
        'group_stats': group_stats,
        'group_doughnut_data': json.dumps(group_doughnut, cls=DecimalEncoder),
        'monthly_trend_data': json.dumps(monthly_trend, cls=DecimalEncoder),
        'top_descriptions': top_descriptions,
    }
    return context


@dashboard_view("spending_income", "core/dashboard_spending_income.html")
def spending_income_dashboard(request, display_currency, time_group):
    """Spending & Income breakdown dashboard — last 12 months."""
    from datetime import timedelta
    today = date.today()
    start_12m = (today.replace(day=1) - timedelta(days=365)).replace(day=1).isoformat()

    return get_dashboard_stats(request.user,
        start_date=start_12m,
        display_currency=display_currency,
    )


@dashboard_view("chart_comparison", "core/chart_comparison.html")
def chart_comparison(request, display_currency, time_group):
    """Temporary test page to compare chart types for category expenses over time."""
    from datetime import timedelta
    today = date.today()
    start_12m = (today.replace(day=1) - timedelta(days=365)).replace(day=1).isoformat()

    return get_dashboard_stats(request.user,
        start_date=start_12m,
        display_currency=display_currency,
    )


@dashboard_view("car", "core/dashboard_car.html")
def car_dashboard(request, display_currency, time_group):
    """Dashboard focused on car-related expenses with multiple sections."""
    from datetime import timedelta
    from django.db.models import Sum, Count, Min, Max
    from django.db.models.functions import TruncMonth, Abs

    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'

    CAR_CATEGORIES = ['Car Gas', 'Car Insurance', 'Car Maintenance', 'Car Parking & Toll', 'Car Tax', 'Car Wash']
    SALARY_CATEGORIES = ['Salary Main', 'Salary Bonuses']

    abs_field = Abs(amount_field)

    # ── Base querysets ──
    car_qs = Transaction.objects.filter(user=request.user).filter(
        category__name__in=CAR_CATEGORIES, **{f'{amount_field}__isnull': False})
    salary_qs = Transaction.objects.filter(user=request.user).filter(
        category__name__in=SALARY_CATEGORIES, **{f'{amount_field}__isnull': False})

    # ── Monthly aggregations ──
    monthly_car = (car_qs.annotate(month=TruncMonth('date'))
        .values('month', 'category__name').annotate(total=Sum(abs_field)).order_by('month'))
    monthly_salary = (salary_qs.annotate(month=TruncMonth('date'))
        .values('month').annotate(total=Sum(amount_field)).order_by('month'))
    salary_by_month = {r['month'].strftime('%Y-%m'): float(r['total'] or 0) for r in monthly_salary}

    months_data = {}
    for r in monthly_car:
        m = r['month'].strftime('%Y-%m')
        cat = r['category__name']
        if m not in months_data:
            months_data[m] = {c: 0 for c in CAR_CATEGORIES}
            months_data[m]['_total'] = 0
        months_data[m][cat] = float(r['total'] or 0)
        months_data[m]['_total'] += float(r['total'] or 0)
    sorted_months = sorted(months_data.keys())

    # ── OVERVIEW section ──
    monthly_totals = [months_data[m]['_total'] for m in sorted_months]
    avg_monthly = sum(monthly_totals) / len(monthly_totals) if monthly_totals else 0
    median_monthly = sorted(monthly_totals)[len(monthly_totals) // 2] if monthly_totals else 0
    last_month = sorted_months[-1] if sorted_months else ''
    last_month_total = months_data.get(last_month, {}).get('_total', 0)
    last_month_salary = salary_by_month.get(last_month, 0)
    salary_pct = (last_month_total / last_month_salary * 100) if last_month_salary else 0
    median_pct = ((last_month_total - median_monthly) / median_monthly * 100) if median_monthly else 0
    tco_total = float(car_qs.aggregate(t=Sum(abs_field))['t'] or 0)
    car_last_year = sum(monthly_totals[-12:]) if monthly_totals else 0

    category_totals = list(car_qs.values('category__name', 'category__color')
        .annotate(total=Sum(abs_field)).order_by('-total'))
    cat_colors = {r['category__name']: r['category__color'] for r in category_totals}
    defaults = {'Car Gas': '#2980b9', 'Car Insurance': '#d35400', 'Car Maintenance': '#5dade2', 'Car Parking & Toll': '#607d8b', 'Car Tax': '#8e44ad', 'Car Wash': '#1abc9c'}
    for c in CAR_CATEGORIES:
        cat_colors.setdefault(c, defaults.get(c, '#6c757d'))

    trend_datasets = {cat: [months_data.get(m, {}).get(cat, 0) for m in sorted_months] for cat in CAR_CATEGORIES}
    pct_trend = [(months_data.get(m, {}).get('_total', 0) / salary_by_month[m] * 100) if salary_by_month.get(m, 0) > 0 else 0 for m in sorted_months]

    # ── GAS section ──
    gas_qs = Transaction.objects.filter(user=request.user).filter(category__name='Car Gas', **{f'{amount_field}__isnull': False})
    monthly_gas = (gas_qs.annotate(month=TruncMonth('date')).values('month')
        .annotate(count=Count('id'), total=Sum(abs_field), avg=Sum(abs_field) / Count('id'))
        .order_by('month'))
    gas_by_month = {r['month'].strftime('%Y-%m'): {
        'count': r['count'], 'total': float(r['total'] or 0), 'avg': float(r['avg'] or 0)
    } for r in monthly_gas}
    gas_counts = [gas_by_month.get(m, {}).get('count', 0) for m in sorted_months]
    gas_avg_per = [gas_by_month.get(m, {}).get('avg', 0) for m in sorted_months]
    gas_monthly_spend = [gas_by_month.get(m, {}).get('total', 0) for m in sorted_months]
    total_fillups = sum(gas_counts)
    avg_fillups = total_fillups / len(sorted_months) if sorted_months else 0
    # Days between fill-ups
    gas_dates = list(gas_qs.order_by('date').values_list('date', flat=True))
    if len(gas_dates) > 1:
        gaps = [(gas_dates[i+1] - gas_dates[i]).days for i in range(len(gas_dates)-1)]
        avg_days_between = sum(gaps) / len(gaps)
    else:
        avg_days_between = 0

    # Gas spend split: above/below 10000 CRC threshold
    threshold = 10000 if display_currency == 'CRC' else 20  # ~$20 USD equivalent
    gas_above = (gas_qs.filter(**{f'{amount_field}__gte': threshold})
        .annotate(month=TruncMonth('date')).values('month')
        .annotate(total=Sum(abs_field), count=Count('id')).order_by('month'))
    gas_below = (gas_qs.filter(**{f'{amount_field}__lt': threshold})
        .annotate(month=TruncMonth('date')).values('month')
        .annotate(total=Sum(abs_field), count=Count('id')).order_by('month'))
    # Also handle negative amounts (credit card gas is positive, debit is negative)
    gas_above_neg = (gas_qs.filter(**{f'{amount_field}__lte': -threshold})
        .annotate(month=TruncMonth('date')).values('month')
        .annotate(total=Sum(abs_field), count=Count('id')).order_by('month'))
    gas_below_neg = (gas_qs.filter(**{f'{amount_field}__gt': -threshold, f'{amount_field}__lt': 0})
        .annotate(month=TruncMonth('date')).values('month')
        .annotate(total=Sum(abs_field), count=Count('id')).order_by('month'))
    # Merge positive and negative results
    above_by_month = {}
    above_count_by_month = {}
    for r in list(gas_above) + list(gas_above_neg):
        m = r['month'].strftime('%Y-%m')
        above_by_month[m] = above_by_month.get(m, 0) + float(r['total'] or 0)
        above_count_by_month[m] = above_count_by_month.get(m, 0) + r['count']
    below_by_month = {}
    below_count_by_month = {}
    for r in list(gas_below) + list(gas_below_neg):
        m = r['month'].strftime('%Y-%m')
        below_by_month[m] = below_by_month.get(m, 0) + float(r['total'] or 0)
        below_count_by_month[m] = below_count_by_month.get(m, 0) + r['count']
    gas_split_above = [above_by_month.get(m, 0) for m in sorted_months]
    gas_split_below = [below_by_month.get(m, 0) for m in sorted_months]
    gas_count_above = [above_count_by_month.get(m, 0) for m in sorted_months]
    gas_count_below = [below_count_by_month.get(m, 0) for m in sorted_months]

    # ── RUNNING COSTS (Gas + Parking + Wash) ──
    RUNNING_CATEGORIES = ['Car Gas', 'Car Parking & Toll', 'Car Wash']
    running_qs = Transaction.objects.filter(user=request.user).filter(
        category__name__in=RUNNING_CATEGORIES, **{f'{amount_field}__isnull': False})
    running_monthly = (running_qs.annotate(month=TruncMonth('date')).values('month')
        .annotate(total=Sum(abs_field)).order_by('month'))
    running_by_month = {r['month'].strftime('%Y-%m'): float(r['total'] or 0) for r in running_monthly}
    running_totals = [running_by_month.get(m, 0) for m in sorted_months]
    running_last_month = running_by_month.get(last_month, 0)
    running_avg = sum(running_totals) / len(running_totals) if running_totals else 0
    running_vals = sorted([v for v in running_totals if v > 0])
    running_median = running_vals[len(running_vals) // 2] if running_vals else 0
    running_median_pct = ((running_last_month - running_median) / running_median * 100) if running_median else 0
    running_last_year = sum(running_totals[-12:]) if running_totals else 0
    # Per-category monthly for stacked chart
    running_by_cat_month = (running_qs.annotate(month=TruncMonth('date'))
        .values('month', 'category__name').annotate(total=Sum(abs_field)).order_by('month'))
    running_cat_data = {c: {m: 0 for m in sorted_months} for c in RUNNING_CATEGORIES}
    for r in running_by_cat_month:
        m = r['month'].strftime('%Y-%m')
        if m in running_cat_data.get(r['category__name'], {}):
            running_cat_data[r['category__name']][m] = float(r['total'] or 0)
    running_colors = {'Car Gas': '#2980b9', 'Car Parking & Toll': '#607d8b', 'Car Wash': '#1abc9c'}

    # ── OWNERSHIP COSTS (Maintenance + Insurance + Tax) ──
    OWNERSHIP_CATEGORIES = ['Car Maintenance', 'Car Insurance', 'Car Tax']
    ownership_qs = Transaction.objects.filter(user=request.user).filter(
        category__name__in=OWNERSHIP_CATEGORIES, **{f'{amount_field}__isnull': False})
    ownership_monthly = (ownership_qs.annotate(month=TruncMonth('date')).values('month')
        .annotate(total=Sum(abs_field)).order_by('month'))
    ownership_by_month = {r['month'].strftime('%Y-%m'): float(r['total'] or 0) for r in ownership_monthly}
    ownership_totals = [ownership_by_month.get(m, 0) for m in sorted_months]
    ownership_last_month = ownership_by_month.get(last_month, 0)
    ownership_avg = sum(ownership_totals) / len(ownership_totals) if ownership_totals else 0
    ownership_vals = sorted([v for v in ownership_totals if v > 0])
    ownership_median = ownership_vals[len(ownership_vals) // 2] if ownership_vals else 0
    ownership_median_pct = ((ownership_last_month - ownership_median) / ownership_median * 100) if ownership_median else 0
    ownership_last_year = sum(ownership_totals[-12:]) if ownership_totals else 0

    from datetime import date, timedelta
    twelve_months_ago = date.today() - timedelta(days=365)

    maint_qs = Transaction.objects.filter(user=request.user).filter(category__name='Car Maintenance', **{f'{amount_field}__isnull': False})
    maint_qs_12m = maint_qs.filter(date__gte=twelve_months_ago)
    maint_12m_total = float(maint_qs_12m.aggregate(t=Sum(abs_field))['t'] or 0)
    maint_12m_count = maint_qs_12m.count()

    ins_qs = Transaction.objects.filter(user=request.user).filter(category__name='Car Insurance', **{f'{amount_field}__isnull': False})
    ins_qs_12m = ins_qs.filter(date__gte=twelve_months_ago)
    ins_12m_total = float(ins_qs_12m.aggregate(t=Sum(abs_field))['t'] or 0)
    ins_12m_count = ins_qs_12m.count()

    tax_qs = Transaction.objects.filter(user=request.user).filter(category__name='Car Tax', **{f'{amount_field}__isnull': False})
    tax_qs_12m = tax_qs.filter(date__gte=twelve_months_ago)
    tax_12m_total = float(tax_qs_12m.aggregate(t=Sum(abs_field))['t'] or 0)
    tax_12m_count = tax_qs_12m.count()

    wash_qs = Transaction.objects.filter(user=request.user).filter(category__name='Car Wash', **{f'{amount_field}__isnull': False})
    wash_total = float(wash_qs.aggregate(t=Sum(abs_field))['t'] or 0)
    wash_count = wash_qs.count()

    # Combined timeline
    periodic_timeline = []
    for e in maint_qs.order_by('-date').values('date', 'description', amount_field):
        periodic_timeline.append({**e, 'type': 'Maintenance', 'color': '#F39C12', 'amount': abs(float(e[amount_field] or 0))})
    for e in ins_qs.order_by('-date').values('date', 'description', amount_field):
        periodic_timeline.append({**e, 'type': 'Insurance', 'color': '#3498DB', 'amount': abs(float(e[amount_field] or 0))})
    for e in tax_qs.order_by('-date').values('date', 'description', amount_field):
        periodic_timeline.append({**e, 'type': 'Tax', 'color': '#8e44ad', 'amount': abs(float(e[amount_field] or 0))})
    periodic_timeline.sort(key=lambda x: x['date'], reverse=True)

    # ── PARKING section ──
    park_qs = Transaction.objects.filter(user=request.user).filter(category__name='Car Parking & Toll', **{f'{amount_field}__isnull': False})
    park_monthly = (park_qs.annotate(month=TruncMonth('date')).values('month')
        .annotate(total=Sum(abs_field), count=Count('id')).order_by('month'))
    park_by_month = {r['month'].strftime('%Y-%m'): {'total': float(r['total'] or 0), 'count': r['count']} for r in park_monthly}
    park_totals = [park_by_month.get(m, {}).get('total', 0) for m in sorted_months]
    park_counts = [park_by_month.get(m, {}).get('count', 0) for m in sorted_months]
    park_total = float(park_qs.aggregate(t=Sum(abs_field))['t'] or 0)
    park_avg_charge = park_total / park_qs.count() if park_qs.count() > 0 else 0
    park_last_month = park_by_month.get(last_month, {}).get('total', 0)
    park_last_month_count = park_by_month.get(last_month, {}).get('count', 0)
    park_monthly_counts = [park_by_month.get(m, {}).get('count', 0) for m in sorted_months]
    park_monthly_counts_nonzero = [c for c in park_monthly_counts if c > 0]
    park_avg_count_monthly = sum(park_monthly_counts) / len(park_monthly_counts) if park_monthly_counts else 0
    park_median_count_monthly = sorted(park_monthly_counts_nonzero)[len(park_monthly_counts_nonzero) // 2] if park_monthly_counts_nonzero else 0
    park_count_last_year = sum(park_monthly_counts[-12:])
    park_monthly_vals = [v for v in park_totals if v > 0]
    park_avg_monthly = sum(park_totals) / len(park_totals) if park_totals else 0
    park_median_monthly = sorted(park_monthly_vals)[len(park_monthly_vals) // 2] if park_monthly_vals else 0
    park_median_pct = ((park_last_month - park_median_monthly) / park_median_monthly * 100) if park_median_monthly else 0
    park_last_year = sum(park_totals[-12:]) if park_totals else 0

    # Monthly parking data by location
    park_monthly_by_loc = (park_qs.annotate(month=TruncMonth('date'))
        .values('month', 'description')
        .annotate(total=Sum(abs_field), count=Count('id'))
        .order_by('month'))

    # Top 10 by total cost (for spend chart)
    top_by_cost = list(park_qs.values('description')
        .annotate(total=Sum(abs_field)).order_by('-total')[:5]
        .values_list('description', flat=True))
    top_by_cost_set = set(top_by_cost)

    # Top 10 by visit count (for frequency chart)
    top_by_visits = list(park_qs.values('description')
        .annotate(cnt=Count('id')).order_by('-cnt')[:5]
        .values_list('description', flat=True))
    top_by_visits_set = set(top_by_visits)

    # Build per-location monthly data for both charts
    def _build_loc_data(top_list, top_set, field):
        loc_data = {loc: {m: 0 for m in sorted_months} for loc in top_list}
        loc_data['Others'] = {m: 0 for m in sorted_months}
        for r in park_monthly_by_loc:
            m = r['month'].strftime('%Y-%m')
            if m not in sorted_months:
                continue
            key = r['description'] if r['description'] in top_set else 'Others'
            loc_data[key][m] += float(r[field] or 0) if field == 'total' else r[field]
        return loc_data

    spend_by_loc = _build_loc_data(top_by_cost, top_by_cost_set, 'total')
    count_by_loc = _build_loc_data(top_by_visits, top_by_visits_set, 'count')

    def _loc_color(i):
        colors = ['#2C3E50', '#18BC9C', '#3498DB', '#F39C12', '#E74C3C', '#95A5A6']
        return colors[i % len(colors)]

    park_spend_datasets = []
    for i, loc in enumerate(top_by_cost + ['Others']):
        park_spend_datasets.append({
            'label': loc[:15], 'data': [spend_by_loc[loc].get(m, 0) for m in sorted_months],
            'backgroundColor': _loc_color(i),
        })

    park_count_datasets = []
    for i, loc in enumerate(top_by_visits + ['Others']):
        park_count_datasets.append({
            'label': loc[:15], 'data': [count_by_loc[loc].get(m, 0) for m in sorted_months],
            'backgroundColor': _loc_color(i),
        })

    # Top parking locations by visits (last 12 months)
    from datetime import date, timedelta
    twelve_months_ago = date.today() - timedelta(days=365)
    park_locations = list(park_qs.filter(date__gte=twelve_months_ago).values('description')
        .annotate(count=Count('id'), total=Sum(abs_field))
        .order_by('-count')[:10])
    for loc in park_locations:
        loc['avg'] = float(loc['total']) / loc['count'] if loc['count'] else 0
        loc['total'] = float(loc['total'])
        loc['avg_per_month'] = float(loc['total']) / 12
    # Top parking locations by cost
    park_locations_by_cost = list(park_qs.values('description')
        .annotate(count=Count('id'), total=Sum(abs_field))
        .order_by('-total')[:10])
    for loc in park_locations_by_cost:
        loc['avg'] = float(loc['total']) / loc['count'] if loc['count'] else 0
        loc['total'] = float(loc['total'])
    # Top parking locations by max single charge
    park_locations_by_max = list(park_qs.filter(date__gte=twelve_months_ago).values('description')
        .annotate(max_charge=Max(abs_field), count=Count('id'))
        .order_by('-max_charge')[:10])
    for loc in park_locations_by_max:
        loc['max_charge'] = float(loc['max_charge'])

    # ── MONTHLY TABLE ──
    table_rows = []
    for m in sorted_months:
        row = {'month': m, 'salary': salary_by_month.get(m, 0)}
        for cat in CAR_CATEGORIES:
            row[cat] = months_data.get(m, {}).get(cat, 0)
        row['total'] = months_data.get(m, {}).get('_total', 0)
        row['pct'] = (row['total'] / row['salary'] * 100) if row['salary'] > 0 else 0
        table_rows.append(row)

    context = {
        'currency_symbol': currency_symbol,
        'car_categories': CAR_CATEGORIES,
        'cat_colors': cat_colors,
        'sorted_months': sorted_months,
        # Overview
        'last_month': last_month, 'last_month_total': last_month_total,
        'avg_monthly': avg_monthly, 'median_monthly': median_monthly,
        'salary_pct': salary_pct, 'last_month_salary': last_month_salary,
        'median_pct': median_pct,
        'tco_total': tco_total, 'num_months': len(sorted_months),
        'car_last_year': car_last_year,
        'category_totals': category_totals,
        # Running Costs
        'running_last_month': running_last_month, 'running_avg': running_avg,
        'running_median': running_median, 'running_median_pct': running_median_pct,
        'running_last_year': running_last_year,
        'running_data': json.dumps({
            'labels': sorted_months,
            'datasets': [{'label': c, 'data': [running_cat_data[c].get(m, 0) for m in sorted_months], 'backgroundColor': running_colors[c]} for c in RUNNING_CATEGORIES],
        }, cls=DecimalEncoder),
        # Gas
        'gas_last_month': gas_by_month.get(last_month, {}).get('total', 0),
        'gas_avg_monthly': sum(gas_monthly_spend) / len(gas_monthly_spend) if gas_monthly_spend else 0,
        'gas_median_monthly': sorted([v for v in gas_monthly_spend if v > 0])[len([v for v in gas_monthly_spend if v > 0]) // 2] if any(v > 0 for v in gas_monthly_spend) else 0,
        'gas_median_pct': ((gas_by_month.get(last_month, {}).get('total', 0) - (sorted([v for v in gas_monthly_spend if v > 0])[len([v for v in gas_monthly_spend if v > 0]) // 2] if any(v > 0 for v in gas_monthly_spend) else 0)) / (sorted([v for v in gas_monthly_spend if v > 0])[len([v for v in gas_monthly_spend if v > 0]) // 2] if any(v > 0 for v in gas_monthly_spend) else 1) * 100),
        'gas_last_year': sum(gas_monthly_spend[-12:]) if gas_monthly_spend else 0,
        # Ownership sub-categories (last 12 months)
        'maint_12m_total': maint_12m_total, 'maint_12m_count': maint_12m_count,
        'ins_12m_total': ins_12m_total, 'ins_12m_count': ins_12m_count,
        'tax_12m_total': tax_12m_total, 'tax_12m_count': tax_12m_count,
        'wash_total': wash_total, 'wash_count': wash_count,
        'ownership_last_month': ownership_last_month, 'ownership_avg': ownership_avg,
        'ownership_median': ownership_median, 'ownership_median_pct': ownership_median_pct,
        'ownership_last_year': ownership_last_year,
        'periodic_timeline': periodic_timeline,
        'amount_field': amount_field,
        # Parking
        'park_total': park_total, 'park_avg_charge': park_avg_charge,
        'park_count': park_qs.count(),
        'park_last_month': park_last_month, 'park_avg_monthly': park_avg_monthly,
        'park_median_monthly': park_median_monthly, 'park_median_pct': park_median_pct,
        'park_last_year': park_last_year,
        'park_last_month_count': park_last_month_count,
        'park_avg_count_monthly': park_avg_count_monthly,
        'park_median_count_monthly': park_median_count_monthly,
        'park_count_last_year': park_count_last_year,
        'park_locations': park_locations,
        'park_locations_by_max': park_locations_by_max,
        'park_locations_by_cost': park_locations_by_cost,
        # Table
        'table_rows': list(reversed(table_rows)),
        # Chart JSON
        'trend_data': json.dumps({
            'labels': sorted_months, 'datasets': trend_datasets, 'colors': cat_colors,
        }, cls=DecimalEncoder),
        'breakdown_data': json.dumps({
            'labels': [r['category__name'] for r in category_totals],
            'values': [float(r['total']) for r in category_totals],
            'colors': [cat_colors.get(r['category__name'], '#6c757d') for r in category_totals],
        }, cls=DecimalEncoder),
        'cost_type_data': json.dumps({
            'labels': ['Running Costs', 'Ownership Costs'],
            'values': [running_last_year, ownership_last_year],
            'colors': ['#18BC9C', '#E74C3C'],
        }, cls=DecimalEncoder),
        'gas_data': json.dumps({
            'labels': sorted_months, 'counts': gas_counts,
            'avg_per_fillup': gas_avg_per, 'spend': gas_monthly_spend,
            'split_above': gas_split_above, 'split_below': gas_split_below,
            'count_above': gas_count_above, 'count_below': gas_count_below,
            'threshold': threshold,
        }, cls=DecimalEncoder),
        'parking_data': json.dumps({
            'labels': sorted_months,
            'spend_datasets': park_spend_datasets,
            'count_datasets': park_count_datasets,
        }, cls=DecimalEncoder),
    }
    return context


@dashboard_view("car_gas", "core/dashboard_car_gas.html")
def car_gas_dashboard(request, display_currency, time_group):
    """Dashboard focused on car gas expenses."""
    from django.db.models import Sum, Count
    from django.db.models.functions import TruncMonth, Abs

    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'
    abs_field = Abs(amount_field)

    gas_qs = Transaction.objects.filter(user=request.user).filter(category__name='Car Gas', **{f'{amount_field}__isnull': False})
    monthly_gas = (gas_qs.annotate(month=TruncMonth('date')).values('month')
        .annotate(count=Count('id'), total=Sum(abs_field), avg=Sum(abs_field) / Count('id'))
        .order_by('month'))
    gas_by_month = {r['month'].strftime('%Y-%m'): {
        'count': r['count'], 'total': float(r['total'] or 0), 'avg': float(r['avg'] or 0)
    } for r in monthly_gas}
    sorted_months = sorted(gas_by_month.keys())
    gas_counts = [gas_by_month.get(m, {}).get('count', 0) for m in sorted_months]
    gas_avg_per = [gas_by_month.get(m, {}).get('avg', 0) for m in sorted_months]
    gas_monthly_spend = [gas_by_month.get(m, {}).get('total', 0) for m in sorted_months]
    last_month = sorted_months[-1] if sorted_months else ''

    # Monthly median per fill-up (from individual transactions)
    from collections import defaultdict
    gas_txns_by_month = defaultdict(list)
    for txn in gas_qs.annotate(month=TruncMonth('date')).values('month', amount_field):
        m = txn['month'].strftime('%Y-%m')
        gas_txns_by_month[m].append(abs(float(txn[amount_field] or 0)))
    gas_median_per = []
    for m in sorted_months:
        vals = sorted(gas_txns_by_month.get(m, []))
        gas_median_per.append(vals[len(vals) // 2] if vals else 0)
    gas_min_per = [min(gas_txns_by_month.get(m, [0])) for m in sorted_months]
    gas_max_per = [max(gas_txns_by_month.get(m, [0])) for m in sorted_months]

    # Threshold split
    threshold = 10000 if display_currency == 'CRC' else 20
    gas_above = (gas_qs.filter(**{f'{amount_field}__gte': threshold})
        .annotate(month=TruncMonth('date')).values('month')
        .annotate(total=Sum(abs_field), count=Count('id')).order_by('month'))
    gas_below = (gas_qs.filter(**{f'{amount_field}__lt': threshold})
        .annotate(month=TruncMonth('date')).values('month')
        .annotate(total=Sum(abs_field), count=Count('id')).order_by('month'))
    gas_above_neg = (gas_qs.filter(**{f'{amount_field}__lte': -threshold})
        .annotate(month=TruncMonth('date')).values('month')
        .annotate(total=Sum(abs_field), count=Count('id')).order_by('month'))
    gas_below_neg = (gas_qs.filter(**{f'{amount_field}__gt': -threshold, f'{amount_field}__lt': 0})
        .annotate(month=TruncMonth('date')).values('month')
        .annotate(total=Sum(abs_field), count=Count('id')).order_by('month'))
    above_by_month, above_count = {}, {}
    for r in list(gas_above) + list(gas_above_neg):
        m = r['month'].strftime('%Y-%m')
        above_by_month[m] = above_by_month.get(m, 0) + float(r['total'] or 0)
        above_count[m] = above_count.get(m, 0) + r['count']
    below_by_month, below_count = {}, {}
    for r in list(gas_below) + list(gas_below_neg):
        m = r['month'].strftime('%Y-%m')
        below_by_month[m] = below_by_month.get(m, 0) + float(r['total'] or 0)
        below_count[m] = below_count.get(m, 0) + r['count']

    gas_median_vals = sorted([v for v in gas_monthly_spend if v > 0])
    gas_median = gas_median_vals[len(gas_median_vals) // 2] if gas_median_vals else 0
    gas_last = gas_by_month.get(last_month, {}).get('total', 0)
    gas_median_pct = ((gas_last - gas_median) / gas_median * 100) if gas_median else 0

    # Event stats
    gas_last_month_count = gas_by_month.get(last_month, {}).get('count', 0)
    gas_counts_nonzero = [c for c in gas_counts if c > 0]
    gas_avg_count_monthly = sum(gas_counts) / len(gas_counts) if gas_counts else 0
    gas_median_count_monthly = sorted(gas_counts_nonzero)[len(gas_counts_nonzero) // 2] if gas_counts_nonzero else 0
    gas_count_last_year = sum(gas_counts[-12:])

    # Per fill-up cost stats
    all_fillup_amounts = [abs(float(a)) for a in gas_qs.values_list(amount_field, flat=True) if a]
    all_fillup_avg = sum(all_fillup_amounts) / len(all_fillup_amounts) if all_fillup_amounts else 0
    all_fillup_median = sorted(all_fillup_amounts)[len(all_fillup_amounts) // 2] if all_fillup_amounts else 0
    # Last month fill-up amounts
    if last_month:
        lm_year, lm_mo = last_month.split('-')
        lm_amounts = [abs(float(a)) for a in gas_qs.filter(date__year=int(lm_year), date__month=int(lm_mo)).values_list(amount_field, flat=True) if a]
    else:
        lm_amounts = []
    lm_fillup_avg = sum(lm_amounts) / len(lm_amounts) if lm_amounts else 0
    lm_fillup_median = sorted(lm_amounts)[len(lm_amounts) // 2] if lm_amounts else 0

    context = {
        'currency_symbol': currency_symbol,
        'last_month': last_month,
        'gas_last_month': gas_last,
        'gas_avg_monthly': sum(gas_monthly_spend) / len(gas_monthly_spend) if gas_monthly_spend else 0,
        'gas_median_monthly': gas_median,
        'gas_median_pct': gas_median_pct,
        'gas_last_year': sum(gas_monthly_spend[-12:]) if gas_monthly_spend else 0,
        'gas_last_month_count': gas_last_month_count,
        'gas_avg_count_monthly': gas_avg_count_monthly,
        'gas_median_count_monthly': gas_median_count_monthly,
        'gas_count_last_year': gas_count_last_year,
        'lm_fillup_avg': lm_fillup_avg, 'lm_fillup_median': lm_fillup_median,
        'all_fillup_avg': all_fillup_avg, 'all_fillup_median': all_fillup_median,
        'fillup_table': [{'month': m, 'min': gas_min_per[i], 'median': gas_median_per[i], 'avg': gas_avg_per[i], 'max': gas_max_per[i]} for i, m in enumerate(sorted_months)][::-1],
        'gas_data': json.dumps({
            'labels': sorted_months, 'counts': gas_counts,
            'avg_per_fillup': gas_avg_per, 'median_per_fillup': gas_median_per,
            'min_per_fillup': gas_min_per, 'max_per_fillup': gas_max_per,
            'spend': gas_monthly_spend,
            'split_above': [above_by_month.get(m, 0) for m in sorted_months],
            'split_below': [below_by_month.get(m, 0) for m in sorted_months],
            'count_above': [above_count.get(m, 0) for m in sorted_months],
            'count_below': [below_count.get(m, 0) for m in sorted_months],
            'threshold': threshold,
        }, cls=DecimalEncoder),
    }
    return context


@dashboard_view("car_parking", "core/dashboard_car_parking.html")
def car_parking_dashboard(request, display_currency, time_group):
    """Dashboard focused on car parking & tolls."""
    from datetime import date, timedelta
    from django.db.models import Sum, Count, Max
    from django.db.models.functions import TruncMonth, Abs

    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'
    abs_field = Abs(amount_field)

    park_qs = Transaction.objects.filter(user=request.user).filter(category__name='Car Parking & Toll', **{f'{amount_field}__isnull': False})
    park_monthly = (park_qs.annotate(month=TruncMonth('date')).values('month')
        .annotate(total=Sum(abs_field), count=Count('id')).order_by('month'))
    park_by_month = {r['month'].strftime('%Y-%m'): {'total': float(r['total'] or 0), 'count': r['count']} for r in park_monthly}
    sorted_months = sorted(park_by_month.keys())
    park_totals = [park_by_month.get(m, {}).get('total', 0) for m in sorted_months]
    park_counts = [park_by_month.get(m, {}).get('count', 0) for m in sorted_months]
    last_month = sorted_months[-1] if sorted_months else ''

    # Cost stats
    park_total = float(park_qs.aggregate(t=Sum(abs_field))['t'] or 0)
    park_last_month = park_by_month.get(last_month, {}).get('total', 0)
    park_avg_monthly = sum(park_totals) / len(park_totals) if park_totals else 0
    park_monthly_vals = [v for v in park_totals if v > 0]
    park_median_monthly = sorted(park_monthly_vals)[len(park_monthly_vals) // 2] if park_monthly_vals else 0
    park_median_pct = ((park_last_month - park_median_monthly) / park_median_monthly * 100) if park_median_monthly else 0
    park_last_year = sum(park_totals[-12:]) if park_totals else 0

    # Event stats
    park_last_month_count = park_by_month.get(last_month, {}).get('count', 0)
    park_monthly_counts_nonzero = [c for c in park_counts if c > 0]
    park_avg_count_monthly = sum(park_counts) / len(park_counts) if park_counts else 0
    park_median_count_monthly = sorted(park_monthly_counts_nonzero)[len(park_monthly_counts_nonzero) // 2] if park_monthly_counts_nonzero else 0
    park_count_last_year = sum(park_counts[-12:])

    # Location charts (top 5 by cost / by visits)
    park_monthly_by_loc = list(park_qs.annotate(month=TruncMonth('date'))
        .values('month', 'description')
        .annotate(total=Sum(abs_field), count=Count('id'))
        .order_by('month'))

    top_by_cost = list(park_qs.values('description')
        .annotate(total=Sum(abs_field)).order_by('-total')[:5]
        .values_list('description', flat=True))
    top_by_visits = list(park_qs.values('description')
        .annotate(cnt=Count('id')).order_by('-cnt')[:5]
        .values_list('description', flat=True))

    def _build_loc_data(top_list, top_set, field):
        loc_data = {loc: {m: 0 for m in sorted_months} for loc in top_list}
        loc_data['Others'] = {m: 0 for m in sorted_months}
        for r in park_monthly_by_loc:
            m = r['month'].strftime('%Y-%m')
            if m not in sorted_months:
                continue
            key = r['description'] if r['description'] in top_set else 'Others'
            loc_data[key][m] += float(r[field] or 0) if field == 'total' else r[field]
        return loc_data

    spend_by_loc = _build_loc_data(top_by_cost, set(top_by_cost), 'total')
    count_by_loc = _build_loc_data(top_by_visits, set(top_by_visits), 'count')

    def _loc_color(i):
        colors = ['#2C3E50', '#18BC9C', '#3498DB', '#F39C12', '#E74C3C', '#95A5A6']
        return colors[i % len(colors)]

    park_spend_datasets = [{'label': loc[:15], 'data': [spend_by_loc[loc].get(m, 0) for m in sorted_months], 'backgroundColor': _loc_color(i)} for i, loc in enumerate(top_by_cost + ['Others'])]
    park_count_datasets = [{'label': loc[:15], 'data': [count_by_loc[loc].get(m, 0) for m in sorted_months], 'backgroundColor': _loc_color(i)} for i, loc in enumerate(top_by_visits + ['Others'])]

    # Top locations table
    twelve_months_ago = date.today() - timedelta(days=365)
    park_locations = list(park_qs.filter(date__gte=twelve_months_ago).values('description')
        .annotate(count=Count('id'), total=Sum(abs_field))
        .order_by('-count')[:10])
    for loc in park_locations:
        loc['avg'] = float(loc['total']) / loc['count'] if loc['count'] else 0
        loc['total'] = float(loc['total'])

    context = {
        'currency_symbol': currency_symbol,
        'last_month': last_month,
        'park_last_month': park_last_month, 'park_avg_monthly': park_avg_monthly,
        'park_median_monthly': park_median_monthly, 'park_median_pct': park_median_pct,
        'park_last_year': park_last_year,
        'park_last_month_count': park_last_month_count,
        'park_avg_count_monthly': park_avg_count_monthly,
        'park_median_count_monthly': park_median_count_monthly,
        'park_count_last_year': park_count_last_year,
        'park_locations': park_locations,
        'parking_data': json.dumps({
            'labels': sorted_months,
            'spend_datasets': park_spend_datasets,
            'count_datasets': park_count_datasets,
        }, cls=DecimalEncoder),
    }
    return context


@dashboard_view("income_salary", "core/dashboard_income_salary.html")
def income_salary_dashboard(request, display_currency, time_group):
    """Dashboard focused on Salary Main income."""
    from django.db.models import Sum, Count, Avg
    from django.db.models.functions import TruncMonth

    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'

    salary_qs = Transaction.objects.filter(user=request.user).filter(
        category__name='Salary Main',
        **{f'{amount_field}__isnull': False},
    )

    # Monthly aggregation
    monthly = (
        salary_qs
        .annotate(month=TruncMonth('date'))
        .values('month')
        .annotate(total=Sum(amount_field), cnt=Count('id'))
        .order_by('month')
    )
    months_data = {}
    for r in monthly:
        m = r['month'].strftime('%Y-%m')
        months_data[m] = float(r['total'] or 0)

    sorted_months = sorted(months_data.keys())
    monthly_totals = [months_data[m] for m in sorted_months]

    # Summary cards
    avg_monthly = sum(monthly_totals) / len(monthly_totals) if monthly_totals else 0
    sorted_totals = sorted(monthly_totals)
    n = len(sorted_totals)
    median_monthly = (sorted_totals[n // 2] if n % 2 else
                      (sorted_totals[n // 2 - 1] + sorted_totals[n // 2]) / 2) if n else 0

    last_month = sorted_months[-1] if sorted_months else ''
    last_month_total = months_data.get(last_month, 0)
    median_pct = ((last_month_total - median_monthly) / median_monthly * 100) if median_monthly else 0

    # 12-month total
    last_12 = monthly_totals[-12:] if len(monthly_totals) >= 12 else monthly_totals
    salary_last_year = sum(last_12)

    # Chart data
    trend_data = json.dumps({
        'labels': sorted_months,
        'values': monthly_totals,
        'average': avg_monthly,
    }, cls=DecimalEncoder)

    # ── BONUSES & NON-RECURRING SECTION ──
    EXTRA_CATEGORIES = ['Salary Bonuses', 'Non-recurring']
    extra_qs = Transaction.objects.filter(user=request.user).filter(
        category__name__in=EXTRA_CATEGORIES,
        **{f'{amount_field}__isnull': False},
    )

    # Per-category 12-month totals
    from django.db.models.functions import Abs
    bonus_total = float(
        extra_qs.filter(category__name='Salary Bonuses')
        .aggregate(t=Sum(amount_field))['t'] or 0
    )
    nonrecurring_total = float(
        extra_qs.filter(category__name='Non-recurring')
        .aggregate(t=Sum(amount_field))['t'] or 0
    )
    extra_combined = bonus_total + nonrecurring_total

    # Event timeline
    extra_events = list(
        extra_qs.order_by('-date')
        .values('date', 'description', 'category__name', 'category__color', amount_field)
    )
    for e in extra_events:
        e['amount'] = float(e.pop(amount_field) or 0)
        e['category'] = e.pop('category__name')
        e['color'] = e.pop('category__color') or '#6c757d'

    context = {
        'currency_symbol': currency_symbol,
        'last_month': last_month,
        'last_month_total': last_month_total,
        'avg_monthly': avg_monthly,
        'median_monthly': median_monthly,
        'median_pct': median_pct,
        'salary_last_year': salary_last_year,
        'sorted_months': sorted_months,
        'trend_data': trend_data,
        # Bonuses & Non-recurring
        'bonus_total': bonus_total,
        'nonrecurring_total': nonrecurring_total,
        'extra_combined': extra_combined,
        'extra_events': extra_events,
    }
    return context
