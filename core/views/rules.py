from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.utils.http import urlencode

from ..models import Transaction, Category, CategoryGroup, CategoryNode, ClassificationRuleV2
from ..forms import YamlRuleForm
from ..ratelimit import ratelimit
from ..services.yaml_classifier import reload_rules as _reload_yaml
from .transactions import _unclassified_node

__all__ = [
    'delete_all_rules',
    'reclassify_all',
    'classify_unclassified',
    'clear_classifications',
    'yaml_category_delete_all',
]


@login_required
@require_POST
def delete_all_rules(request):
    """Delete all V2 rules. Rule-classified transactions become unclassified (V2 fields only)."""
    CategoryNode.ensure_protected(request.user)
    unclassified = _unclassified_node(request.user)
    rules = ClassificationRuleV2.objects.filter(user=request.user)
    rule_count = rules.count()

    Transaction.objects.filter(user=request.user, classification_method_v2='rule').update(
        category_v2=unclassified, matched_rule_v2=None, classification_method_v2='unclassified'
    )
    rules.delete()

    messages.success(request, f'Deleted {rule_count} rules. Affected transactions moved to Unclassified.')
    return redirect('core:account_page')


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

    from ..services.yaml_classifier import classify_transactions_yaml
    classified = classify_transactions_yaml(
        Transaction.objects.filter(user=request.user).filter(classification_method='unclassified').select_related(
            'category', 'raw_transaction__ledger__statement_import__account'
        )
    )
    manual_count = Transaction.objects.filter(user=request.user).filter(classification_method='manual').count()
    remaining = total - classified
    messages.success(request, f'Rules applied: {classified} transactions classified. {remaining} unclassified, {manual_count} manual (untouched).')
    return redirect('core:transaction_list')


@login_required
@require_POST
def classify_unclassified(request):
    """Apply rules only to unclassified transactions."""
    from ..services.yaml_classifier import classify_transactions_yaml
    classified = classify_transactions_yaml(
        Transaction.objects.filter(user=request.user).filter(classification_method='unclassified').select_related(
            'category', 'raw_transaction__ledger__statement_import__account'
        )
    )
    remaining = Transaction.objects.filter(user=request.user).filter(classification_method='unclassified').count()
    messages.success(request, f'Rules applied: {classified} transactions classified. {remaining} remain unclassified.')
    next_url = request.POST.get('next')
    if next_url:
        from django.utils.http import url_has_allowed_host_and_scheme
        if url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(next_url)
    return redirect('core:transaction_list')


@login_required
@require_POST
def clear_classifications(request):
    """Clear classifications by method (rule, manual, or all)."""
    method = request.POST.get('method', '')
    unclassified = Category.get_unclassified(request.user)

    qs = Transaction.objects.filter(user=request.user)
    if method == 'rule':
        qs = qs.filter(classification_method='rule')
        label = 'rule-based'
    elif method == 'manual':
        qs = qs.filter(classification_method='manual')
        label = 'manual'
    elif method == 'all':
        qs = qs.exclude(classification_method='unclassified')
        label = 'all'
    else:
        messages.error(request, 'Invalid classification method.')
        return redirect('core:transaction_list')

    count = qs.update(
        category=unclassified, matched_rule=None, classification_method='unclassified'
    )
    messages.success(request, f'Cleared {count} {label} classifications.')
    return redirect('core:transaction_list')


@login_required
@require_POST
def yaml_category_delete_all(request):
    """Delete all non-protected V2 categories and their rules. Transactions become unclassified."""
    unclassified = _unclassified_node(request.user)
    group_slug = request.POST.get('group', '').strip()

    deletable = CategoryNode.objects.filter(user=request.user).exclude(
        parent__isnull=True, name=CategoryNode.UNCLASSIFIED_NAME,
    )
    if group_slug:
        deletable = deletable.filter(group__slug=group_slug)
    ids = list(deletable.values_list('pk', flat=True))

    cat_count = len(ids)
    rule_count = ClassificationRuleV2.objects.filter(user=request.user, category_id__in=ids).count()

    Transaction.objects.filter(user=request.user, category_v2_id__in=ids).update(
        category_v2=unclassified, matched_rule_v2=None, classification_method_v2='unclassified'
    )
    # Parent FK is PROTECT, so delete from the leaves up
    remaining = CategoryNode.objects.filter(pk__in=ids)
    while remaining.exists():
        remaining.filter(children__isnull=True).delete()

    scope = f'"{dict(CategoryGroup.SLUG_CHOICES).get(group_slug, group_slug)}" ' if group_slug else ''
    messages.success(
        request,
        f'Deleted {cat_count} {scope}categories and {rule_count} rules. Affected transactions moved to Unclassified.'
    )
    return redirect('core:account_page')
