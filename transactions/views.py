import json
import logging
import os
from datetime import date

from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST
from django.utils.http import urlencode

from .models import Transaction, LogicalTransaction, RawTransaction, Category, CategoryGroup, StatementImport, CurrencyLedger, Account, CreditAccount, DebitAccount, ClassificationRule, UserPreference
from django.db import models as db_models
from django.db.models import Prefetch
from .forms import UploadForm, YamlRuleForm
from .parsers.credit_card import CreditCardParser
from .parsers.debit_card import DebitCardParser
from .services.stats import get_dashboard_stats
from .services.classifier import classify_transaction
from .services.exchange_rates import fetch_rates, convert_transaction
from .ratelimit import ratelimit

logger = logging.getLogger(__name__)

ALLOWED_UPLOAD_EXTENSIONS = {'.csv'}


@login_required
def statement_list(request):
    from decimal import Decimal

    # Build wallet list (account + currency combos)
    wallets_qs = (
        CurrencyLedger.objects.filter(user=request.user)
        .select_related('statement_import__account')
        .values('statement_import__account__id', 'statement_import__account__nickname',
                'statement_import__account__account_type', 'currency')
        .distinct()
        .order_by('-statement_import__account__account_type', 'statement_import__account__nickname', 'currency')
    )
    wallet_list = []
    for w in wallets_qs:
        acct_id = w['statement_import__account__id']
        currency = w['currency']
        nickname = w['statement_import__account__nickname']
        key = f"{acct_id}:{currency}"
        wallet_list.append({
            'key': key,
            'label': f"{nickname} — {currency}",
            'account_id': acct_id,
            'currency': currency,
            'account_type': w['statement_import__account__account_type'],
        })

    # Handle selection — default to first wallet
    selected_wallet = request.GET.get('wallet', '')
    if not selected_wallet and wallet_list:
        selected_wallet = wallet_list[0]['key']

    selected_account = None
    typed_account = None
    selected_currency = 'CRC'
    ledgers = []

    if selected_wallet:
        parts = selected_wallet.split(':')
        if len(parts) == 2:
            account_id, selected_currency = parts[0], parts[1]
            try:
                selected_account = Account.objects.filter(user=request.user).get(pk=account_id)
            except Account.DoesNotExist:
                selected_account = None

    if selected_account:
        if selected_account.account_type == 'credit_account':
            try:
                typed_account = selected_account.creditaccount
            except CreditAccount.DoesNotExist:
                typed_account = selected_account
        elif selected_account.account_type == 'debit_account':
            try:
                typed_account = selected_account.debitaccount
            except DebitAccount.DoesNotExist:
                typed_account = selected_account
        else:
            typed_account = selected_account

        currency_symbol = '₡' if selected_currency == 'CRC' else '$'
        decimals = 0 if selected_currency == 'CRC' else 2

        qs = CurrencyLedger.objects.filter(user=request.user).filter(
            currency=selected_currency,
            statement_import__account=selected_account,
        ).select_related('statement_import').prefetch_related(
            'raw_transactions__logical_transactions__category__group'
        ).order_by(
            '-statement_import__statement_date'
        )

        for lg in qs:
            raw_count = RawTransaction.objects.filter(user=request.user).filter(ledger=lg).count()
            txns = LogicalTransaction.objects.filter(user=request.user).filter(raw_transaction__ledger=lg).select_related('category__group')
            lg.raw_count = raw_count
            lg.txn_count = txns.count()
            lg.total_spent = sum(t.amount for t in txns if t.category and t.category.group.slug == 'expense')
            lg.total_payments = abs(sum(t.amount for t in txns if t.category and t.category.group.slug == 'transaction'))
            lg.total_income = sum(t.amount for t in txns if t.category and t.category.group.slug == 'income')
            lg.total_expenses = abs(sum(t.amount for t in txns if t.category and t.category.group.slug == 'expense'))
            lg.points = lg.statement_import.points_assigned
            lg.currency_symbol = currency_symbol
            lg.decimals = decimals
            ledgers.append(lg)

    context = {
        'wallets': wallet_list,
        'selected_wallet': selected_wallet,
        'selected_account': selected_account,
        'typed_account': typed_account,
        'selected_currency': selected_currency,
        'ledgers': ledgers,
        'statement_count': selected_account.statements.count() if selected_account else 0,
        'transaction_count': sum(lg.txn_count for lg in ledgers) if ledgers else 0,
    }
    return render(request, 'transactions/statement_list.html', context)


@login_required
def dashboard(request):
    """Overview dashboard — last 12 months, no user filters."""
    from datetime import timedelta
    display_currency = request.GET.get('display_currency', 'CRC')
    time_group = request.GET.get('time_group', 'biweekly')

    today = date.today()
    start_12m = (today.replace(day=1) - timedelta(days=365)).replace(day=1).isoformat()

    context = get_dashboard_stats(request.user, 
        start_date=start_12m,
        display_currency=display_currency,
        time_group=time_group,
    )

    context['display_currency'] = display_currency
    context['time_group'] = time_group
    return render(request, 'transactions/dashboard.html', context)


@login_required
def transaction_health_dashboard(request):
    """Dashboard showing classification health: unclassified %, rule coverage, manual vs auto."""
    import json
    from decimal import Decimal
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
        'colors': ['rgba(13, 110, 253, 0.8)', 'rgba(13, 202, 240, 0.8)', 'rgba(255, 193, 7, 0.8)'],
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
    return render(request, 'transactions/dashboard_transaction_health.html', context)


