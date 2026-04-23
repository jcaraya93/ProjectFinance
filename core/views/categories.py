from django.db.models import Count, Q
from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST

from ..models import Transaction, Category, CategoryGroup, ClassificationRule

__all__ = [
    'category_list',
    'export_categories',
    'import_categories',
]


@login_required
def category_list(request):
    """List all categories grouped by group, with rule and transaction counts."""
    annotated_cats = (
        Category.objects.filter(user=request.user)
        .select_related('group')
        .annotate(
            txn_count=Count('logical_transactions', distinct=True),
            rule_count=Count('classification_rules', distinct=True),
        )
        .order_by('name')
    )

    # Build a lookup: group_slug -> [cat_dicts]
    cats_by_group = {}
    for cat in annotated_cats:
        cats_by_group.setdefault(cat.group.slug, []).append({
            'id': cat.pk,
            'name': cat.name,
            'color': cat.color,
            'rule_count': cat.rule_count,
            'txn_count': cat.txn_count,
        })

    groups = {}
    for grp in CategoryGroup.objects.exclude(slug='unclassified').order_by('name'):
        groups[grp.slug] = {
            'name': grp.name,
            'categories': cats_by_group.get(grp.slug, []),
        }

    # Add unclassified group last
    unclassified_grp = CategoryGroup.objects.filter(slug='unclassified').first()
    if unclassified_grp:
        groups['unclassified'] = {
            'name': unclassified_grp.name,
            'categories': cats_by_group.get('unclassified', []),
        }

    return render(request, 'core/category_list.html', {'groups': groups})


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
        return redirect('core:account_page')

    try:
        data = yaml.safe_load(uploaded.read().decode('utf-8'))
    except Exception:
        messages.error(request, 'Invalid YAML file.')
        return redirect('core:account_page')

    if not data or 'groups' not in data:
        messages.error(request, 'YAML must have a "groups" key.')
        return redirect('core:account_page')

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
    return redirect('core:account_page')
