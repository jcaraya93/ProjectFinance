import json
import logging

from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST
from django.utils.http import urlencode

from ..models import (
    Transaction, LogicalTransaction, RawTransaction, Category,
    CategoryGroup, CurrencyLedger, ClassificationRule, UserPreference,
)
from ..ratelimit import ratelimit
from ._helpers import _safe_next_url, get_category_groups

logger = logging.getLogger(__name__)

__all__ = [
    'transaction_list',
    'save_transaction_columns',
    'bulk_update_category',
    'edit_transaction',
    'split_transaction',
    'unsplit_transaction',
]


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

    from ..models import CurrencyLedger
    category_groups = get_category_groups(request.user)

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
    return render(request, 'core/transaction_list.html', context)


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
@ratelimit(key='bulk_category', rate='30/m', method='POST')
def bulk_update_category(request):
    """Bulk assign a category to multiple transactions (same logic as manual single update)."""
    txn_ids = request.POST.getlist('txn_ids')
    category_id = request.POST.get('category_id')
    next_url = _safe_next_url(request)

    if not txn_ids or not category_id:
        messages.error(request, 'No transactions or category selected.')
        return redirect(next_url or 'core:transaction_list')

    cat = get_object_or_404(Category.objects.filter(user=request.user).select_related('group'), pk=category_id)
    is_unclassified = cat.group.slug == 'unclassified' and cat.name == 'Default'
    method = 'unclassified' if is_unclassified else 'manual'

    updated = Transaction.objects.filter(user=request.user).filter(pk__in=txn_ids).update(
        category=cat,
        classification_method=method,
        matched_rule=None,
    )
    messages.success(request, f'{updated} transaction{"s" if updated != 1 else ""} updated to {cat.name}.')
    return redirect(next_url or 'core:transaction_list')


@login_required
def edit_transaction(request, raw_id):
    """Edit a transaction: change description/category, or split into multiple."""
    from decimal import Decimal, InvalidOperation
    raw = get_object_or_404(RawTransaction, pk=raw_id, user=request.user)
    logical_txns = list(raw.logical_transactions.select_related('category__group').order_by('pk'))
    category_groups = get_category_groups(request.user)
    is_split = len(logical_txns) > 1
    next_url = _safe_next_url(request)

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
                from ..services.exchange_rates import convert_transaction
                convert_transaction(first)
                first.save(update_fields=['amount_crc', 'amount_usd'])
                messages.success(request, 'Transaction unsplit and reset to unclassified.')
            edit_url = reverse('core:edit_transaction', args=[raw_id])
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
            return redirect('core:edit_transaction', raw_id=raw_id)

        if not parsed:
            messages.error(request, 'At least one entry is required.')
            return redirect('core:edit_transaction', raw_id=raw_id)

        total = sum(a for _, a, _ in parsed)
        if total != raw.amount:
            messages.error(request, f'Amounts ({total}) must equal the original amount ({raw.amount}).')
            return redirect('core:edit_transaction', raw_id=raw_id)

        first_logical = logical_txns[0] if logical_txns else None
        if len(logical_txns) > 1:
            for lt in logical_txns[1:]:
                lt.delete()

        from ..services.exchange_rates import convert_transaction

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
        return redirect(next_url or 'core:transaction_list')

    return render(request, 'core/edit_transaction.html', {
        'raw': raw,
        'logical_txns': logical_txns,
        'category_groups': category_groups,
        'is_split': is_split,
        'next_url': next_url,
    })


@login_required
def split_transaction(request, raw_id):
    """Split a raw transaction into multiple logical transactions."""
    return redirect('core:edit_transaction', raw_id=raw_id)


@login_required
@require_POST
def unsplit_transaction(request, raw_id):
    """Merge all logical transactions back to a single 1:1 with the raw."""
    raw = get_object_or_404(RawTransaction, pk=raw_id, user=request.user)
    logical_txns = list(raw.logical_transactions.order_by('pk'))

    if len(logical_txns) <= 1:
        messages.info(request, 'Transaction is not split.')
        return redirect('core:transaction_list')

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

    from ..services.exchange_rates import convert_transaction
    convert_transaction(first)
    first.save(update_fields=['amount_crc', 'amount_usd'])

    messages.success(request, 'Transaction unsplit and reset to unclassified.')
    return redirect('core:transaction_list')