@login_required
def rule_matching_dashboard(request):
    """Dashboard dedicated to classification rule matching analysis."""
    import json
    from decimal import Decimal
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
        'expense': 'rgba(220,53,69,0.8)', 'income': 'rgba(25,135,84,0.8)',
        'transaction': 'rgba(13,110,253,0.8)', 'unclassified': 'rgba(255,193,7,0.8)',
    }
    group_counts = (
        rules_qs.values('category__group__slug')
        .annotate(c=Count('id'))
        .order_by('category__group__slug')
    )
    rules_by_group = {
        'labels': [GROUP_LABELS.get(r['category__group__slug'], r['category__group__slug']) for r in group_counts],
        'values': [r['c'] for r in group_counts],
        'colors': [GROUP_CHART_COLORS.get(r['category__group__slug'], 'rgba(108,117,125,0.8)') for r in group_counts],
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

    class DE(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, Decimal):
                return float(obj)
            return super().default(obj)

    context = {
        'total_rules': total_rules,
        'active_rules': active_rules,
        'unused_count': unused_count,
        'total_rule_matched': total_rule_matched,
        'avg_matches': avg_matches,
        'rules_by_group_data': json.dumps(rules_by_group, cls=DE),
        'rule_activity_data': json.dumps(rule_activity_data, cls=DE),
        'top_rules': top_rules,
        'unused_rules': unused_rules,
    }
    return render(request, 'transactions/dashboard_rule_matching.html', context)


@login_required
def default_buckets_dashboard(request):
    """Dashboard for transactions in the Default category of each group."""
    import json
    from decimal import Decimal
    from collections import defaultdict
    from django.db.models import Count, Sum, Q
    from django.db.models.functions import TruncMonth, Abs

    user = request.user
    display_currency = request.GET.get('display_currency', 'CRC')
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

    class DE(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, Decimal):
                return float(obj)
            return super().default(obj)

    context = {
        'display_currency': display_currency,
        'currency_symbol': currency_symbol,
        'total_all': total_all,
        'total_default': total_default,
        'default_pct': (total_default / total_all * 100) if total_all else 0,
        'group_stats': group_stats,
        'group_doughnut_data': json.dumps(group_doughnut, cls=DE),
        'monthly_trend_data': json.dumps(monthly_trend, cls=DE),
        'top_descriptions': top_descriptions,
    }
    return render(request, 'transactions/dashboard_default_buckets.html', context)


@login_required
def spending_income_dashboard(request):
    """Spending & Income breakdown dashboard — last 12 months."""
    from datetime import timedelta
    display_currency = request.GET.get('display_currency', 'CRC')

    today = date.today()
    start_12m = (today.replace(day=1) - timedelta(days=365)).replace(day=1).isoformat()

    context = get_dashboard_stats(request.user, 
        start_date=start_12m,
        display_currency=display_currency,
    )

    context['display_currency'] = display_currency
    return render(request, 'transactions/dashboard_spending_income.html', context)


@login_required
def chart_comparison(request):
    """Temporary test page to compare chart types for category expenses over time."""
    from datetime import timedelta
    display_currency = request.GET.get('display_currency', 'CRC')

    today = date.today()
    start_12m = (today.replace(day=1) - timedelta(days=365)).replace(day=1).isoformat()

    context = get_dashboard_stats(request.user, 
        start_date=start_12m,
        display_currency=display_currency,
    )

    context['display_currency'] = display_currency
    return render(request, 'transactions/chart_comparison.html', context)


@login_required
def car_dashboard(request):
    """Dashboard focused on car-related expenses with multiple sections."""
    import json
    from decimal import Decimal
    from datetime import timedelta
    from django.db.models import Sum, Count, Min, Max
    from django.db.models.functions import TruncMonth, Abs

    display_currency = request.GET.get('display_currency', 'CRC')
    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'

    CAR_CATEGORIES = ['Car Gas', 'Car Insurance', 'Car Maintenance', 'Car Parking & Toll', 'Car Tax', 'Car Wash']
    SALARY_CATEGORIES = ['Salary Main', 'Salary Bonuses']

    class DE(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, Decimal):
                return float(obj)
            return super().default(obj)

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
        'display_currency': display_currency,
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
        }, cls=DE),
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
        }, cls=DE),
        'breakdown_data': json.dumps({
            'labels': [r['category__name'] for r in category_totals],
            'values': [float(r['total']) for r in category_totals],
            'colors': [cat_colors.get(r['category__name'], '#6c757d') for r in category_totals],
        }, cls=DE),
        'cost_type_data': json.dumps({
            'labels': ['Running Costs', 'Ownership Costs'],
            'values': [running_last_year, ownership_last_year],
            'colors': ['#18BC9C', '#E74C3C'],
        }, cls=DE),
        'gas_data': json.dumps({
            'labels': sorted_months, 'counts': gas_counts,
            'avg_per_fillup': gas_avg_per, 'spend': gas_monthly_spend,
            'split_above': gas_split_above, 'split_below': gas_split_below,
            'count_above': gas_count_above, 'count_below': gas_count_below,
            'threshold': threshold,
        }, cls=DE),
        'parking_data': json.dumps({
            'labels': sorted_months,
            'spend_datasets': park_spend_datasets,
            'count_datasets': park_count_datasets,
        }, cls=DE),
    }
    return render(request, 'transactions/dashboard_car.html', context)


@login_required
def car_gas_dashboard(request):
    """Dashboard focused on car gas expenses."""
    import json
    from decimal import Decimal
    from django.db.models import Sum, Count
    from django.db.models.functions import TruncMonth, Abs

    display_currency = request.GET.get('display_currency', 'CRC')
    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'
    abs_field = Abs(amount_field)

    class DE(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, Decimal):
                return float(obj)
            return super().default(obj)

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
        'display_currency': display_currency,
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
        }, cls=DE),
    }
    return render(request, 'transactions/dashboard_car_gas.html', context)


@login_required
def car_parking_dashboard(request):
    """Dashboard focused on car parking & tolls."""
    import json
    from decimal import Decimal
    from datetime import date, timedelta
    from django.db.models import Sum, Count, Max
    from django.db.models.functions import TruncMonth, Abs

    display_currency = request.GET.get('display_currency', 'CRC')
    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'
    abs_field = Abs(amount_field)

    class DE(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, Decimal):
                return float(obj)
            return super().default(obj)

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
        'display_currency': display_currency, 'currency_symbol': currency_symbol,
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
        }, cls=DE),
    }
    return render(request, 'transactions/dashboard_car_parking.html', context)


@login_required
def income_salary_dashboard(request):
    """Dashboard focused on Salary Main income."""
    import json
    from decimal import Decimal
    from django.db.models import Sum, Count, Avg
    from django.db.models.functions import TruncMonth

    display_currency = request.GET.get('display_currency', 'CRC')
    amount_field = 'amount_crc' if display_currency == 'CRC' else 'amount_usd'
    currency_symbol = '₡' if display_currency == 'CRC' else '$'

    class DE(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, Decimal):
                return float(obj)
            return super().default(obj)

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
    }, cls=DE)

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
        'display_currency': display_currency,
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
    return render(request, 'transactions/dashboard_income_salary.html', context)


def _detect_card_type(content):
    """Auto-detect whether a CSV is a credit card or debit card statement."""
    first_line = content.split('\n', 1)[0].strip()
    if first_line.startswith('Pro') or '5466-' in content[:500]:
        return 'credit'
    return 'debit'


