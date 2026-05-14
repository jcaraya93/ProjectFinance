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
    'income_bonus_dashboard',
    'income_overview_dashboard',
    'reimbursement_overview_dashboard',
    'bank_income_overview_dashboard',
    'internal_transfers_dashboard',
    'credit_transfers_dashboard',
    'external_transfers_dashboard',
    'transfer_flow_dashboard',
    'transaction_pairing_dashboard',
    'category_stats_dashboard',
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

# Category name constants used by car and salary dashboards.
# Update these if the user renames categories in the UI.
CAR_CATEGORIES = ['Car Gas', 'Car Insurance', 'Car Maintenance', 'Car Parking & Toll', 'Car Tax', 'Car Wash']
RUNNING_CATEGORIES = ['Car Gas', 'Car Parking & Toll', 'Car Wash']
OWNERSHIP_CATEGORIES = ['Car Maintenance', 'Car Insurance', 'Car Tax']
SALARY_CATEGORIES = ['Work Salary', 'Work Bonuses']
EXTRA_INCOME_CATEGORIES = ['Work Bonuses', 'Work Association', 'Work Government']
TXN_INCOME_CATEGORIES = ['Reimbursement General']
BANK_INCOME_CATEGORIES = ['Bank Interest CDP', 'Bank Interest Cashback', 'Bank Interest Reversals', 'Bank Interest Credit']
CREDIT_PAYMENT_CATEGORY = 'Credit'
PERSONAL_ACCOUNT_CATEGORY = 'Internal'


