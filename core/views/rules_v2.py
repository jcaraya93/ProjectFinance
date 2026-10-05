from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import Account, CategoryGroup, CategoryNode, ClassificationRuleV2, LogicalTransaction
from .categories_v2 import _build_tree

__all__ = [
    'rules_v2_list',
    'rules_v2_save',
    'rules_v2_delete',
]


def _parse_metadata(text):
    """Parse one `key=value` pair per line into a dict. Raises ValueError on a bad line."""
    metadata = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        key, sep, value = line.partition('=')
        if not sep or not key.strip():
            raise ValueError(f'Metadata line "{line}" must look like key=value.')
        metadata[key.strip()] = value.strip()
    return metadata


def _parse_amount(raw, label):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise ValueError(f'{label} must be a number.')


def _back(request, fallback_node=None):
    """Redirect to the list, keeping the node selected in the left pane when there was one."""
    node = request.POST.get('selected_node') or ''
    if not node.isdigit():
        node = fallback_node
    url = reverse('core:rules_v2_list')
    return redirect(f'{url}?node={node}' if node else url)


@login_required
def rules_v2_list(request):
    """Show categories by group and rules assigned directly to the selected category."""
    CategoryNode.ensure_protected(request.user)
    rules = list(
        ClassificationRuleV2.objects.filter(user=request.user).select_related('category__group')
    )
    tree = _build_tree(list(CategoryNode.objects.filter(user=request.user).select_related('group')))

    selected = None
    selected_id = request.GET.get('node', '')
    if selected_id.isdigit():
        selected = next((r for r in tree if r['node'].pk == int(selected_id)), None)

    if selected:
        shown = [r for r in rules if r.category_id == selected['node'].pk]
    else:
        shown = list(rules)
    order = {row['node'].pk: i for i, row in enumerate(tree)}
    shown.sort(key=lambda r: (order.get(r.category_id, 0), r.pk))

    sections = [
        {'name': grp.name, 'slug': grp.slug, 'rows': [r for r in tree if r['node'].group_id == grp.pk]}
        for grp in CategoryGroup.objects.order_by('name')
    ]

    return render(request, 'core/rules_v2_list.html', {
        'sections': sections,
        'rules': shown,
        'selected': selected,
        'category_options': tree,
        'account_types': Account.ACCOUNT_TYPES,
    })


@login_required
@require_POST
def rules_v2_save(request):
    """Create a rule, or update one when `id` is supplied."""
    rule_id = request.POST.get('id')
    category = get_object_or_404(CategoryNode, pk=request.POST.get('category') or 0, user=request.user)

    account_type = request.POST.get('account_type', '').strip()
    if account_type and account_type not in dict(Account.ACCOUNT_TYPES):
        messages.error(request, 'Invalid account type.')
        return redirect('core:rules_v2_list')

    try:
        amount_min = _parse_amount(request.POST.get('amount_min'), 'Minimum amount')
        amount_max = _parse_amount(request.POST.get('amount_max'), 'Maximum amount')
        metadata = _parse_metadata(request.POST.get('metadata', ''))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('core:rules_v2_list')

    previous_category_id = None
    if rule_id:
        rule = get_object_or_404(ClassificationRuleV2, pk=rule_id, user=request.user)
        previous_category_id = rule.category_id
    else:
        rule = ClassificationRuleV2(user=request.user)
    rule.category = category
    rule.description = request.POST.get('description', '').strip()
    rule.account_type = account_type
    rule.amount_min, rule.amount_max, rule.metadata = amount_min, amount_max, metadata
    rule.detail = request.POST.get('detail', '').strip()

    try:
        rule.save()
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
        return redirect('core:rules_v2_list')

    moved = 0
    if previous_category_id is not None and previous_category_id != category.pk:
        # Manual classifications are left untouched.
        moved = LogicalTransaction.objects.filter(
            user=request.user, matched_rule_v2=rule, classification_method_v2='rule',
        ).update(category_v2=category)

    message = f'Saved rule \u2192 {category.name}.'
    if moved:
        message += f' {moved} transaction{"s" if moved != 1 else ""} moved to {category.name}.'
    messages.success(request, message)
    return _back(request, category.pk)


@login_required
@require_POST
def rules_v2_delete(request):
    rule = get_object_or_404(ClassificationRuleV2, pk=request.POST.get('id') or 0, user=request.user)
    rule.delete()
    messages.success(request, 'Rule deleted.')
    return _back(request)