@login_required
@ratelimit(key='upload', rate='20/h', method='POST')
def upload(request):
    if request.method == 'POST':
        uploaded_files = request.FILES.getlist('files')
        if not uploaded_files:
            uploaded_files = request.FILES.getlist('files[]')
        
        messages.info(request, f'Processing {len(uploaded_files)} file(s)...')
        
        if not uploaded_files:
            messages.error(request, 'Please select at least one file.')
            return render(request, 'transactions/upload.html', {'form': UploadForm()})

        # Validate file extensions
        for f in uploaded_files:
            ext = os.path.splitext(f.name)[1].lower()
            if ext not in ALLOWED_UPLOAD_EXTENSIONS:
                messages.error(request, f'"{f.name}" is not a supported file type. Only CSV files are allowed.')
                return render(request, 'transactions/upload.html', {'form': UploadForm()})

        total_files = 0
        total_txns = 0

        for uploaded_file in uploaded_files:
            try:
                raw = uploaded_file.read()
                import hashlib
                file_hash = hashlib.sha256(raw).hexdigest()

                try:
                    content = raw.decode('utf-8-sig')
                except UnicodeDecodeError:
                    content = raw.decode('latin-1')

                card_type = _detect_card_type(content)
                parser = CreditCardParser() if card_type == 'credit' else DebitCardParser()
                parsed = parser.parse(content)

                # Skip files with no transactions
                txn_count = sum(len(led.transactions) for led in parsed.ledgers)
                if txn_count == 0:
                    messages.info(request, f'"{uploaded_file.name}" has no transactions. Skipped.')
                    continue

                if StatementImport.objects.filter(user=request.user).filter(file_hash=file_hash).exists():
                    messages.warning(request, f'"{uploaded_file.name}" already imported. Skipped.')
                    continue

                if card_type == 'credit':
                    account, _ = CreditAccount.objects.get_or_create(user=request.user, 
                        card_number=parsed.card_number, defaults={'card_holder': parsed.card_holder})
                else:
                    account, _ = DebitAccount.objects.get_or_create(user=request.user, 
                        iban=parsed.card_number, defaults={'card_holder': parsed.card_holder, 'client_number': getattr(parsed, 'client_number', '')})

                stmt_import = StatementImport.objects.create(
                    account=account, user=request.user, filename=uploaded_file.name, file_hash=file_hash,
                    statement_date=parsed.statement_date,
                    points_assigned=parsed.points_assigned, points_redeemable=parsed.points_redeemable)

                file_txn_count = 0
                unclassified = Category.get_unclassified(request.user)
                for pl in parsed.ledgers:
                    ledger = CurrencyLedger.objects.create(
                        statement_import=stmt_import, user=request.user, currency=pl.currency,
                        previous_balance=pl.previous_balance, balance_at_cutoff=pl.balance_at_cutoff)
                    for pt in pl.transactions:
                        raw_txn = RawTransaction.objects.create(
                            date=pt.date, description=pt.description, amount=pt.amount,
                            ledger=ledger, user=request.user, account_metadata=pt.account_metadata)
                        txn = LogicalTransaction.objects.create(
                            raw_transaction=raw_txn, user=request.user, date=pt.date, description=pt.description,
                            amount=pt.amount, category=unclassified)
                        cat, rule_obj = classify_transaction(txn)
                        if rule_obj:
                            txn.category = cat
                            txn.matched_rule = rule_obj
                            txn.classification_method = 'rule'
                            txn.save(update_fields=['category', 'matched_rule', 'classification_method'])
                    file_txn_count += len(pl.transactions)

                all_dates = [t.date for pl in parsed.ledgers for t in pl.transactions]
                if all_dates:
                    try:
                        fetch_rates(min(all_dates), max(all_dates))
                    except Exception:
                        pass

                for ledger in stmt_import.ledgers.all():
                    for raw_obj in ledger.raw_transactions.all():
                        for txn in raw_obj.logical_transactions.all():
                            if convert_transaction(txn):
                                txn.save(update_fields=['amount_crc', 'amount_usd'])

                if stmt_import.statement_date:
                    prev_stmt = StatementImport.objects.filter(user=request.user).filter(
                        account=account, statement_date__lt=stmt_import.statement_date
                    ).order_by('-statement_date').first()
                    if prev_stmt:
                        for ledger in stmt_import.ledgers.all():
                            prev_ledger = CurrencyLedger.objects.filter(
                                statement_import=prev_stmt, currency=ledger.currency).first()
                            if prev_ledger and abs(prev_ledger.balance_at_cutoff - ledger.previous_balance) > 0.01:
                                messages.warning(request,
                                    f'{uploaded_file.name}: Continuity gap ({ledger.currency})')

                for w in parsed.warnings:
                    messages.warning(request, f'{uploaded_file.name}: {w}')

                total_files += 1
                total_txns += file_txn_count
                messages.info(request, f'"{uploaded_file.name}" ({card_type}): {file_txn_count} transactions.')

            except Exception as e:
                logger.exception('Error importing "%s"', uploaded_file.name)
                messages.error(request, f'Error importing "{uploaded_file.name}". The file may be corrupted or in an unsupported format.')

        if total_files:
            messages.success(request, f'Total: {total_txns} transactions from {total_files} file{"s" if total_files > 1 else ""}.')
        return redirect('transactions:statement_list')

    return render(request, 'transactions/upload.html', {'form': UploadForm()})