DASHBOARD_CATEGORIES = {
    'overview': 'overview', 'spending_income': 'overview', 'category_stats': 'overview',
    'income_overview': 'income', 'income_salary': 'income', 'income_bonus': 'income',
    'reimbursement_overview': 'income',
    'bank_income_overview': 'income',
    'transfer_flow': 'transfers', 'internal_transfers': 'transfers',
    'credit_transfers': 'transfers', 'external_transfers': 'transfers',
    'transaction_pairing': 'transfers',
    'car': 'expense', 'car_gas': 'expense', 'car_parking': 'expense',
    'transaction_health': 'data_quality', 'rule_matching': 'data_quality',
    'default_buckets': 'data_quality',
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
                context.setdefault('dashboard_category', DASHBOARD_CATEGORIES.get(name, 'overview'))

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
    GROUP_LABELS = {'expense': 'Expense', 'income': 'Income', 'transfer': 'Transfer', 'unclassified': 'Unclassified'}
    GROUP_CHART_COLORS = {
        'expense': CHART_COLORS['expense'], 'income': CHART_COLORS['income'],
        'transfer': CHART_COLORS['primary'], 'unclassified': CHART_COLORS['warning'],
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
    GROUP_LABELS = {'expense': 'Expense', 'income': 'Income', 'transfer': 'Transfer', 'unclassified': 'Unclassified'}
    GROUP_COLORS = {'expense': 'danger', 'income': 'success', 'transfer': 'primary', 'unclassified': 'warning'}
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
             'transfer': 'rgba(13,110,253,0.8)', 'unclassified': 'rgba(255,193,7,0.8)'
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
            'spend_median': float(gas_median),
            'count_median': float(gas_median_count_monthly),
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


@dashboard_view("income_salary", "core/dashboard_income_salary.html", default_time_group="biweekly")
def income_salary_dashboard(request, display_currency, time_group):
    """Dashboard focused on Salary Main income."""
    from collections import defaultdict
    from datetime import timedelta
    from django.db.models import Sum, Count, Avg
    from django.db.models.functions import TruncMonth

    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'

    salary_qs = Transaction.objects.filter(user=request.user).filter(
        category__name='Work Salary',
        **{f'{amount_field}__isnull': False},
    )

    # --- Determine last period from latest statement ---
    from core.models import StatementImport
    today = date.today()
    latest_stmt = (
        StatementImport.objects.filter(user=request.user)
        .order_by('-statement_date').first()
    )
    if latest_stmt and latest_stmt.statement_date:
        stmt_date = latest_stmt.statement_date
        last_period_start = stmt_date.replace(day=1)
        if stmt_date.month == 12:
            last_period_end = stmt_date.replace(year=stmt_date.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            last_period_end = stmt_date.replace(month=stmt_date.month + 1, day=1) - timedelta(days=1)
        last_month_label = f"{last_period_start.strftime('%b %Y')} (latest)"
        last_period_key = last_period_start.strftime('%Y-%m')
    else:
        last_period_end = today.replace(day=1) - timedelta(days=1)
        last_period_start = last_period_end.replace(day=1)
        last_month_label = last_period_start.strftime('%b %Y')
        last_period_key = last_period_start.strftime('%Y-%m')

    # --- Always compute monthly summary cards ---
    monthly_agg = (
        salary_qs
        .annotate(month=TruncMonth('date'))
        .values('month')
        .annotate(total=Sum(amount_field), cnt=Count('id'))
        .order_by('month')
    )
    monthly_map = {}
    for r in monthly_agg:
        m = r['month'].strftime('%Y-%m')
        monthly_map[m] = float(r['total'] or 0)

    sorted_month_keys = sorted(monthly_map.keys())
    monthly_totals_list = [monthly_map[m] for m in sorted_month_keys]

    avg_monthly = sum(monthly_totals_list) / len(monthly_totals_list) if monthly_totals_list else 0
    sorted_totals = sorted(monthly_totals_list)
    n = len(sorted_totals)
    median_monthly = (sorted_totals[n // 2] if n % 2 else
                      (sorted_totals[n // 2 - 1] + sorted_totals[n // 2]) / 2) if n else 0

    last_month_total = monthly_map.get(last_period_key, 0)
    median_pct = ((last_month_total - median_monthly) / median_monthly * 100) if median_monthly else 0

    last_12 = monthly_totals_list[-12:] if len(monthly_totals_list) >= 12 else monthly_totals_list
    salary_last_year = sum(last_12)

    # --- Chart data grouped by time_group ---
    if time_group == 'biweekly':
        def _semi_month_key(d):
            return date(d.year, d.month, 1 if d.day < 15 else 15)

        salary_txns = salary_qs.values_list('date', amount_field)
        period_totals = defaultdict(float)
        for d, amt in salary_txns:
            if amt:
                period_totals[_semi_month_key(d)] += float(amt)

        sorted_periods = sorted(period_totals.keys())
        chart_labels = [p.strftime('%Y-%m-%d') for p in sorted_periods]
        chart_values = [period_totals[p] for p in sorted_periods]
    else:
        chart_labels = sorted_month_keys
        chart_values = monthly_totals_list

    chart_avg = sum(chart_values) / len(chart_values) if chart_values else 0
    sorted_chart = sorted(chart_values)
    cn = len(sorted_chart)
    chart_median = (sorted_chart[cn // 2] if cn % 2 else
                    (sorted_chart[cn // 2 - 1] + sorted_chart[cn // 2]) / 2) if cn else 0

    trend_data = json.dumps({
        'labels': chart_labels,
        'values': chart_values,
        'average': chart_avg,
        'median': chart_median,
    }, cls=DecimalEncoder)

    # --- Link to transactions ---
    from core.models import Category
    salary_cat_ids = list(
        Category.objects.filter(
            user=request.user, name='Work Salary', group__slug='income',
        ).values_list('id', flat=True)
    )
    salary_category_ids = '&category='.join(str(cid) for cid in salary_cat_ids)

    context = {
        'currency_symbol': currency_symbol,
        'last_month_label': last_month_label,
        'last_month_total': last_month_total,
        'avg_monthly': avg_monthly,
        'median_monthly': median_monthly,
        'median_pct': median_pct,
        'salary_last_year': salary_last_year,
        'sorted_months': sorted_month_keys,
        'trend_data': trend_data,
        'salary_category_ids': salary_category_ids,
    }
    return context


@dashboard_view("income_bonus", "core/dashboard_income_bonus.html")
def income_bonus_dashboard(request, display_currency, time_group):
    """Dashboard focused on bonuses and non-recurring income."""
    from django.db.models import Sum

    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'

    extra_qs = Transaction.objects.filter(user=request.user).filter(
        category__name__in=EXTRA_INCOME_CATEGORIES,
        **{f'{amount_field}__isnull': False},
    )

    bonus_total = float(
        extra_qs.filter(category__name='Work Bonuses')
        .aggregate(t=Sum(amount_field))['t'] or 0
    )
    association_total = float(
        extra_qs.filter(category__name='Work Association')
        .aggregate(t=Sum(amount_field))['t'] or 0
    )
    goverment_total = float(
        extra_qs.filter(category__name='Work Government')
        .aggregate(t=Sum(amount_field))['t'] or 0
    )
    extra_combined = bonus_total + association_total + goverment_total

    extra_events = list(
        extra_qs.order_by('-date')
        .values('date', 'description', 'category__name', 'category__color', amount_field)
    )
    for e in extra_events:
        e['amount'] = float(e.pop(amount_field) or 0)
        e['category'] = e.pop('category__name')
        e['color'] = e.pop('category__color') or '#6c757d'

    extra_events_json = json.dumps([
        {'date': e['date'].isoformat() if hasattr(e['date'], 'isoformat') else str(e['date']),
         'description': e['description'],
         'category': e['category'],
         'color': e['color'],
         'amount': e['amount']}
        for e in extra_events
    ], cls=DecimalEncoder)

    # --- Link to transactions ---
    from core.models import Category
    extras_cat_ids = list(
        Category.objects.filter(
            user=request.user, name__in=EXTRA_INCOME_CATEGORIES, group__slug='income',
        ).values_list('id', flat=True)
    )
    extras_category_ids = '&category='.join(str(cid) for cid in extras_cat_ids)

    context = {
        'currency_symbol': currency_symbol,
        'bonus_total': bonus_total,
        'association_total': association_total,
        'goverment_total': goverment_total,
        'extra_combined': extra_combined,
        'extra_events': extra_events,
        'extra_events_json': extra_events_json,
        'extras_category_ids': extras_category_ids,
    }
    return context


@dashboard_view("income_overview", "core/dashboard_income_overview.html")
def income_overview_dashboard(request, display_currency, time_group):
    """Overview dashboard for all income categories."""
    from collections import defaultdict
    from datetime import timedelta
    from django.db.models import Sum, Count
    from django.db.models.functions import Abs, TruncMonth

    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'
    abs_field = Abs(amount_field)

    income_qs = Transaction.objects.filter(
        user=request.user,
        category__group__slug='income',
        **{f'{amount_field}__isnull': False},
    )

    # --- Summary cards ---
    from core.models import StatementImport
    today = date.today()
    latest_stmt = (
        StatementImport.objects.filter(user=request.user)
        .order_by('-statement_date').first()
    )
    if latest_stmt and latest_stmt.statement_date:
        stmt_date = latest_stmt.statement_date
        last_period_start = stmt_date.replace(day=1)
        if stmt_date.month == 12:
            last_period_end = stmt_date.replace(year=stmt_date.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            last_period_end = stmt_date.replace(month=stmt_date.month + 1, day=1) - timedelta(days=1)
        last_period_label = f"{last_period_start.strftime('%b %Y')} (latest)"
    else:
        last_period_end = today.replace(day=1) - timedelta(days=1)
        last_period_start = last_period_end.replace(day=1)
        last_period_label = last_period_start.strftime('%b %Y')

    last_period_total = float(
        income_qs.filter(date__gte=last_period_start, date__lte=last_period_end)
        .aggregate(t=Sum(abs_field))['t'] or 0
    )

    monthly_agg = (
        income_qs.annotate(month=TruncMonth('date'))
        .values('month')
        .annotate(total=Sum(abs_field))
        .order_by('month')
    )
    monthly_map = {}
    for r in monthly_agg:
        m = r['month'].strftime('%Y-%m')
        monthly_map[m] = float(r['total'] or 0)
    sorted_months = sorted(monthly_map.keys())
    monthly_totals = [monthly_map[m] for m in sorted_months]

    avg_monthly = sum(monthly_totals) / len(monthly_totals) if monthly_totals else 0
    sorted_vals = sorted(monthly_totals)
    n = len(sorted_vals)
    median_monthly = (sorted_vals[n // 2] if n % 2 else
                      (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2) if n else 0
    all_time_total = sum(monthly_totals)
    median_pct = ((last_period_total - median_monthly) / median_monthly * 100) if median_monthly else 0

    # --- Monthly trend (total) ---
    trend_data = json.dumps({
        'labels': sorted_months,
        'values': [round(v) for v in monthly_totals],
        'median': round(median_monthly),
    }, cls=DecimalEncoder)

    # --- Stacked bar by category over time ---
    cat_monthly = (
        income_qs.annotate(month=TruncMonth('date'))
        .values('month', 'category__name', 'category__color')
        .annotate(total=Sum(abs_field))
        .order_by('month')
    )
    cat_series_map = defaultdict(lambda: defaultdict(float))
    cat_color_map = {}
    for r in cat_monthly:
        name = r['category__name']
        m = r['month'].strftime('%Y-%m')
        cat_series_map[name][m] = float(r['total'] or 0)
        cat_color_map[name] = r['category__color'] or '#6c757d'

    stacked_series = []
    stacked_colors = []
    for name in sorted(cat_series_map.keys(), key=lambda k: -sum(cat_series_map[k].values())):
        stacked_series.append({
            'name': name,
            'data': [round(cat_series_map[name].get(m, 0)) for m in sorted_months],
        })
        stacked_colors.append(cat_color_map[name])

    stacked_data = json.dumps({
        'labels': sorted_months,
        'series': stacked_series,
        'colors': stacked_colors,
    }, cls=DecimalEncoder)

    # --- All-time breakdown by category (donut + bar) ---
    cat_breakdown = list(
        income_qs.values('category__name', 'category__color')
        .annotate(abs_total=Sum(abs_field))
        .order_by('-abs_total')
    )
    income_category_data = {'labels': [], 'values': [], 'colors': []}
    top_income_data = {'labels': [], 'values': [], 'colors': []}
    for r in cat_breakdown:
        name = r['category__name'] or 'Uncategorized'
        val = float(r['abs_total'] or 0)
        color = r['category__color'] or '#6c757d'
        income_category_data['labels'].append(name)
        income_category_data['values'].append(val)
        income_category_data['colors'].append(color)
        top_income_data['labels'].append(name)
        top_income_data['values'].append(val)
        top_income_data['colors'].append(color)

    # --- Income composition (% stacked) ---
    composition_series = []
    composition_colors = []
    for name in sorted(cat_series_map.keys(), key=lambda k: -sum(cat_series_map[k].values())):
        composition_series.append({
            'name': name,
            'data': [round(cat_series_map[name].get(m, 0)) for m in sorted_months],
        })
        composition_colors.append(cat_color_map[name])

    composition_data = json.dumps({
        'labels': sorted_months,
        'series': composition_series,
        'colors': composition_colors,
    }, cls=DecimalEncoder)

    context = {
        'currency_symbol': currency_symbol,
        'last_period_label': last_period_label,
        'last_period_total': last_period_total,
        'avg_monthly': avg_monthly,
        'median_monthly': median_monthly,
        'median_pct': median_pct,
        'all_time_total': all_time_total,
        'trend_data': trend_data,
        'stacked_data': stacked_data,
        'composition_data': composition_data,
        'income_category_data': json.dumps(income_category_data, cls=DecimalEncoder),
        'top_income_data': json.dumps(top_income_data, cls=DecimalEncoder),
        'filter_group': 'income',
    }
    return context


REIMBURSEMENT_CATEGORIES = ['Reimbursement General', 'Reimbursement Housing', 'Reimbursement Insurance', 'Reimbursement Partner']


@dashboard_view("reimbursement_overview", "core/dashboard_reimbursement_overview.html")
def reimbursement_overview_dashboard(request, display_currency, time_group):
    """Overview dashboard for all reimbursement income categories."""
    from collections import defaultdict
    from datetime import timedelta
    from django.db.models import Sum
    from django.db.models.functions import TruncMonth, Abs

    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'
    abs_field = Abs(amount_field)

    from core.models import StatementImport
    today = date.today()
    latest_stmt = (
        StatementImport.objects.filter(user=request.user)
        .order_by('-statement_date').first()
    )
    if latest_stmt and latest_stmt.statement_date:
        stmt_date = latest_stmt.statement_date
        last_month_start = stmt_date.replace(day=1)
        if stmt_date.month == 12:
            last_month_end = stmt_date.replace(year=stmt_date.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            last_month_end = stmt_date.replace(month=stmt_date.month + 1, day=1) - timedelta(days=1)
        last_month_label = f"{last_month_start.strftime('%b %Y')} (latest)"
    else:
        last_month_end = today.replace(day=1) - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        last_month_label = last_month_start.strftime('%Y-%m')

    reimb_qs = Transaction.objects.filter(
        user=request.user,
        category__name__in=REIMBURSEMENT_CATEGORIES,
        category__group__slug='income',
        **{f'{amount_field}__isnull': False},
    )

    # --- Summary cards ---
    last_month_total = float(
        reimb_qs.filter(date__gte=last_month_start, date__lte=last_month_end)
        .aggregate(t=Sum(abs_field))['t'] or 0
    )

    monthly_agg = (
        reimb_qs.annotate(month=TruncMonth('date'))
        .values('month')
        .annotate(total=Sum(abs_field))
        .order_by('month')
    )
    monthly_map = {}
    for r in monthly_agg:
        m = r['month'].strftime('%Y-%m')
        monthly_map[m] = float(r['total'] or 0)
    sorted_months = sorted(monthly_map.keys())
    monthly_totals = [monthly_map[m] for m in sorted_months]

    avg_monthly = sum(monthly_totals) / len(monthly_totals) if monthly_totals else 0
    sorted_vals = sorted(monthly_totals)
    n = len(sorted_vals)
    median_monthly = (sorted_vals[n // 2] if n % 2 else
                      (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2) if n else 0
    all_time_total = sum(monthly_totals)
    median_pct = ((last_month_total - median_monthly) / median_monthly * 100) if median_monthly else 0

    # --- Monthly trend ---
    trend_data = json.dumps({
        'labels': sorted_months,
        'values': monthly_totals,
        'median': median_monthly,
    }, cls=DecimalEncoder)

    # --- All-time breakdown by category (donut + bar) ---
    cat_breakdown = list(
        reimb_qs.values('category__name', 'category__color')
        .annotate(abs_total=Sum(abs_field))
        .order_by('-abs_total')
    )
    breakdown_data = {'labels': [], 'values': [], 'colors': []}
    top_data = {'labels': [], 'values': [], 'colors': []}
    for r in cat_breakdown:
        name = r['category__name'] or 'Uncategorized'
        val = float(r['abs_total'] or 0)
        color = r['category__color'] or '#6c757d'
        breakdown_data['labels'].append(name)
        breakdown_data['values'].append(val)
        breakdown_data['colors'].append(color)
        top_data['labels'].append(name)
        top_data['values'].append(val)
        top_data['colors'].append(color)

    # --- Stacked bar by category over time ---
    cat_monthly = (
        reimb_qs.annotate(month=TruncMonth('date'))
        .values('month', 'category__name', 'category__color')
        .annotate(total=Sum(abs_field))
        .order_by('month')
    )
    cat_series_map = defaultdict(lambda: defaultdict(float))
    cat_color_map = {}
    for r in cat_monthly:
        name = r['category__name']
        m = r['month'].strftime('%Y-%m')
        cat_series_map[name][m] = float(r['total'] or 0)
        cat_color_map[name] = r['category__color'] or '#6c757d'

    stacked_series = []
    stacked_colors = []
    for name in sorted(cat_series_map.keys(), key=lambda k: -sum(cat_series_map[k].values())):
        stacked_series.append({
            'name': name,
            'data': [round(cat_series_map[name].get(m, 0)) for m in sorted_months],
        })
        stacked_colors.append(cat_color_map[name])

    stacked_data = json.dumps({
        'labels': sorted_months,
        'series': stacked_series,
        'colors': stacked_colors,
    }, cls=DecimalEncoder)

    # --- Recent transactions ---
    events = list(
        reimb_qs.order_by('-date')[:50]
        .values('date', 'description', 'category__name', 'category__color', amount_field)
    )
    for e in events:
        e['amount'] = abs(float(e.pop(amount_field) or 0))
        e['category'] = e.pop('category__name')
        e['color'] = e.pop('category__color') or '#6c757d'

    # --- Individual transactions scatter (with category) ---
    individual_txns = list(
        reimb_qs.order_by('date')
        .values_list('date', amount_field, 'description', 'category__name', 'category__color')
    )
    # Group by category for multi-series scatter
    from collections import OrderedDict
    scatter_by_cat = defaultdict(list)
    scatter_colors = {}
    for d, amt, desc, cat_name, cat_color in individual_txns:
        cat = cat_name or 'Uncategorized'
        scatter_by_cat[cat].append({
            'date': d.isoformat() if hasattr(d, 'isoformat') else str(d),
            'amount': round(abs(float(amt))) if amt else 0,
            'description': desc,
        })
        scatter_colors[cat] = cat_color or '#6c757d'

    scatter_series = []
    scatter_color_list = []
    for cat in sorted(scatter_by_cat.keys(), key=lambda k: -len(scatter_by_cat[k])):
        scatter_series.append({'name': cat, 'data': scatter_by_cat[cat]})
        scatter_color_list.append(scatter_colors[cat])

    scatter_data = json.dumps({
        'series': scatter_series,
        'colors': scatter_color_list,
    }, cls=DecimalEncoder)

    # --- Transaction count per month (stacked by category) ---
    from django.db.models import Count
    count_cat_agg = (
        reimb_qs.annotate(month=TruncMonth('date'))
        .values('month', 'category__name', 'category__color')
        .annotate(cnt=Count('id'))
        .order_by('month')
    )
    count_cat_map = defaultdict(lambda: defaultdict(int))
    count_color_map = {}
    for r in count_cat_agg:
        name = r['category__name'] or 'Uncategorized'
        m = r['month'].strftime('%Y-%m')
        count_cat_map[name][m] = r['cnt']
        count_color_map[name] = r['category__color'] or '#6c757d'

    count_series = []
    count_colors = []
    for name in sorted(count_cat_map.keys(), key=lambda k: -sum(count_cat_map[k].values())):
        count_series.append({
            'name': name,
            'data': [count_cat_map[name].get(m, 0) for m in sorted_months],
        })
        count_colors.append(count_color_map[name])

    count_data = json.dumps({
        'labels': sorted_months,
        'series': count_series,
        'colors': count_colors,
    }, cls=DecimalEncoder)

    # --- Link to transactions page with reimbursement filter ---
    from core.models import Category
    reimb_cat_ids = list(
        Category.objects.filter(
            user=request.user,
            name__in=REIMBURSEMENT_CATEGORIES,
            group__slug='income',
        ).values_list('id', flat=True)
    )
    reimbursement_category_ids = '&category='.join(str(cid) for cid in reimb_cat_ids)

    context = {
        'currency_symbol': currency_symbol,
        'last_month_label': last_month_label,
        'last_month_total': last_month_total,
        'avg_monthly': avg_monthly,
        'median_monthly': median_monthly,
        'median_pct': median_pct,
        'all_time_total': all_time_total,
        'trend_data': trend_data,
        'breakdown_data': json.dumps(breakdown_data, cls=DecimalEncoder),
        'top_data': json.dumps(top_data, cls=DecimalEncoder),
        'stacked_data': stacked_data,
        'scatter_data': scatter_data,
        'count_data': count_data,
        'reimbursement_category_ids': reimbursement_category_ids,
    }
    return context


@dashboard_view("bank_income_overview", "core/dashboard_bank_income_overview.html")
def bank_income_overview_dashboard(request, display_currency, time_group):
    """Overview dashboard for all bank interest income categories."""
    from collections import defaultdict
    from datetime import timedelta
    from django.db.models import Sum
    from django.db.models.functions import TruncMonth, Abs

    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'
    abs_field = Abs(amount_field)

    from core.models import StatementImport
    today = date.today()
    latest_stmt = (
        StatementImport.objects.filter(user=request.user)
        .order_by('-statement_date').first()
    )
    if latest_stmt and latest_stmt.statement_date:
        stmt_date = latest_stmt.statement_date
        last_month_start = stmt_date.replace(day=1)
        if stmt_date.month == 12:
            last_month_end = stmt_date.replace(year=stmt_date.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            last_month_end = stmt_date.replace(month=stmt_date.month + 1, day=1) - timedelta(days=1)
        last_month_label = f"{last_month_start.strftime('%b %Y')} (latest)"
    else:
        last_month_end = today.replace(day=1) - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        last_month_label = last_month_start.strftime('%Y-%m')

    bank_qs = Transaction.objects.filter(
        user=request.user,
        category__name__in=BANK_INCOME_CATEGORIES,
        category__group__slug='income',
        **{f'{amount_field}__isnull': False},
    )

    # Summary cards
    last_month_total = float(
        bank_qs.filter(date__gte=last_month_start, date__lte=last_month_end)
        .aggregate(t=Sum(abs_field))['t'] or 0
    )
    monthly_agg = (
        bank_qs.annotate(month=TruncMonth('date'))
        .values('month').annotate(total=Sum(abs_field)).order_by('month')
    )
    monthly_map = {r['month'].strftime('%Y-%m'): float(r['total'] or 0) for r in monthly_agg}
    sorted_months = sorted(monthly_map.keys())
    monthly_totals = [monthly_map[m] for m in sorted_months]
    avg_monthly = sum(monthly_totals) / len(monthly_totals) if monthly_totals else 0
    sv = sorted(monthly_totals)
    n = len(sv)
    median_monthly = (sv[n // 2] if n % 2 else (sv[n // 2 - 1] + sv[n // 2]) / 2) if n else 0
    all_time_total = sum(monthly_totals)
    median_pct = ((last_month_total - median_monthly) / median_monthly * 100) if median_monthly else 0

    # Breakdown
    cat_breakdown = list(
        bank_qs.values('category__name', 'category__color')
        .annotate(abs_total=Sum(abs_field)).order_by('-abs_total')
    )
    breakdown_data = {'labels': [], 'values': [], 'colors': []}
    top_data = {'labels': [], 'values': [], 'colors': []}
    for r in cat_breakdown:
        name = r['category__name'] or 'Uncategorized'
        val = float(r['abs_total'] or 0)
        color = r['category__color'] or '#6c757d'
        breakdown_data['labels'].append(name)
        breakdown_data['values'].append(val)
        breakdown_data['colors'].append(color)
        top_data['labels'].append(name)
        top_data['values'].append(val)
        top_data['colors'].append(color)

    # Stacked bar
    cat_monthly = (
        bank_qs.annotate(month=TruncMonth('date'))
        .values('month', 'category__name', 'category__color')
        .annotate(total=Sum(abs_field)).order_by('month')
    )
    csm = defaultdict(lambda: defaultdict(float))
    ccm = {}
    for r in cat_monthly:
        name = r['category__name']
        m = r['month'].strftime('%Y-%m')
        csm[name][m] = float(r['total'] or 0)
        ccm[name] = r['category__color'] or '#6c757d'
    ss, sc = [], []
    for name in sorted(csm.keys(), key=lambda k: -sum(csm[k].values())):
        ss.append({'name': name, 'data': [round(csm[name].get(m, 0)) for m in sorted_months]})
        sc.append(ccm[name])

    # --- Individual transactions scatter (with category) ---
    individual_txns = list(
        bank_qs.order_by('date')
        .values_list('date', amount_field, 'description', 'category__name', 'category__color')
    )
    scatter_by_cat = defaultdict(list)
    scatter_colors = {}
    for d, amt, desc, cat_name, cat_color in individual_txns:
        cat = cat_name or 'Uncategorized'
        scatter_by_cat[cat].append({
            'date': d.isoformat() if hasattr(d, 'isoformat') else str(d),
            'amount': round(abs(float(amt))) if amt else 0,
            'description': desc,
        })
        scatter_colors[cat] = cat_color or '#6c757d'

    scatter_series = []
    scatter_color_list = []
    for cat in sorted(scatter_by_cat.keys(), key=lambda k: -len(scatter_by_cat[k])):
        scatter_series.append({'name': cat, 'data': scatter_by_cat[cat]})
        scatter_color_list.append(scatter_colors[cat])

    scatter_data = json.dumps({
        'series': scatter_series,
        'colors': scatter_color_list,
    }, cls=DecimalEncoder)

    # --- Transaction count per month (stacked by category) ---
    from django.db.models import Count
    count_cat_agg = (
        bank_qs.annotate(month=TruncMonth('date'))
        .values('month', 'category__name', 'category__color')
        .annotate(cnt=Count('id'))
        .order_by('month')
    )
    count_cat_map = defaultdict(lambda: defaultdict(int))
    count_color_map = {}
    for r in count_cat_agg:
        name = r['category__name'] or 'Uncategorized'
        m = r['month'].strftime('%Y-%m')
        count_cat_map[name][m] = r['cnt']
        count_color_map[name] = r['category__color'] or '#6c757d'

    count_series = []
    count_colors = []
    for name in sorted(count_cat_map.keys(), key=lambda k: -sum(count_cat_map[k].values())):
        count_series.append({
            'name': name,
            'data': [count_cat_map[name].get(m, 0) for m in sorted_months],
        })
        count_colors.append(count_color_map[name])

    count_data = json.dumps({
        'labels': sorted_months,
        'series': count_series,
        'colors': count_colors,
    }, cls=DecimalEncoder)

    # --- Link to transactions page ---
    from core.models import Category
    bank_cat_ids = list(
        Category.objects.filter(
            user=request.user,
            name__in=BANK_INCOME_CATEGORIES,
            group__slug='income',
        ).values_list('id', flat=True)
    )
    bank_category_ids = '&category='.join(str(cid) for cid in bank_cat_ids)

    context = {
        'currency_symbol': currency_symbol,
        'last_month_label': last_month_label,
        'last_month_total': last_month_total,
        'avg_monthly': avg_monthly,
        'median_monthly': median_monthly,
        'median_pct': median_pct,
        'all_time_total': all_time_total,
        'breakdown_data': json.dumps(breakdown_data, cls=DecimalEncoder),
        'stacked_data': json.dumps({'labels': sorted_months, 'series': ss, 'colors': sc}, cls=DecimalEncoder),
        'scatter_data': scatter_data,
        'count_data': count_data,
        'bank_category_ids': bank_category_ids,
    }
    return context



@dashboard_view("credit_payment", "core/dashboard_credit_payment.html")
def credit_payment_dashboard(request, display_currency, time_group):
    """Dashboard for credit card payment analysis — matching debit/credit sides."""
    from collections import defaultdict
    from datetime import timedelta
    from django.db.models import Sum
    from django.db.models.functions import TruncMonth, Abs

    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'
    abs_field = Abs(amount_field)

    # Find categories that contain credit-account transactions, then fetch both sides
    credit_cat_ids = list(
        Transaction.objects.filter(
            user=request.user,
            category__group__slug='transfer',
            raw_transaction__ledger__statement_import__account__account_type='credit_account',
        ).values_list('category_id', flat=True).distinct()
    )
    cp_qs = Transaction.objects.filter(
        user=request.user,
        category_id__in=credit_cat_ids,
    ).exclude(
        amount_crc__isnull=True, amount_usd__isnull=True,
    ).select_related('raw_transaction__ledger__statement_import__account')

    # Separate by account type, store both currency amounts for matching
    credit_txns = []
    debit_txns = []
    for t in cp_qs.order_by('date'):
        raw = t.raw_transaction
        ledger = raw.ledger if raw else None
        stmt = ledger.statement_import if ledger else None
        acct = stmt.account if stmt else None
        is_credit = hasattr(acct, 'creditaccount') if acct else False
        amt_display = abs(float(getattr(t, amount_field) or 0))
        amt_crc = abs(float(t.amount_crc or 0))
        amt_usd = abs(float(t.amount_usd or 0))
        entry = {
            'date': t.date, 'amount': amt_display,
            'amount_crc': amt_crc, 'amount_usd': amt_usd,
            'description': t.description, 'id': t.id,
        }
        if is_credit:
            credit_txns.append(entry)
        else:
            debit_txns.append(entry)

    # Match credit entries to debit entries (±2 days, close amount in either currency)
    matched_credits = set()
    matched_debits = set()
    matches = []
    unmatched_credits = []

    for ci, ct in enumerate(credit_txns):
        best_match = None
        best_diff = float('inf')
        for di, dt in enumerate(debit_txns):
            if di in matched_debits:
                continue
            day_diff = abs((ct['date'] - dt['date']).days)
            if day_diff > 2:
                continue
            # Match on CRC or USD — whichever is closer
            crc_diff = abs(ct['amount_crc'] - dt['amount_crc']) if ct['amount_crc'] and dt['amount_crc'] else float('inf')
            usd_diff = abs(ct['amount_usd'] - dt['amount_usd']) if ct['amount_usd'] and dt['amount_usd'] else float('inf')
            amt_diff = min(crc_diff, usd_diff)
            if amt_diff < 5000 and amt_diff < best_diff:
                best_diff = amt_diff
                best_match = di
        if best_match is not None:
            matched_credits.add(ci)
            matched_debits.add(best_match)
            matches.append({'credit': ct, 'debit': debit_txns[best_match], 'fee': debit_txns[best_match]['amount'] - ct['amount']})
        else:
            unmatched_credits.append(ct)

    # Summary
    total_credit = sum(c['amount'] for c in credit_txns)
    total_debit = sum(d['amount'] for d in debit_txns)
    total_matched = sum(m['credit']['amount'] for m in matches)
    total_unmatched = sum(u['amount'] for u in unmatched_credits)
    match_rate = (len(matches) / len(credit_txns) * 100) if credit_txns else 0

    # Monthly trend: credit vs debit totals
    monthly_credit = defaultdict(float)
    monthly_debit = defaultdict(float)
    monthly_unmatched = defaultdict(float)
    for ct in credit_txns:
        m = ct['date'].strftime('%Y-%m')
        monthly_credit[m] += ct['amount']
    for dt in debit_txns:
        m = dt['date'].strftime('%Y-%m')
        monthly_debit[m] += dt['amount']
    for u in unmatched_credits:
        m = u['date'].strftime('%Y-%m')
        monthly_unmatched[m] += u['amount']

    all_months = sorted(set(list(monthly_credit.keys()) + list(monthly_debit.keys())))

    trend_data = json.dumps({
        'labels': all_months,
        'credit': [round(monthly_credit.get(m, 0)) for m in all_months],
        'debit': [round(monthly_debit.get(m, 0)) for m in all_months],
        'unmatched': [round(monthly_unmatched.get(m, 0)) for m in all_months],
    }, cls=DecimalEncoder)

    # Unmatched table data
    unmatched_json = json.dumps([{
        'date': u['date'].isoformat(),
        'amount': round(u['amount']),
        'description': u['description'],
    } for u in sorted(unmatched_credits, key=lambda x: x['date'], reverse=True)], cls=DecimalEncoder)

    context = {
        'currency_symbol': currency_symbol,
        'total_credit': total_credit,
        'total_debit': total_debit,
        'total_matched': total_matched,
        'total_unmatched': total_unmatched,
        'match_rate': match_rate,
        'match_count': len(matches),
        'unmatched_count': len(unmatched_credits),
        'total_count': len(credit_txns),
        'trend_data': trend_data,
        'unmatched_credits': unmatched_credits,
    }
    return context


@dashboard_view("personal_account", "core/dashboard_personal_account.html")
def personal_account_dashboard(request, display_currency, time_group):
    """Dashboard for Personal Account transfers — internal vs external."""
    from collections import defaultdict
    from django.db.models.functions import TruncMonth, Abs

    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'

    pa_qs = Transaction.objects.filter(
        user=request.user,
        category__group__slug='transfer',
    ).exclude(
        amount_crc__isnull=True, amount_usd__isnull=True,
    ).select_related('raw_transaction__ledger__statement_import__account', 'category')

    # Collect all transactions with their account info
    all_txns = []
    for t in pa_qs.order_by('date'):
        raw = t.raw_transaction
        ledger = raw.ledger if raw else None
        stmt = ledger.statement_import if ledger else None
        acct = stmt.account if stmt else None
        acct_id = acct.id if acct else None
        amt_display = float(getattr(t, amount_field) or 0)
        amt_crc = float(t.amount_crc or 0)
        amt_usd = float(t.amount_usd or 0)
        all_txns.append({
            'date': t.date, 'amount': amt_display,
            'amount_crc': amt_crc, 'amount_usd': amt_usd,
            'description': t.description, 'account_id': acct_id, 'id': t.id,
            'category': t.category.name,
        })

    # Match: find pairs on same/adjacent day, same category, different accounts,
    # opposite signs, close amounts
    matched = set()
    internal_txns = []
    external_txns = []

    for i, ti in enumerate(all_txns):
        if i in matched:
            continue
        found_pair = False
        for j, tj in enumerate(all_txns):
            if j <= i or j in matched:
                continue
            if ti['category'] != tj['category']:
                continue
            if ti['account_id'] == tj['account_id']:
                continue
            day_diff = abs((ti['date'] - tj['date']).days)
            if day_diff > 2:
                continue
            # Opposite signs check
            if ti['amount_crc'] * tj['amount_crc'] >= 0:
                continue
            # Match on CRC or USD — whichever is closer
            crc_diff = abs(abs(ti['amount_crc']) - abs(tj['amount_crc'])) if ti['amount_crc'] and tj['amount_crc'] else float('inf')
            usd_diff = abs(abs(ti['amount_usd']) - abs(tj['amount_usd'])) if ti['amount_usd'] and tj['amount_usd'] else float('inf')
            amt_diff = min(crc_diff, usd_diff)
            threshold = min(abs(ti['amount_crc']), abs(tj['amount_crc'])) * 0.02 + 5000
            if amt_diff < threshold:
                    matched.add(i)
                    matched.add(j)
                    internal_txns.append(ti)
                    internal_txns.append(tj)
                    found_pair = True
                    break
        if not found_pair and i not in matched:
            external_txns.append(ti)

    # Summary
    total_internal = sum(abs(t['amount']) for t in internal_txns) / 2  # pairs counted twice
    total_external_out = sum(abs(t['amount']) for t in external_txns if t['amount'] < 0)
    total_external_in = sum(abs(t['amount']) for t in external_txns if t['amount'] > 0)

    # Monthly trend
    monthly_internal = defaultdict(float)
    monthly_ext_out = defaultdict(float)
    monthly_ext_in = defaultdict(float)
    for t in internal_txns:
        if t['amount'] < 0:
            monthly_internal[t['date'].strftime('%Y-%m')] += abs(t['amount'])
    for t in external_txns:
        m = t['date'].strftime('%Y-%m')
        if t['amount'] < 0:
            monthly_ext_out[m] += abs(t['amount'])
        else:
            monthly_ext_in[m] += abs(t['amount'])

    all_months = sorted(set(
        list(monthly_internal.keys()) + list(monthly_ext_out.keys()) + list(monthly_ext_in.keys())
    ))

    trend_data = json.dumps({
        'labels': all_months,
        'internal': [round(monthly_internal.get(m, 0)) for m in all_months],
        'external_out': [round(monthly_ext_out.get(m, 0)) for m in all_months],
        'external_in': [round(monthly_ext_in.get(m, 0)) for m in all_months],
    }, cls=DecimalEncoder)

    # External transactions table
    ext_sorted = sorted(external_txns, key=lambda t: t['date'], reverse=True)

    context = {
        'currency_symbol': currency_symbol,
        'total_txns': len(all_txns),
        'internal_count': len(internal_txns) // 2,
        'external_count': len(external_txns),
        'total_internal': total_internal,
        'total_external_out': total_external_out,
        'total_external_in': total_external_in,
        'trend_data': trend_data,
        'external_txns': ext_sorted,
    }
    return context


def _match_transfer_pairs(user, amount_field, display_currency):
    """Shared logic: match transfer-group transactions into internal pairs / external.

    Uses the same rules as pair_matcher.py: same category, different accounts,
    opposite CRC signs, ±2 day tolerance, and 2% + ₡5000 amount threshold.
    """
    from collections import defaultdict

    pa_qs = Transaction.objects.filter(
        user=user,
        category__group__slug='transfer',
    ).exclude(
        amount_crc__isnull=True, amount_usd__isnull=True,
    ).select_related('raw_transaction__ledger__statement_import__account', 'category')

    all_txns = []
    for t in pa_qs.order_by('date'):
        raw = t.raw_transaction
        ledger = raw.ledger if raw else None
        stmt = ledger.statement_import if ledger else None
        acct = stmt.account if stmt else None
        currency = ledger.currency if ledger else ''
        acct_label = str(acct) if acct else 'Unknown'
        all_txns.append({
            'date': t.date,
            'amount': float(getattr(t, amount_field) or 0),
            'amount_crc': float(t.amount_crc or 0),
            'amount_usd': float(t.amount_usd or 0),
            'description': t.description,
            'account_id': acct.id if acct else None,
            'account_name': acct_label,
            'category': t.category.name,
            'id': t.id,
        })

    matched = set()
    internal_pairs = []
    external_txns = []

    for i, ti in enumerate(all_txns):
        if i in matched:
            continue
        best_match = None
        best_diff = float('inf')
        for j, tj in enumerate(all_txns):
            if j <= i or j in matched:
                continue
            # Same category, different accounts, opposite CRC signs
            if ti['category'] != tj['category']:
                continue
            if ti['account_id'] == tj['account_id']:
                continue
            if ti['amount_crc'] * tj['amount_crc'] >= 0:
                continue
            if abs((ti['date'] - tj['date']).days) > 2:
                continue
            crc_diff = abs(abs(ti['amount_crc']) - abs(tj['amount_crc'])) if ti['amount_crc'] and tj['amount_crc'] else float('inf')
            usd_diff = abs(abs(ti['amount_usd']) - abs(tj['amount_usd'])) if ti['amount_usd'] and tj['amount_usd'] else float('inf')
            amt_diff = min(crc_diff, usd_diff)
            threshold = min(abs(ti['amount_crc']), abs(tj['amount_crc'])) * 0.02 + 5000
            if amt_diff < threshold and amt_diff < best_diff:
                best_diff = amt_diff
                best_match = j
        if best_match is not None:
            tj = all_txns[best_match]
            matched.add(i)
            matched.add(best_match)
            out_side = ti if ti['amount_crc'] < 0 else tj
            in_side = tj if ti['amount_crc'] < 0 else ti
            out_side['abs_amount'] = abs(out_side['amount'])
            internal_pairs.append({'out': out_side, 'in': in_side})
        elif i not in matched:
            external_txns.append(ti)

    return all_txns, internal_pairs, external_txns


@dashboard_view("internal_transfers", "core/dashboard_internal_transfers.html")
def internal_transfers_dashboard(request, display_currency, time_group):
    """Dashboard dedicated to internal transfers between registered accounts."""
    from collections import defaultdict

    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'

    _, internal_pairs, _ = _match_transfer_pairs(request.user, amount_field, display_currency)

    # Summary
    total_volume = sum(abs(p['out']['amount']) for p in internal_pairs)
    pair_count = len(internal_pairs)
    avg_transfer = total_volume / pair_count if pair_count else 0

    # Determine account type label for each side of a pair
    def _acct_type(name):
        if name.startswith('Credit'):
            return 'Credit'
        return 'Debit'

    # Monthly trend — broken down by route (e.g. Debit → Credit)
    monthly_volume = defaultdict(lambda: defaultdict(float))
    monthly_count = defaultdict(lambda: defaultdict(int))
    route_totals = defaultdict(float)
    for p in internal_pairs:
        m = p['out']['date'].strftime('%Y-%m')
        route = f"{_acct_type(p['out']['account_name'])} → {_acct_type(p['in']['account_name'])}"
        vol = abs(p['out']['amount'])
        monthly_volume[m][route] += vol
        monthly_count[m][route] += 1
        route_totals[route] += vol

    all_months = sorted(monthly_volume.keys())
    routes = sorted(route_totals.keys(), key=lambda r: -route_totals[r])

    # Assign colors per route
    route_colors = {}
    palette = ['#3498db', '#2ecc71', '#e67e22', '#9b59b6', '#1abc9c', '#34495e']
    for i, r in enumerate(routes):
        route_colors[r] = palette[i % len(palette)]

    volume_data = json.dumps({
        'labels': all_months,
        'routes': routes,
        'colors': [route_colors[r] for r in routes],
        'series': [{
            'name': r,
            'data': [round(monthly_volume[m].get(r, 0)) for m in all_months],
        } for r in routes],
    }, cls=DecimalEncoder)

    count_data = json.dumps({
        'labels': all_months,
        'routes': routes,
        'colors': [route_colors[r] for r in routes],
        'series': [{
            'name': r,
            'data': [monthly_count[m].get(r, 0) for m in all_months],
        } for r in routes],
    }, cls=DecimalEncoder)

    # Route summary for cards
    route_summary = [
        {'route': r, 'volume': round(route_totals[r]), 'color': route_colors[r],
         'count': sum(monthly_count[m].get(r, 0) for m in all_months)}
        for r in routes
    ]

    # Paired table (most recent first)
    pairs_sorted = sorted(internal_pairs, key=lambda p: p['out']['date'], reverse=True)

    context = {
        'currency_symbol': currency_symbol,
        'total_volume': total_volume,
        'pair_count': pair_count,
        'avg_transfer': avg_transfer,
        'volume_data': volume_data,
        'count_data': count_data,
        'route_summary': route_summary,
        'pairs': pairs_sorted,
    }
    return context


@dashboard_view("credit_transfers", "core/dashboard_credit_transfers.html")
def credit_transfers_dashboard(request, display_currency, time_group):
    """Dashboard matching credit card payments from debit to credit accounts."""
    from collections import defaultdict

    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'

    # Get all transfer transactions that involve a credit account on either side.
    # Find categories containing credit-account transactions, then fetch both sides.
    credit_cat_ids = list(
        Transaction.objects.filter(
            user=request.user,
            category__group__slug='transfer',
            raw_transaction__ledger__statement_import__account__account_type='credit_account',
        ).values_list('category_id', flat=True).distinct()
    )
    cp_qs = Transaction.objects.filter(
        user=request.user,
        category_id__in=credit_cat_ids,
    ).exclude(
        amount_crc__isnull=True, amount_usd__isnull=True,
    ).select_related('raw_transaction__ledger__statement_import__account')

    # Separate by account type
    credit_txns = []
    debit_txns = []
    for t in cp_qs.order_by('date'):
        raw = t.raw_transaction
        ledger = raw.ledger if raw else None
        stmt = ledger.statement_import if ledger else None
        acct = stmt.account if stmt else None
        is_credit = hasattr(acct, 'creditaccount') if acct else False
        currency = ledger.currency if ledger else ''
        acct_label = str(acct) if acct else 'Unknown'
        entry = {
            'date': t.date,
            'amount': float(getattr(t, amount_field) or 0),
            'amount_crc': float(t.amount_crc or 0),
            'amount_usd': float(t.amount_usd or 0),
            'description': t.description,
            'account_name': acct_label,
            'currency': currency,
            'id': t.id,
        }
        if is_credit:
            credit_txns.append(entry)
        else:
            debit_txns.append(entry)

    # Match debit→credit pairs
    matched_debits = set()
    matched_credits = set()
    pairs = []
    unmatched_credit = []
    unmatched_debit = []

    for ci, ct in enumerate(credit_txns):
        best_match = None
        best_diff = float('inf')
        for di, dt in enumerate(debit_txns):
            if di in matched_debits:
                continue
            day_diff = abs((ct['date'] - dt['date']).days)
            if day_diff > 2:
                continue
            crc_diff = abs(abs(ct['amount_crc']) - abs(dt['amount_crc'])) if ct['amount_crc'] and dt['amount_crc'] else float('inf')
            usd_diff = abs(abs(ct['amount_usd']) - abs(dt['amount_usd'])) if ct['amount_usd'] and dt['amount_usd'] else float('inf')
            amt_diff = min(crc_diff, usd_diff)
            if amt_diff < 5000 and amt_diff < best_diff:
                best_diff = amt_diff
                best_match = di
        if best_match is not None:
            matched_credits.add(ci)
            matched_debits.add(best_match)
            ct['abs_amount'] = abs(ct['amount'])
            pairs.append({'debit': debit_txns[best_match], 'credit': ct})
        else:
            unmatched_credit.append(ct)

    for di, dt in enumerate(debit_txns):
        if di not in matched_debits:
            unmatched_debit.append(dt)

    # Summary
    total_matched = sum(abs(p['credit']['amount']) for p in pairs)
    total_unmatched_credit = sum(abs(u['amount']) for u in unmatched_credit)
    total_unmatched_debit = sum(abs(u['amount']) for u in unmatched_debit)
    match_rate = (len(pairs) / len(credit_txns) * 100) if credit_txns else 0

    # Monthly trend
    monthly_credit = defaultdict(float)
    monthly_debit = defaultdict(float)
    monthly_unmatched = defaultdict(float)
    for ct in credit_txns:
        m = ct['date'].strftime('%Y-%m')
        monthly_credit[m] += abs(ct['amount'])
    for dt in debit_txns:
        m = dt['date'].strftime('%Y-%m')
        monthly_debit[m] += abs(dt['amount'])
    for u in unmatched_credit:
        m = u['date'].strftime('%Y-%m')
        monthly_unmatched[m] += abs(u['amount'])

    all_months = sorted(set(list(monthly_credit.keys()) + list(monthly_debit.keys())))

    trend_data = json.dumps({
        'labels': all_months,
        'credit': [round(monthly_credit.get(m, 0)) for m in all_months],
        'debit': [round(monthly_debit.get(m, 0)) for m in all_months],
        'unmatched': [round(monthly_unmatched.get(m, 0)) for m in all_months],
    }, cls=DecimalEncoder)

    pairs_sorted = sorted(pairs, key=lambda p: p['credit']['date'], reverse=True)

    for u in unmatched_credit:
        u['abs_amount'] = abs(u['amount'])
    for u in unmatched_debit:
        u['abs_amount'] = abs(u['amount'])
    unmatched_credit_sorted = sorted(unmatched_credit, key=lambda u: u['date'], reverse=True)
    unmatched_debit_sorted = sorted(unmatched_debit, key=lambda u: u['date'], reverse=True)

    context = {
        'currency_symbol': currency_symbol,
        'pair_count': len(pairs),
        'total_matched': total_matched,
        'unmatched_credit_count': len(unmatched_credit),
        'total_unmatched_credit': total_unmatched_credit,
        'unmatched_debit_count': len(unmatched_debit),
        'total_unmatched_debit': total_unmatched_debit,
        'match_rate': match_rate,
        'trend_data': trend_data,
        'pairs': pairs_sorted,
        'unmatched_credit': unmatched_credit_sorted,
        'unmatched_debit': unmatched_debit_sorted,
    }
    return context


@dashboard_view("external_transfers", "core/dashboard_external_transfers.html")
def external_transfers_dashboard(request, display_currency, time_group):
    """Dashboard for external transfers — money going to/from non-registered accounts."""
    from collections import defaultdict

    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'

    # External transfers are unmatched transfer-group transactions
    _, _, ext_list = _match_transfer_pairs(request.user, amount_field, display_currency)

    outgoing = []
    incoming = []
    for t in ext_list:
        amt = t['amount']
        entry = {
            'date': t['date'], 'amount': amt, 'abs_amount': abs(amt),
            'description': t['description'], 'category': t['category'],
            'account_name': t.get('account_name', ''),
        }
        if amt < 0:
            outgoing.append(entry)
        else:
            incoming.append(entry)

    # Sort by date descending
    outgoing.sort(key=lambda e: e['date'], reverse=True)
    incoming.sort(key=lambda e: e['date'], reverse=True)
    all_txns = outgoing + incoming

    total_out = sum(t['abs_amount'] for t in outgoing)
    total_in = sum(t['abs_amount'] for t in incoming)
    net = total_in - total_out

    # Monthly trend
    monthly_out = defaultdict(float)
    monthly_in = defaultdict(float)
    for t in outgoing:
        monthly_out[t['date'].strftime('%Y-%m')] += t['abs_amount']
    for t in incoming:
        monthly_in[t['date'].strftime('%Y-%m')] += t['abs_amount']

    all_months = sorted(set(list(monthly_out.keys()) + list(monthly_in.keys())))

    trend_data = json.dumps({
        'labels': all_months,
        'outgoing': [round(monthly_out.get(m, 0)) for m in all_months],
        'incoming': [round(monthly_in.get(m, 0)) for m in all_months],
    }, cls=DecimalEncoder)

    context = {
        'currency_symbol': currency_symbol,
        'total_out': total_out,
        'total_in': total_in,
        'net': net,
        'out_count': len(outgoing),
        'in_count': len(incoming),
        'total_count': len(all_txns),
        'trend_data': trend_data,
        'outgoing': outgoing,
        'incoming': incoming,
    }
    return context


@dashboard_view("transfer_flow", "core/dashboard_transfer_flow.html")
def transfer_flow_dashboard(request, display_currency, time_group):
    """Graph view of money flows between accounts and external nodes."""
    from collections import defaultdict
    from django.db.models.functions import Abs

    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'

    flows = defaultdict(float)
    flow_counts = defaultdict(int)

    # Build flows from matched pairs and unmatched transactions
    _, internal_pairs, external_txns = _match_transfer_pairs(request.user, amount_field, display_currency)

    # Paired transfers: actual account → account
    for p in internal_pairs:
        src = p['out']['account_name']
        dst = p['in']['account_name']
        vol = abs(p['out']['amount'])
        flows[(src, dst)] += vol
        flow_counts[(src, dst)] += 1

    # Unpaired transfers: account → category (outgoing) or category → account (incoming)
    for t in external_txns:
        raw = t.get('raw_transaction')
        acct_name = t.get('account_name', 'Unknown')
        cat = t['category']
        amt = t['amount']
        if amt < 0:
            flows[(acct_name, cat)] += abs(amt)
            flow_counts[(acct_name, cat)] += 1
        elif amt > 0:
            flows[(cat, acct_name)] += abs(amt)
            flow_counts[(cat, acct_name)] += 1

    # Add income sources flowing into debit accounts
    from django.db.models.functions import Abs as _Abs
    income_qs = Transaction.objects.filter(
        user=request.user,
        category__group__slug='income',
        **{f'{amount_field}__isnull': False},
    ).select_related(
        'raw_transaction__ledger__statement_import__account',
        'category',
    )

    # Map income categories to unified labels
    income_label_map = {}
    for cat_name in ['Work Salary', 'Work Bonuses', 'Work Association', 'Work Government']:
        income_label_map[cat_name] = 'Work Income'
    for cat_name in REIMBURSEMENT_CATEGORIES:
        income_label_map[cat_name] = 'Reimbursement Income'
    for cat_name in BANK_INCOME_CATEGORIES:
        income_label_map[cat_name] = 'Bank Income'

    for t in income_qs:
        raw = t.raw_transaction
        ledger = raw.ledger if raw else None
        stmt = ledger.statement_import if ledger else None
        acct = stmt.account if stmt else None
        acct_name = str(acct) if acct else 'Unknown'
        cat = t.category.name
        amt = float(getattr(t, amount_field) or 0)
        if amt > 0:
            if cat in REIMBURSEMENT_CATEGORIES:
                label = 'Reimbursement Income'
            elif cat in BANK_INCOME_CATEGORIES:
                label = 'Bank Income'
            else:
                label = cat
            flows[(label, acct_name)] += abs(amt)
            flow_counts[(label, acct_name)] += 1

    # Add expenses flowing out of accounts
    expense_qs = Transaction.objects.filter(
        user=request.user,
        category__group__slug='expense',
        **{f'{amount_field}__isnull': False},
    ).select_related(
        'raw_transaction__ledger__statement_import__account',
    )

    for t in expense_qs:
        raw = t.raw_transaction
        ledger = raw.ledger if raw else None
        stmt = ledger.statement_import if ledger else None
        acct = stmt.account if stmt else None
        acct_name = str(acct) if acct else 'Unknown'
        amt = float(getattr(t, amount_field) or 0)
        if amt < 0:
            flows[(acct_name, 'Expenses')] += abs(amt)
            flow_counts[(acct_name, 'Expenses')] += 1

    # Build nodes and links for Sankey — resolve circular flows by netting
    all_nodes = set()
    sankey_flows = {}
    table_rows = []
    processed = set()

    for (src, dst), vol in sorted(flows.items(), key=lambda x: -x[1]):
        pair_key = tuple(sorted([src, dst]))
        if pair_key in processed:
            continue
        processed.add(pair_key)

        reverse_vol = flows.get((dst, src), 0)
        reverse_cnt = flow_counts.get((dst, src), 0)
        fwd_vol = vol
        fwd_cnt = flow_counts[(src, dst)]

        # Table gets both directions
        table_rows.append({'source': src, 'target': dst, 'volume': round(fwd_vol), 'count': fwd_cnt})
        if reverse_vol > 0:
            table_rows.append({'source': dst, 'target': src, 'volume': round(reverse_vol), 'count': reverse_cnt})

        # Sankey gets net direction only
        net = fwd_vol - reverse_vol
        if net > 0:
            sankey_flows[(src, dst)] = round(net)
        elif net < 0:
            sankey_flows[(dst, src)] = round(abs(net))
        # If exactly equal, skip (net zero)

    for (src, dst) in sankey_flows:
        all_nodes.add(src)
        all_nodes.add(dst)

    node_list = sorted(all_nodes)
    node_index = {n: i for i, n in enumerate(node_list)}
    sankey_links = [{'source': node_index[src], 'target': node_index[dst], 'value': vol}
                    for (src, dst), vol in sankey_flows.items() if vol > 0]

    # Compute net balance per node from raw flows (not netted)
    node_inflows = defaultdict(float)
    node_outflows = defaultdict(float)
    for (src, dst), vol in flows.items():
        node_outflows[src] += vol
        node_inflows[dst] += vol
    node_balances = {}
    for n in node_list:
        net = node_inflows[n] - node_outflows[n]
        if abs(net) > 100:
            node_balances[n] = round(net)

    # Sort table by volume
    table_rows.sort(key=lambda r: -r['volume'])

    flow_data = json.dumps({
        'nodes': node_list,
        'links': sankey_links,
        'balances': node_balances,
    }, cls=DecimalEncoder)

    context = {
        'currency_symbol': currency_symbol,
        'flow_data': flow_data,
        'table_rows': table_rows,
        'total_volume': sum(r['volume'] for r in table_rows),
    }
    return context


@dashboard_view("transfer_graph", "core/dashboard_transfer_graph.html")
def transfer_graph_dashboard(request, display_currency, time_group):
    """Network graph view of connections between accounts."""
    from collections import defaultdict

    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'

    # Build edges from matched pairs and unmatched transactions
    edges = defaultdict(lambda: {'volume': 0, 'count': 0})

    _, internal_pairs, external_txns = _match_transfer_pairs(request.user, amount_field, display_currency)

    # Paired transfers: account → account
    for p in internal_pairs:
        src = p['out']['account_name']
        dst = p['in']['account_name']
        edges[(src, dst)]['volume'] += abs(p['out']['amount'])
        edges[(src, dst)]['count'] += 1

    # Unpaired transfers: account → category or category → account
    for t in external_txns:
        acct_name = t.get('account_name', 'Unknown')
        cat = t['category']
        amt = t['amount']
        if amt < 0:
            edges[(acct_name, cat)]['volume'] += abs(amt)
            edges[(acct_name, cat)]['count'] += 1
        elif amt > 0:
            edges[(cat, acct_name)]['volume'] += abs(amt)
            edges[(cat, acct_name)]['count'] += 1

    # Build graph data
    all_nodes = set()
    for src, dst in edges:
        all_nodes.add(src)
        all_nodes.add(dst)

    node_types = {}
    for n in all_nodes:
        if n.startswith('Debit'):
            node_types[n] = 'debit'
        elif n.startswith('Credit'):
            node_types[n] = 'credit'
        else:
            node_types[n] = 'external'

    graph_data = json.dumps({
        'nodes': [{'id': n, 'type': node_types.get(n, 'external')} for n in sorted(all_nodes)],
        'links': [{'source': src, 'target': dst, 'volume': round(e['volume']), 'count': e['count']}
                  for (src, dst), e in sorted(edges.items(), key=lambda x: -x[1]['volume'])],
    }, cls=DecimalEncoder)

    context = {
        'currency_symbol': currency_symbol,
        'graph_data': graph_data,
    }
    return context


@dashboard_view("transaction_pairing", "core/dashboard_transaction_pairing.html")
def transaction_pairing_dashboard(request, display_currency, time_group):
    """Dashboard for managing transfer transaction pairs."""
    from django.views.decorators.http import require_POST
    from core.models import TransactionPair
    from core.services.pair_matcher import auto_match_transfers

    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'

    # Handle clear pairs POST
    if request.method == 'POST' and 'clear_pairs' in request.POST:
        from django.contrib import messages
        count = TransactionPair.objects.filter(user=request.user).count()
        TransactionPair.objects.filter(user=request.user).delete()
        messages.success(request, f'Cleared {count} pairs.')

    # Handle auto-match POST
    if request.method == 'POST' and 'run_auto_match' in request.POST:
        result = auto_match_transfers(request.user)
        from django.contrib import messages
        messages.success(request, f'Auto-match complete: {result.paired} paired, {result.unmatched} unmatched, {result.skipped} skipped.')

    # Fetch all pairs
    pairs_qs = TransactionPair.objects.filter(user=request.user).select_related(
        'outgoing__ledger__statement_import__account',
        'incoming__ledger__statement_import__account',
    )

    internal = []
    external_outgoing = []
    external_incoming = []
    for p in pairs_qs:
        entry = {'id': p.id, 'status': p.status, 'created_at': p.created_at}

        if p.outgoing:
            out_lt = p.outgoing.logical_transactions.select_related('category').first()
            out_acct = p.outgoing.ledger.statement_import.account
            entry['out_date'] = p.outgoing.date
            entry['out_account'] = str(out_acct)
            entry['out_currency'] = p.outgoing.ledger.currency
            entry['out_description'] = p.outgoing.description
            entry['out_amount'] = abs(float(getattr(out_lt, amount_field) or 0)) if out_lt else 0
            entry['out_category'] = out_lt.category.name if out_lt and out_lt.category else ''
        else:
            entry['out_date'] = None
            entry['out_account'] = '—'
            entry['out_description'] = '—'
            entry['out_amount'] = 0
            entry['out_category'] = ''
            entry['out_currency'] = ''

        if p.incoming:
            in_lt = p.incoming.logical_transactions.select_related('category').first()
            in_acct = p.incoming.ledger.statement_import.account
            entry['in_date'] = p.incoming.date
            entry['in_account'] = str(in_acct)
            entry['in_currency'] = p.incoming.ledger.currency
            entry['in_description'] = p.incoming.description
            entry['in_amount'] = abs(float(getattr(in_lt, amount_field) or 0)) if in_lt else 0
            entry['in_category'] = in_lt.category.name if in_lt and in_lt.category else ''
        else:
            entry['in_date'] = None
            entry['in_account'] = '—'
            entry['in_description'] = '—'
            entry['in_amount'] = 0
            entry['in_category'] = ''
            entry['in_currency'] = ''

        if p.status == 'paired':
            internal.append(entry)
        elif p.outgoing and not p.incoming:
            external_outgoing.append(entry)
        else:
            external_incoming.append(entry)

    # Sort by date descending
    internal.sort(key=lambda e: e['out_date'] or e['in_date'] or '', reverse=True)
    external_outgoing.sort(key=lambda e: e['out_date'] or '', reverse=True)
    external_incoming.sort(key=lambda e: e['in_date'] or '', reverse=True)

    external_count = len(external_outgoing) + len(external_incoming)
    total = len(internal) + external_count
    match_rate = (len(internal) / total * 100) if total else 0

    context = {
        'currency_symbol': currency_symbol,
        'internal': internal,
        'external_outgoing': external_outgoing,
        'external_incoming': external_incoming,
        'internal_count': len(internal),
        'external_outgoing_count': len(external_outgoing),
        'external_incoming_count': len(external_incoming),
        'external_count': external_count,
        'total_count': total,
        'match_rate': match_rate,
    }
    return context


@dashboard_view("category_stats", "core/dashboard_category_stats.html")
def category_stats_dashboard(request, display_currency, time_group):
    """Dashboard comparing last month per category vs historical min/median/avg/max."""
    from collections import defaultdict
    from django.db.models import Sum
    from django.db.models.functions import TruncMonth, Abs

    user = request.user
    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'
    abs_field = Abs(amount_field)

    qs = LogicalTransaction.objects.filter(
        user=user,
        category__isnull=False,
        **{f'{amount_field}__isnull': False},
    ).exclude(category__name='Unclassified')

    monthly_cat = (
        qs.annotate(month=TruncMonth('date'))
        .values('month', 'category__name', 'category__group__slug', 'category__color')
        .annotate(total=Sum(abs_field))
        .order_by('category__group__slug', 'category__name', 'month')
    )

    cat_months = defaultdict(lambda: {'month_data': {}, 'group': '', 'color': '#6c757d'})
    all_months = set()
    for r in monthly_cat:
        key = r['category__name']
        m = r['month'].strftime('%Y-%m')
        cat_months[key]['month_data'][m] = float(r['total'] or 0)
        cat_months[key]['group'] = r['category__group__slug']
        cat_months[key]['color'] = r['category__color'] or '#6c757d'
        all_months.add(r['month'])

    sorted_months = sorted(all_months)
    last_12_months = sorted_months[-12:] if len(sorted_months) >= 12 else sorted_months
    month_labels = [m.strftime('%Y-%m') for m in last_12_months]
    last_month = sorted_months[-1] if sorted_months else None
    last_month_name = last_month.strftime('%B %Y') if last_month else 'N/A'

    last_month_totals = {}
    if last_month:
        last_month_qs = (
            qs.filter(date__year=last_month.year, date__month=last_month.month)
            .values('category__name')
            .annotate(total=Sum(abs_field))
        )
        for r in last_month_qs:
            last_month_totals[r['category__name']] = float(r['total'] or 0)

    def compute_stats(values):
        if not values:
            return {'min': 0, 'max': 0, 'avg': 0, 'median': 0}
        s = sorted(values)
        n = len(s)
        return {
            'min': s[0],
            'max': s[-1],
            'avg': sum(s) / n,
            'median': s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2,
        }

    expense_categories = []
    income_categories = []

    for cat_name, data in sorted(cat_months.items(), key=lambda x: x[0]):
        all_vals = list(data['month_data'].values())
        stats = compute_stats(all_vals)
        last_val = last_month_totals.get(cat_name, 0)
        # Monthly values for the last 12 months (0 if no data)
        monthly_vals = [round(data['month_data'].get(m, 0)) for m in month_labels]
        # Deviation from median per month
        med = stats['median']
        monthly_dev = [round((v - med) / med * 100) if med > 0 else 0 for v in monthly_vals]
        entry = {
            'name': cat_name,
            'color': data['color'],
            'last_month': round(last_val),
            'min': round(stats['min']),
            'median': round(stats['median']),
            'avg': round(stats['avg']),
            'max': round(stats['max']),
            'monthly_dev': monthly_dev,
        }
        if data['group'] == 'expense':
            expense_categories.append(entry)
        elif data['group'] == 'income':
            income_categories.append(entry)

    expense_categories.sort(key=lambda x: x['last_month'], reverse=True)
    income_categories.sort(key=lambda x: x['last_month'], reverse=True)

    context = {
        'currency_symbol': currency_symbol,
        'last_month_name': last_month_name,
        'month_labels': month_labels,
        'expense_categories': expense_categories,
        'income_categories': income_categories,
        'expense_data': json.dumps(expense_categories, cls=DecimalEncoder),
        'income_data': json.dumps(income_categories, cls=DecimalEncoder),
        'month_labels_json': json.dumps(month_labels),
    }
    return context
