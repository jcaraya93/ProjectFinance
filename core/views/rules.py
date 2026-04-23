from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.utils.http import urlencode

from ..models import Transaction, Category, CategoryGroup, ClassificationRule
from ..forms import YamlRuleForm
from ..ratelimit import ratelimit
from ..services.yaml_classifier import reload_rules as _reload_yaml
from ._helpers import _safe_next_url, get_category_groups

__all__ = [
    'yaml_rule_list',
    'yaml_rule_add',
    'yaml_rule_edit',
    'yaml_rule_delete',
    'delete_all_rules',
    'reclassify_all',
    'classify_unclassified',
    'clear_classifications',
    'yaml_category_add',
    'yaml_category_delete',
    'yaml_category_delete_all',
    'yaml_category_rename',
]


@login_required
def yaml_rule_list(request):
    """List, filter, and search classification rules."""
    # Build group->categories from DB
    group_categories = {}
    for grp in get_category_groups(request.user, exclude_unclassified=True):
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

    return render(request, 'core/yaml_rule_list.html', {
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
                _reload_yaml()
                desc = rule_dict.get('description', '?')
                messages.success(request, f'Rule "{desc}" \u2192 {cat_name} added.')
                from django.utils.http import urlencode
                return redirect(f"{reverse('core:yaml_rule_list')}?{urlencode({'group': group_slug, 'category': cat_name})}")
    else:
        initial = {}
        grp = request.GET.get('group', '')
        cat = request.GET.get('category', '')
        if grp:
            initial['group'] = grp
        if grp and cat:
            initial['category'] = f'{grp}:{cat}'
        form = YamlRuleForm(initial=initial)
    return render(request, 'core/yaml_rule_form.html', {
        'form': form,
        'title': 'Add Rule',
    })


@login_required
def yaml_rule_edit(request, idx):
    """Edit a classification rule by pk."""
    rule_obj = get_object_or_404(ClassificationRule.objects.filter(user=request.user).select_related('category__group'), pk=idx)
    next_url = _safe_next_url(request)
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
                _reload_yaml()
                messages.success(request, 'Rule updated.')
                return redirect(next_url or 'core:yaml_rule_list')
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

    return render(request, 'core/yaml_rule_form.html', {
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
    next_url = _safe_next_url(request)
    rule = get_object_or_404(ClassificationRule, pk=idx, user=request.user)
    # Reset transactions that were classified by this rule
    unclassified = Category.get_unclassified(request.user)
    Transaction.objects.filter(user=request.user).filter(matched_rule=rule).update(
        category=unclassified, matched_rule=None, classification_method='unclassified'
    )
    rule.delete()
    _reload_yaml()
    messages.success(request, 'Rule deleted.')
    return redirect(next_url or 'core:yaml_rule_list')


@login_required
@require_POST
def delete_all_rules(request):
    """Delete all classification rules. Resets rule-classified transactions to unclassified."""
    unclassified = Category.get_unclassified(request.user)
    rules = ClassificationRule.objects.filter(user=request.user)
    rule_count = rules.count()

    Transaction.objects.filter(user=request.user, classification_method='rule').update(
        category=unclassified, matched_rule=None, classification_method='unclassified'
    )
    rules.delete()
    _reload_yaml()

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
@ratelimit(key='category_add', rate='20/h', method='POST')
def yaml_category_add(request):
    """Add a new category."""
    group_slug = request.POST.get('group', '')
    cat_name = request.POST.get('category', '').strip()
    if not group_slug or not cat_name:
        messages.error(request, 'Group and category name are required.')
        return redirect('core:category_list')

    grp = CategoryGroup.objects.filter(slug=group_slug).first()
    if not grp:
        messages.error(request, f'Group "{group_slug}" not found.')
        return redirect('core:category_list')

    if Category.objects.filter(user=request.user).filter(name=cat_name, group=grp).exists():
        messages.error(request, f'Category "{cat_name}" already exists.')
        return redirect('core:category_list')

    Category.objects.create(name=cat_name, group=grp, color='#6c757d', user=request.user)

    messages.success(request, f'Category "{cat_name}" created.')
    return redirect('core:category_list')


@login_required
@require_POST
def yaml_category_delete(request):
    """Delete a category and its rules."""
    group_slug = request.POST.get('group', '')
    cat_name = request.POST.get('category', '').strip()

    PROTECTED = {'Default'}
    if cat_name in PROTECTED:
        messages.error(request, f'The "{cat_name}" category is protected and cannot be deleted.')
        return redirect('core:category_list')

    if not group_slug or not cat_name:
        messages.error(request, 'Group and category name are required.')
        return redirect('core:category_list')

    cat = Category.objects.filter(user=request.user).filter(name=cat_name, group__slug=group_slug).first()
    if not cat:
        messages.error(request, f'Category "{cat_name}" not found.')
        return redirect('core:category_list')

    # Move transactions to Unclassified
    unclassified = Category.get_unclassified(request.user)
    Transaction.objects.filter(user=request.user).filter(category=cat).update(category=unclassified)
    cat.delete()  # Cascades to ClassificationRule

    messages.success(request, f'Category "{cat_name}" deleted.')
    return redirect('core:category_list')


@login_required
@require_POST
def yaml_category_delete_all(request):
    """Delete all non-protected categories and their rules. Transactions become unclassified."""
    unclassified = Category.get_unclassified(request.user)
    group_slug = request.POST.get('group', '').strip()

    deletable = Category.objects.filter(user=request.user).exclude(name__in=Category.PROTECTED_NAMES)
    if group_slug:
        deletable = deletable.filter(group__slug=group_slug)

    cat_count = deletable.count()
    rule_count = ClassificationRule.objects.filter(user=request.user, category__in=deletable).count()

    # Move all affected transactions to Unclassified
    Transaction.objects.filter(user=request.user, category__in=deletable).update(
        category=unclassified, matched_rule=None, classification_method='unclassified'
    )
    deletable.delete()  # Cascades to ClassificationRule

    scope= f'"{dict(CategoryGroup.SLUG_CHOICES).get(group_slug, group_slug)}" ' if group_slug else ''
    messages.success(
        request,
        f'Deleted {cat_count} {scope}categories and {rule_count} rules. Affected transactions moved to Unclassified.'
    )
    return redirect('core:account_page')


@login_required
@require_POST
def yaml_category_rename(request):
    """Rename a category."""
    group_slug = request.POST.get('group', '')
    old_name = request.POST.get('old_name', '').strip()
    new_name = request.POST.get('new_name', '').strip()

    PROTECTED = {'Default'}
    if old_name in PROTECTED:
        messages.error(request, f'The "{old_name}" category is protected and cannot be renamed.')
        return redirect('core:category_list')

    if not group_slug or not old_name or not new_name:
        messages.error(request, 'Group, old name, and new name are required.')
        return redirect('core:category_list')

    if old_name == new_name:
        return redirect('core:category_list')

    cat = Category.objects.filter(user=request.user).filter(name=old_name, group__slug=group_slug).first()
    if not cat:
        messages.error(request, f'Category "{old_name}" not found.')
        return redirect('core:category_list')

    if Category.objects.filter(user=request.user).filter(name=new_name, group__slug=group_slug).exists():
        messages.error(request, f'Category "{new_name}" already exists.')
        return redirect('core:category_list')

    cat.name = new_name
    cat.save(update_fields=['name'])

    messages.success(request, f'Category renamed: "{old_name}" \u2192 "{new_name}".')
    return redirect('core:category_list')