@login_required
def transaction_list(request):
    from django.db.models import Count, Subquery, OuterRef
    qs = Transaction.objects.filter(user=request.user).select_related(
        'category__group', 'raw_transaction__ledger',
        'raw_transaction__ledger__statement_import',
        'raw_transaction__ledger__statement_import__account'
    ).annotate(
        split_count=Subquery(
            LogicalTransaction.objects.filter(user=request.user).filter(raw_transaction=OuterRef('raw_transaction'))
            .values('raw_transaction').annotate(c=Count('id')).values('c')
        )
    )

    # Apply filters
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    categories = request.GET.getlist('category')
    wallet_ids = request.GET.getlist('wallet')  # account_id:currency combos
    groups = request.GET.getlist('group')
    search = request.GET.get('search', '').strip()
    meta_filters = request.GET.getlist('meta')  # format: "key:value" (from dropdown filters)
    cls_methods = request.GET.getlist('cls_method')  # classification method filter
    # Advanced metadata search (key-value pairs)
    adv_meta_keys = request.GET.getlist('meta_key')
    adv_meta_values = request.GET.getlist('meta_value')
    advanced_meta_filters = []
    for k, v in zip(adv_meta_keys, adv_meta_values):
        k, v = k.strip(), v.strip()
        if k and v:
            advanced_meta_filters.append({'key': k, 'value': v})
    # Rule ID filters
    rule_ids = [r for r in request.GET.getlist('rule') if r.strip()]
    statement_ids = [s for s in request.GET.getlist('statement') if s.strip()]
    split_filter = request.GET.get('split', '').strip()  # 'yes', 'no', or ''
    amount_min = request.GET.get('amount_min', '').strip()
    amount_max = request.GET.get('amount_max', '').strip()
    transaction_code = request.GET.get('transaction_code', '').strip()
    reference_number = request.GET.get('reference_number', '').strip()

    if start_date:
        qs = qs.filter(date__gte=start_date)
    if end_date:
        qs = qs.filter(date__lte=end_date)
    if categories:
        qs = qs.filter(category_id__in=categories)
    if groups:
        qs = qs.filter(category__group__slug__in=groups)
    if wallet_ids:
        from django.db.models import Q
        wallet_q = Q()
        for w in wallet_ids:
            parts = w.split(':')
            if len(parts) == 2:
                wallet_q |= Q(raw_transaction__ledger__statement_import__account_id=parts[0], raw_transaction__ledger__currency=parts[1])
        if wallet_q:
            qs = qs.filter(wallet_q)
    if search:
        qs = qs.filter(description__icontains=search)
    if meta_filters:
        from django.db.models import Q
        # Group by key, OR values within same key
        meta_by_key = {}
        for mf in meta_filters:
            if ':' in mf:
                mk, mv = mf.split(':', 1)
                meta_by_key.setdefault(mk, []).append(mv)
        for mk, mvs in meta_by_key.items():
            q = Q()
            for mv in mvs:
                q |= Q(**{f'raw_transaction__account_metadata__{mk}': mv})
            qs = qs.filter(q)
    if cls_methods:
        qs = qs.filter(classification_method__in=cls_methods)
    for amf in advanced_meta_filters:
        qs = qs.filter(**{f'raw_transaction__account_metadata__{amf["key"]}': amf['value']})
    if rule_ids:
        qs = qs.filter(matched_rule_id__in=rule_ids)
    if statement_ids:
        qs = qs.filter(raw_transaction__ledger__statement_import_id__in=statement_ids)
    if split_filter == 'yes':
        qs = qs.filter(split_count__gt=1)
    elif split_filter == 'no':
        qs = qs.filter(split_count=1)
    if amount_min:
        try:
            qs = qs.filter(amount__gte=amount_min)
        except (ValueError, Exception):
            pass
    if amount_max:
        try:
            qs = qs.filter(amount__lte=amount_max)
        except (ValueError, Exception):
            pass
    if transaction_code:
        qs = qs.filter(raw_transaction__account_metadata__transaction_code=transaction_code)
    if reference_number:
        qs = qs.filter(raw_transaction__account_metadata__reference_number=reference_number)

    # Sorting
    SORT_FIELDS = {
        'date': 'date',
        'account': 'raw_transaction__ledger__statement_import__account__nickname',
        'method': 'classification_method',
        'group': 'category__group__name',
        'category': 'category__name',
        'description': 'description',
        'amount': 'amount',
    }
    sort_col = request.GET.get('sort', 'date')
    sort_dir = request.GET.get('dir', 'desc')
    if sort_col not in SORT_FIELDS:
        sort_col = 'date'
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'desc'
    order_field = SORT_FIELDS[sort_col]
    if sort_dir == 'desc':
        order_field = '-' + order_field
    qs = qs.order_by(order_field, '-id')

    paginator = Paginator(qs, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    from .models import CategoryGroup, CurrencyLedger
    category_groups = CategoryGroup.objects.prefetch_related(Prefetch('categories', queryset=Category.objects.filter(user=request.user))).all()

    # Build virtual wallet list (account + currency combos)
    wallets = (
        CurrencyLedger.objects
        .select_related('statement_import__account')
        .values('statement_import__account__id', 'statement_import__account__nickname', 'currency')
        .distinct()
        .order_by('-statement_import__account__account_type', 'statement_import__account__nickname', 'currency')
    )
    wallet_list = []
    for w in wallets:
        acct_id = w['statement_import__account__id']
        currency = w['currency']
        nickname = w['statement_import__account__nickname']
        key = f"{acct_id}:{currency}"
        wallet_list.append({'key': key, 'label': f"{nickname} — {currency}"})

    # Build query strings for links
    query_params = request.GET.copy()
    query_params.pop('page', None)
    # pagination_qs: includes sort/dir for pagination links
    pagination_qs = query_params.urlencode()
    # filter_qs: excludes sort/dir for sort header links
    query_params.pop('sort', None)
    query_params.pop('dir', None)
    filter_qs = query_params.urlencode()

    # Build metadata filter options (keys with ≤30 distinct values)
    from collections import Counter
    meta_key_values = {}
    for meta in RawTransaction.objects.filter(user=request.user).exclude(account_metadata={}).values_list('account_metadata', flat=True):
        for k, v in meta.items():
            meta_key_values.setdefault(k, set()).add(str(v))
    metadata_filters = []
    for k, vals in sorted(meta_key_values.items()):
        if len(vals) <= 30:
            metadata_filters.append({'key': k, 'values': sorted(vals)})

    # Build metadata key list for advanced search
    meta_keys = sorted(meta_key_values.keys())

    context = {
        'page_obj': page_obj,
        'category_groups': category_groups,
        'wallets': wallet_list,
        'total_count': paginator.count,
        'meta_keys': meta_keys,
        'start_date': start_date or '',
        'end_date': end_date or '',
        'selected_categories': categories,
        'selected_wallets': wallet_ids,
        'selected_groups': groups,
        'selected_meta': meta_filters,
        'selected_cls_methods': cls_methods,
        'advanced_meta_filters': advanced_meta_filters,
        'rule_ids': rule_ids,
        'statement_ids': statement_ids,
        'split_filter': split_filter,
        'amount_min': amount_min,
        'amount_max': amount_max,
        'transaction_code': transaction_code,
        'reference_number': reference_number,
        'adv_open': request.GET.get('adv_open', ''),
        'metadata_filters': metadata_filters,
        'search': search,
        'pagination_qs': pagination_qs,
        'filter_qs': filter_qs,
        'sort_col': sort_col,
        'sort_dir': sort_dir,
        'saved_columns_json': json.dumps(UserPreference.objects.filter(user=request.user).values_list('transaction_columns', flat=True).first() or {}),
    }
    return render(request, 'transactions/transaction_list.html', context)


@login_required
@require_POST
def save_transaction_columns(request):
    try:
        columns = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)
    pref, _ = UserPreference.objects.get_or_create(user=request.user)
    pref.transaction_columns = columns
    pref.save(update_fields=['transaction_columns'])
    return JsonResponse({'status': 'ok'})


@login_required
@require_POST
def bulk_update_category(request):
    """Bulk assign a category to multiple transactions (same logic as manual single update)."""
    txn_ids = request.POST.getlist('txn_ids')
    category_id = request.POST.get('category_id')
    next_url = request.POST.get('next', '')

    if not txn_ids or not category_id:
        messages.error(request, 'No transactions or category selected.')
        return redirect(next_url or 'transactions:transaction_list')

    cat = get_object_or_404(Category.objects.filter(user=request.user).select_related('group'), pk=category_id)
    is_unclassified = cat.group.slug == 'unclassified' and cat.name == 'Default'
    method = 'unclassified' if is_unclassified else 'manual'

    updated = Transaction.objects.filter(user=request.user).filter(pk__in=txn_ids).update(
        category=cat,
        classification_method=method,
        matched_rule=None,
    )
    messages.success(request, f'{updated} transaction{"s" if updated != 1 else ""} updated to {cat.name}.')
    return redirect(next_url or 'transactions:transaction_list')


@login_required
def edit_transaction(request, raw_id):
    """Edit a transaction: change description/category, or split into multiple."""
    from decimal import Decimal, InvalidOperation
    raw = get_object_or_404(RawTransaction, pk=raw_id, user=request.user)
    logical_txns = list(raw.logical_transactions.select_related('category__group').order_by('pk'))
    category_groups = CategoryGroup.objects.prefetch_related(Prefetch('categories', queryset=Category.objects.filter(user=request.user))).all()
    is_split = len(logical_txns) > 1
    next_url = request.GET.get('next', request.POST.get('next', ''))

    if request.method == 'POST':
        action = request.POST.get('action', 'save')

        # Unsplit action
        if action == 'unsplit':
            if len(logical_txns) > 1:
                first = logical_txns[0]
                for lt in logical_txns[1:]:
                    lt.delete()
                unclassified = Category.get_unclassified(request.user)
                first.date = raw.date
                first.description = raw.description
                first.amount = raw.amount
                first.category = unclassified
                first.classification_method = 'unclassified'
                first.matched_rule = None
                first.save(update_fields=['date', 'description', 'amount', 'category', 'classification_method', 'matched_rule'])
                from .services.exchange_rates import convert_transaction
                convert_transaction(first)
                first.save(update_fields=['amount_crc', 'amount_usd'])
                messages.success(request, 'Transaction unsplit and reset to unclassified.')
            edit_url = reverse('transactions:edit_transaction', args=[raw_id])
            if next_url:
                edit_url += '?' + urlencode({'next': next_url})
            return redirect(edit_url)

        # Save action (single edit or split save)
        descriptions = request.POST.getlist('split_description')
        amounts = request.POST.getlist('split_amount')
        category_ids = request.POST.getlist('split_category')

        try:
            parsed = [(d.strip(), Decimal(a.strip()), int(c)) for d, a, c in zip(descriptions, amounts, category_ids) if a.strip()]
        except (ValueError, InvalidOperation):
            messages.error(request, 'Invalid amount values.')
            return redirect('transactions:edit_transaction', raw_id=raw_id)

        if not parsed:
            messages.error(request, 'At least one entry is required.')
            return redirect('transactions:edit_transaction', raw_id=raw_id)

        total = sum(a for _, a, _ in parsed)
        if total != raw.amount:
            messages.error(request, f'Amounts ({total}) must equal the original amount ({raw.amount}).')
            return redirect('transactions:edit_transaction', raw_id=raw_id)

        first_logical = logical_txns[0] if logical_txns else None
        if len(logical_txns) > 1:
            for lt in logical_txns[1:]:
                lt.delete()

        from .services.exchange_rates import convert_transaction

        for i, (desc, amt, cat_id) in enumerate(parsed):
            cat = Category.objects.filter(user=request.user).get(pk=cat_id)
            if i == 0 and first_logical:
                first_logical.description = desc
                first_logical.amount = amt
                first_logical.category = cat
                first_logical.classification_method = 'manual'
                first_logical.matched_rule = None
                first_logical.save(update_fields=['description', 'amount', 'category', 'classification_method', 'matched_rule'])
                convert_transaction(first_logical)
                first_logical.save(update_fields=['amount_crc', 'amount_usd'])
            else:
                txn = LogicalTransaction.objects.create(
                    raw_transaction=raw,
                    user=request.user,
                    date=raw.date,
                    description=desc,
                    amount=amt,
                    category=cat,
                    classification_method='manual',
                )
                convert_transaction(txn)
                txn.save(update_fields=['amount_crc', 'amount_usd'])

        if len(parsed) > 1:
            messages.success(request, f'Transaction saved with {len(parsed)} splits.')
        else:
            messages.success(request, 'Transaction updated.')
        return redirect(next_url or 'transactions:transaction_list')

    return render(request, 'transactions/edit_transaction.html', {
        'raw': raw,
        'logical_txns': logical_txns,
        'category_groups': category_groups,
        'is_split': is_split,
        'next_url': next_url,
    })


@login_required
def split_transaction(request, raw_id):
    """Split a raw transaction into multiple logical transactions."""
    return redirect('transactions:edit_transaction', raw_id=raw_id)


@login_required
@require_POST
def unsplit_transaction(request, raw_id):
    """Merge all logical transactions back to a single 1:1 with the raw."""
    raw = get_object_or_404(RawTransaction, pk=raw_id, user=request.user)
    logical_txns = list(raw.logical_transactions.order_by('pk'))

    if len(logical_txns) <= 1:
        messages.info(request, 'Transaction is not split.')
        return redirect('transactions:transaction_list')

    # Keep the first, delete the rest
    first = logical_txns[0]
    for lt in logical_txns[1:]:
        lt.delete()

    # Restore first to match raw
    unclassified = Category.get_unclassified(request.user)
    first.date = raw.date
    first.description = raw.description
    first.amount = raw.amount
    first.category = unclassified
    first.classification_method = 'unclassified'
    first.matched_rule = None
    first.save(update_fields=['date', 'description', 'amount', 'category', 'classification_method', 'matched_rule'])

    from .services.exchange_rates import convert_transaction
    convert_transaction(first)
    first.save(update_fields=['amount_crc', 'amount_usd'])

    messages.success(request, 'Transaction unsplit and reset to unclassified.')
    return redirect('transactions:transaction_list')


@login_required
def category_list(request):
    """List all categories grouped by group, with rule and transaction counts."""
    from django.db.models import Count

    category_groups = CategoryGroup.objects.exclude(slug='unclassified').order_by('name')
    groups = {}
    for grp in category_groups:
        cats = []
        for cat in Category.objects.filter(user=request.user, group=grp).order_by('name'):
            txn_count = Transaction.objects.filter(user=request.user, category=cat).count()
            rule_count = ClassificationRule.objects.filter(user=request.user, category=cat).count()
            cats.append({
                'id': cat.pk,
                'name': cat.name,
                'color': cat.color,
                'rule_count': rule_count,
                'txn_count': txn_count,
            })
        groups[grp.slug] = {'name': grp.name, 'categories': cats}

    # Add unclassified group last
    unclassified_grp = CategoryGroup.objects.filter(slug='unclassified').first()
    if unclassified_grp:
        cats = []
        for cat in Category.objects.filter(user=request.user, group=unclassified_grp).order_by('name'):
            txn_count = Transaction.objects.filter(user=request.user, category=cat).count()
            cats.append({
                'id': cat.pk,
                'name': cat.name,
                'color': cat.color,
                'rule_count': 0,
                'txn_count': txn_count,
            })
        groups['unclassified'] = {'name': unclassified_grp.name, 'categories': cats}

    return render(request, 'transactions/category_list.html', {'groups': groups})


@login_required
def export_categories(request):
    """Export user's categories and rules as a YAML file download."""
    import yaml
    data = {'groups': {}}
    for grp in CategoryGroup.objects.order_by('name'):
        cats = {}
        for cat in Category.objects.filter(user=request.user, group=grp).order_by('name'):
            rules = []
            for rule in ClassificationRule.objects.filter(user=request.user, category=cat).order_by('description'):
                r = {}
                if rule.description:
                    r['description'] = rule.description
                if rule.account_type:
                    r['account_type'] = rule.account_type
                if rule.amount_min is not None:
                    r['amount_min'] = float(rule.amount_min)
                if rule.amount_max is not None:
                    r['amount_max'] = float(rule.amount_max)
                for k, v in rule.metadata.items():
                    r[f'metadata.{k}'] = v
                if rule.detail:
                    r['detail'] = rule.detail
                rules.append(r)
            cat_data = {'color': cat.color}
            if rules:
                cat_data['rules'] = rules
            cats[cat.name] = cat_data
        if cats:
            data['groups'][grp.slug] = {'name': grp.name, 'categories': cats}

    content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    from django.http import HttpResponse
    response = HttpResponse(content, content_type='application/x-yaml')
    response['Content-Disposition'] = 'attachment; filename="categories_and_rules.yaml"'
    return response


@login_required
@require_POST
def import_categories(request):
    """Import categories and rules from an uploaded YAML file."""
    import yaml
    uploaded = request.FILES.get('file')
    if not uploaded:
        messages.error(request, 'No file selected.')
        return redirect('transactions:category_list')

    try:
        data = yaml.safe_load(uploaded.read().decode('utf-8'))
    except Exception:
        messages.error(request, 'Invalid YAML file.')
        return redirect('transactions:category_list')

    if not data or 'groups' not in data:
        messages.error(request, 'YAML must have a "groups" key.')
        return redirect('transactions:category_list')

    cats_created = 0
    cats_skipped = 0
    rules_created = 0
    rules_skipped = 0

    for grp_slug, grp_info in data['groups'].items():
        grp = CategoryGroup.objects.filter(slug=grp_slug).first()
        if not grp:
            continue

        categories = grp_info.get('categories', {})
        # Support both list format (names only) and dict format (with rules)
        if isinstance(categories, list):
            categories = {name: {} for name in categories}
        if not isinstance(categories, dict):
            continue

        for cat_name, cat_info in categories.items():
            color = cat_info.get('color', '#6c757d') if isinstance(cat_info, dict) else '#6c757d'
            cat, was_created = Category.objects.get_or_create(
                name=cat_name, group=grp, user=request.user,
                defaults={'color': color},
            )
            if was_created:
                cats_created += 1
            else:
                cats_skipped += 1

            if not isinstance(cat_info, dict):
                continue
            for rule_data in cat_info.get('rules', []):
                # Build rule fields
                desc = rule_data.get('description', '')
                acct = rule_data.get('account_type', '')
                amt_min = rule_data.get('amount_min')
                amt_max = rule_data.get('amount_max')
                detail = rule_data.get('detail', '')
                metadata = {}
                for k, v in rule_data.items():
                    if k.startswith('metadata.'):
                        metadata[k[9:]] = v

                # Check for duplicate
                exists = ClassificationRule.objects.filter(
                    user=request.user, category=cat,
                    description=desc, account_type=acct,
                    amount_min=amt_min, amount_max=amt_max,
                    metadata=metadata,
                ).exists()
                if exists:
                    rules_skipped += 1
                    continue

                ClassificationRule.objects.create(
                    user=request.user, category=cat,
                    description=desc, account_type=acct,
                    amount_min=amt_min, amount_max=amt_max,
                    metadata=metadata, detail=detail,
                )
                rules_created += 1

    parts = []
    if cats_created:
        parts.append(f'{cats_created} categories created')
    if rules_created:
        parts.append(f'{rules_created} rules imported')
    if cats_skipped or rules_skipped:
        skip_parts = []
        if cats_skipped:
            skip_parts.append(f'{cats_skipped} categories')
        if rules_skipped:
            skip_parts.append(f'{rules_skipped} rules')
        parts.append(f'{" and ".join(skip_parts)} already existed')

    messages.success(request, '. '.join(parts) + '.' if parts else 'Nothing to import.')
    return redirect('transactions:category_list')


# ────────────────────────────────────────────────────────────
# Rules management (DB-backed with YAML sync)
# ────────────────────────────────────────────────────────────

from .models import ClassificationRule
from .services.yaml_classifier import load_yaml, save_yaml, reload_rules as _reload_yaml


def _sync_rules_to_yaml(user):
    """Export current DB rules to the YAML file (dual-write)."""
    data = load_yaml()
    groups = data.get('groups', {})
    # Clear all existing rules in YAML
    for grp_info in groups.values():
        for cat_info in grp_info.get('categories', {}).values():
            cat_info['rules'] = []
    # Write DB rules into YAML structure
    for rule in ClassificationRule.objects.filter(user=user).select_related('category__group').all():
        grp_slug = rule.category.group.slug
        cat_name = rule.category.name
        cat_info = groups.get(grp_slug, {}).get('categories', {}).get(cat_name)
        if cat_info is None:
            continue
        r = {}
        if rule.description:
            r['description'] = rule.description
        if rule.account_type:
            r['account_type'] = rule.account_type
        if rule.amount_min is not None:
            r['amount_min'] = float(rule.amount_min)
        if rule.amount_max is not None:
            r['amount_max'] = float(rule.amount_max)
        for k, v in rule.metadata.items():
            r[f'metadata.{k}'] = v
        if rule.detail:
            r['detail'] = rule.detail
        cat_info['rules'].append(r)
    save_yaml(data)


@login_required
def yaml_rule_list(request):
    """List, filter, and search classification rules."""
    # Build group→categories from DB
    group_categories = {}
    for grp in CategoryGroup.objects.prefetch_related(Prefetch('categories', queryset=Category.objects.filter(user=request.user))).exclude(slug='unclassified').order_by('name'):
        cats = sorted(
            [c.name for c in grp.categories.all()],
            key=lambda x: (x == 'Default', x)
        )
        if cats:
            group_categories[grp.slug] = cats

    filter_group = request.GET.get('group', '')
    filter_category = request.GET.get('category', '')
    search_q = request.GET.get('q', '').strip()

    if not filter_group and not filter_category and not search_q and group_categories:
        first_group = next(iter(group_categories))
        first_category = group_categories[first_group][0]
        filter_group = first_group
        filter_category = first_category

    rules = []
    if filter_group and filter_category:
        from django.db.models import Count
        db_rules = ClassificationRule.objects.filter(user=request.user).filter(
            category__name=filter_category,
            category__group__slug=filter_group,
        ).select_related('category__group').annotate(
            txn_count=Count('matched_transactions')
        )

        for rule in db_rules:
            meta_parts = [f'{k}={v}' for k, v in rule.metadata.items()]
            rules.append({
                'pk': rule.pk,
                'description': rule.description,
                'account_type': rule.account_type,
                'amount_min': float(rule.amount_min) if rule.amount_min is not None else None,
                'amount_max': float(rule.amount_max) if rule.amount_max is not None else None,
                'metadata_display': ', '.join(meta_parts),
                'detail': rule.detail,
                'group': filter_group,
                'category': filter_category,
                'txn_count': rule.txn_count,
            })

    rules.sort(key=lambda r: (
        r.get('description', '').upper(),
        r.get('account_type', ''),
        r.get('metadata_display', ''),
        float(r.get('amount_min', 0) or 0),
    ))

    if search_q:
        q_upper = search_q.upper()
        rules = [r for r in rules if any(
            q_upper in str(v).upper() for k, v in r.items()
            if k not in ('pk', 'metadata_display', 'txn_count')
        )]

    total_rules = ClassificationRule.objects.filter(user=request.user).count()

    from django.http import QueryDict
    qs = QueryDict(mutable=True)
    if filter_group:
        qs['group'] = filter_group
    if filter_category:
        qs['category'] = filter_category
    if search_q:
        qs['q'] = search_q
    effective_path = request.path + ('?' + qs.urlencode() if qs else '')

    cat_txn_count = 0
    cat_rule_count = len(rules)
    if filter_group and filter_category:
        cat_txn_count = Transaction.objects.filter(user=request.user).filter(
            category__name=filter_category,
            category__group__slug=filter_group,
        ).count()
        cat_rule_count = ClassificationRule.objects.filter(user=request.user).filter(
            category__name=filter_category,
            category__group__slug=filter_group,
        ).count()

    return render(request, 'transactions/yaml_rule_list.html', {
        'rules': rules,
        'total_rules': total_rules,
        'filtered_count': len(rules),
        'group_categories': group_categories,
        'filter_group': filter_group,
        'filter_category': filter_category,
        'cat_txn_count': cat_txn_count,
        'cat_rule_count': cat_rule_count,
        'search_q': search_q,
        'effective_path': effective_path,
        'form': YamlRuleForm(),
    })


@login_required
def yaml_rule_add(request):
    """Add a new classification rule."""
    if request.method == 'POST':
        form = YamlRuleForm(request.POST)
        if form.is_valid():
            rule_dict = form.to_rule_dict()
            group_slug = rule_dict.pop('group')
            cat_name = rule_dict.pop('category')
            cat = Category.objects.filter(user=request.user).filter(name=cat_name, group__slug=group_slug).first()
            if cat:
                metadata = {}
                for k in list(rule_dict.keys()):
                    if k.startswith('metadata.'):
                        metadata[k[9:]] = rule_dict.pop(k)
                rule_dict.pop('detail', '')
                ClassificationRule.objects.create(
                    category=cat,
                    user=request.user,
                    description=rule_dict.get('description', ''),
                    account_type=rule_dict.get('account_type', ''),
                    amount_min=rule_dict.get('amount_min'),
                    amount_max=rule_dict.get('amount_max'),
                    metadata=metadata,
                    detail=form.cleaned_data.get('detail', ''),
                )
                _sync_rules_to_yaml(request.user)
                _reload_yaml()
                desc = rule_dict.get('description', '?')
                messages.success(request, f'Rule "{desc}" → {cat_name} added.')
                from django.utils.http import urlencode
                return redirect(f"{reverse('transactions:yaml_rule_list')}?{urlencode({'group': group_slug, 'category': cat_name})}")
    else:
        initial = {}
        grp = request.GET.get('group', '')
        cat = request.GET.get('category', '')
        if grp:
            initial['group'] = grp
        if grp and cat:
            initial['category'] = f'{grp}:{cat}'
        form = YamlRuleForm(initial=initial)
    return render(request, 'transactions/yaml_rule_form.html', {
        'form': form,
        'title': 'Add Rule',
    })


@login_required
def yaml_rule_edit(request, idx):
    """Edit a classification rule by pk."""
    rule_obj = get_object_or_404(ClassificationRule.objects.filter(user=request.user).select_related('category__group'), pk=idx)
    next_url = request.GET.get('next', request.POST.get('next', ''))
    filter_group = rule_obj.category.group.slug
    filter_category = rule_obj.category.name

    if request.method == 'POST':
        form = YamlRuleForm(request.POST)
        if form.is_valid():
            rule_dict = form.to_rule_dict()
            new_group = rule_dict.pop('group')
            new_cat_name = rule_dict.pop('category')
            new_cat = Category.objects.filter(user=request.user).filter(name=new_cat_name, group__slug=new_group).first()
            if new_cat:
                old_cat = rule_obj.category
                metadata = {}
                for k in list(rule_dict.keys()):
                    if k.startswith('metadata.'):
                        metadata[k[9:]] = rule_dict.pop(k)
                # Detect if matching parameters changed
                new_description = rule_dict.get('description', '')
                new_account_type = rule_dict.get('account_type', '')
                new_amount_min = rule_dict.get('amount_min')
                new_amount_max = rule_dict.get('amount_max')
                conditions_changed = (
                    rule_obj.description != new_description
                    or rule_obj.account_type != new_account_type
                    or rule_obj.metadata != metadata
                    or rule_obj.amount_min != new_amount_min
                    or rule_obj.amount_max != new_amount_max
                )

                rule_obj.category = new_cat
                rule_obj.description = new_description
                rule_obj.account_type = new_account_type
                rule_obj.amount_min = new_amount_min
                rule_obj.amount_max = new_amount_max
                rule_obj.metadata = metadata
                rule_obj.detail = form.cleaned_data.get('detail', '')
                rule_obj.save()

                if conditions_changed:
                    # Reset linked transactions when matching parameters change
                    unclassified = Category.get_unclassified(request.user)
                    reset_count = Transaction.objects.filter(user=request.user).filter(
                        matched_rule=rule_obj, classification_method='rule'
                    ).update(
                        category=unclassified,
                        matched_rule=None,
                        classification_method='unclassified',
                    )
                    if reset_count:
                        messages.info(request, f'{reset_count} transaction{"s" if reset_count > 1 else ""} unlinked and set to unclassified.')
                elif new_cat != old_cat:
                    # Only move transactions if target category changed (conditions stayed the same)
                    updated = Transaction.objects.filter(user=request.user).filter(
                        matched_rule=rule_obj, classification_method='rule'
                    ).update(category=new_cat)
                    if updated:
                        messages.info(request, f'{updated} transaction{"s" if updated > 1 else ""} moved to {new_cat.name}.')
                _sync_rules_to_yaml(request.user)
                _reload_yaml()
                messages.success(request, 'Rule updated.')
                return redirect(next_url or 'transactions:yaml_rule_list')
    else:
        meta_key = ''
        meta_val = ''
        for k, v in rule_obj.metadata.items():
            meta_key = k
            meta_val = str(v)
            break
        initial = {
            'description': rule_obj.description,
            'metadata_key': meta_key,
            'metadata_value': meta_val,
            'account_type': rule_obj.account_type,
            'amount_min': rule_obj.amount_min,
            'amount_max': rule_obj.amount_max,
            'group': filter_group,
            'category': f'{filter_group}:{filter_category}',
            'detail': rule_obj.detail,
        }
        form = YamlRuleForm(initial=initial)

    return render(request, 'transactions/yaml_rule_form.html', {
        'form': form,
        'title': 'Edit Rule',
        'idx': idx,
        'next_url': next_url,
        'filter_group': filter_group,
        'filter_category': filter_category,
    })


@login_required
@require_POST
def yaml_rule_delete(request, idx):
    """Delete a classification rule by pk. Resets affected transactions."""
    next_url = request.POST.get('next', '')
    rule = get_object_or_404(ClassificationRule, pk=idx, user=request.user)
    # Reset transactions that were classified by this rule
    unclassified = Category.get_unclassified(request.user)
    Transaction.objects.filter(user=request.user).filter(matched_rule=rule).update(
        category=unclassified, matched_rule=None, classification_method='unclassified'
    )
    rule.delete()
    _sync_rules_to_yaml(request.user)
    _reload_yaml()
    messages.success(request, 'Rule deleted.')
    return redirect(next_url or 'transactions:yaml_rule_list')


@login_required
@require_POST
def reclassify_all(request):
    """Reset non-manual transactions to Unclassified, then re-apply all rules."""
    unclassified = Category.get_unclassified(request.user)
    # Only reset rule-classified and unclassified transactions (skip manual)
    non_manual = Transaction.objects.filter(user=request.user).exclude(classification_method='manual')
    total = non_manual.update(
        category=unclassified, matched_rule=None, classification_method='unclassified'
    )

    from .services.yaml_classifier import classify_transactions_yaml
    classified = classify_transactions_yaml(
        Transaction.objects.filter(user=request.user).filter(classification_method='unclassified').select_related(
            'category', 'raw_transaction__ledger__statement_import__account'
        )
    )
    manual_count = Transaction.objects.filter(user=request.user).filter(classification_method='manual').count()
    remaining = total - classified
    messages.success(request, f'Reclassified {classified} transactions. {remaining} unclassified, {manual_count} manual (untouched).')
    return redirect('transactions:yaml_rule_list')


@login_required
@require_POST
def classify_unclassified(request):
    """Apply rules only to unclassified transactions."""
    from .services.yaml_classifier import classify_transactions_yaml
    classified = classify_transactions_yaml(
        Transaction.objects.filter(user=request.user).filter(classification_method='unclassified').select_related(
            'category', 'raw_transaction__ledger__statement_import__account'
        )
    )
    remaining = Transaction.objects.filter(user=request.user).filter(classification_method='unclassified').count()
    messages.success(request, f'Classified {classified} transactions. {remaining} remain unclassified.')
    return redirect('transactions:yaml_rule_list')


@login_required
@require_POST
def yaml_category_add(request):
    """Add a new category to YAML and DB."""
    group_slug = request.POST.get('group', '')
    cat_name = request.POST.get('category', '').strip()
    if not group_slug or not cat_name:
        messages.error(request, 'Group and category name are required.')
        return redirect('transactions:category_list')

    grp = CategoryGroup.objects.filter(slug=group_slug).first()
    if not grp:
        messages.error(request, f'Group "{group_slug}" not found.')
        return redirect('transactions:category_list')

    if Category.objects.filter(user=request.user).filter(name=cat_name, group=grp).exists():
        messages.error(request, f'Category "{cat_name}" already exists.')
        return redirect('transactions:category_list')

    Category.objects.create(name=cat_name, group=grp, color='#6c757d', user=request.user)

    # Sync to YAML
    data = load_yaml()
    cats = data.get('groups', {}).get(group_slug, {}).get('categories', {})
    cats[cat_name] = {'color': '#6c757d', 'rules': []}
    save_yaml(data)

    messages.success(request, f'Category "{cat_name}" created.')
    return redirect('transactions:category_list')


@login_required
@require_POST
def yaml_category_delete(request):
    """Delete a category from DB and YAML."""
    group_slug = request.POST.get('group', '')
    cat_name = request.POST.get('category', '').strip()

    PROTECTED = {'Default'}
    if cat_name in PROTECTED:
        messages.error(request, f'The "{cat_name}" category is protected and cannot be deleted.')
        return redirect('transactions:category_list')

    if not group_slug or not cat_name:
        messages.error(request, 'Group and category name are required.')
        return redirect('transactions:category_list')

    cat = Category.objects.filter(user=request.user).filter(name=cat_name, group__slug=group_slug).first()
    if not cat:
        messages.error(request, f'Category "{cat_name}" not found.')
        return redirect('transactions:category_list')

    # Move transactions to Unclassified
    unclassified = Category.get_unclassified(request.user)
    Transaction.objects.filter(user=request.user).filter(category=cat).update(category=unclassified)
    cat.delete()  # Cascades to ClassificationRule

    # Sync to YAML
    data = load_yaml()
    cats = data.get('groups', {}).get(group_slug, {}).get('categories', {})
    cats.pop(cat_name, None)
    save_yaml(data)

    messages.success(request, f'Category "{cat_name}" deleted.')
    return redirect('transactions:category_list')


@login_required
@require_POST
def yaml_category_rename(request):
    """Rename a category in DB and YAML."""
    group_slug = request.POST.get('group', '')
    old_name = request.POST.get('old_name', '').strip()
    new_name = request.POST.get('new_name', '').strip()

    PROTECTED = {'Default'}
    if old_name in PROTECTED:
        messages.error(request, f'The "{old_name}" category is protected and cannot be renamed.')
        return redirect('transactions:category_list')

    if not group_slug or not old_name or not new_name:
        messages.error(request, 'Group, old name, and new name are required.')
        return redirect('transactions:category_list')

    if old_name == new_name:
        return redirect('transactions:category_list')

    cat = Category.objects.filter(user=request.user).filter(name=old_name, group__slug=group_slug).first()
    if not cat:
        messages.error(request, f'Category "{old_name}" not found.')
        return redirect('transactions:category_list')

    if Category.objects.filter(user=request.user).filter(name=new_name, group__slug=group_slug).exists():
        messages.error(request, f'Category "{new_name}" already exists.')
        return redirect('transactions:category_list')

    cat.name = new_name
    cat.save(update_fields=['name'])

    # Sync to YAML
    data = load_yaml()
    cats = data.get('groups', {}).get(group_slug, {}).get('categories', {})
    if old_name in cats:
        new_cats = {}
        for k, v in cats.items():
            new_cats[new_name if k == old_name else k] = v
        data['groups'][group_slug]['categories'] = new_cats
        save_yaml(data)

    messages.success(request, f'Category renamed: "{old_name}" → "{new_name}".')
    return redirect('transactions:category_list')
