from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import Category, CategoryGroup, CategoryNode

__all__ = [
    'category_v2_import_v1',
    'category_v2_list',
    'category_v2_save',
    'category_v2_group',
    'category_v2_move',
    'category_v2_delete',
]

HEX_COLOR_LENGTH = 7
DEFAULT_COLOR = '#6c757d'


def _build_tree(nodes):
    """Flatten a user's nodes into depth-first rows with depth and ancestor ids."""
    children = {}
    names = {node.pk: node.name for node in nodes}
    for node in nodes:
        children.setdefault(node.parent_id, []).append(node)

    rows = []

    def walk(parent_id, depth, ancestors):
        for node in sorted(children.get(parent_id, []), key=lambda n: n.name.lower()):
            chain = ancestors + [node.pk]
            rows.append({
                'node': node,
                'parent_name': names.get(node.parent_id, ''),
                'depth': depth,
                'padding': 12 + depth * 24,
                'label': '— ' * depth + node.name,
                'is_leaf': node.pk not in children,
                'child_count': len(children.get(node.pk, [])),
                'ancestors': ','.join(str(pk) for pk in chain),
            })
            walk(node.pk, depth + 1, chain)

    walk(None, 0, [])
    return rows


@login_required
def category_v2_list(request):
    """Tree view of the user's hierarchical categories, grouped by CategoryGroup."""
    nodes = list(CategoryNode.objects.filter(user=request.user).select_related('group'))
    rows_by_group = {}
    for node in nodes:
        rows_by_group.setdefault(node.group_id, [])

    all_rows = _build_tree(nodes)
    for row in all_rows:
        rows_by_group[row['node'].group_id].append(row)

    groups = []
    for grp in CategoryGroup.objects.exclude(slug=CategoryGroup.UNCLASSIFIED).order_by('name'):
        groups.append({
            'slug': grp.slug,
            'name': grp.name,
            'rows': rows_by_group.get(grp.pk, []),
        })

    return render(request, 'core/category_v2_list.html', {
        'groups': groups,
        'parent_options': all_rows,
    })


@login_required
@require_POST
def category_v2_save(request):
    """Create a node, or update one when `id` is supplied."""
    node_id = request.POST.get('id')
    name = request.POST.get('name', '').strip()
    color = request.POST.get('color', '').strip() or DEFAULT_COLOR
    parent_id = request.POST.get('parent') or None

    if not name:
        messages.error(request, 'Category name is required.')
        return redirect('core:category_v2_list')
    if len(color) != HEX_COLOR_LENGTH or not color.startswith('#'):
        messages.error(request, 'Color must be a hex value like #6c757d.')
        return redirect('core:category_v2_list')

    parent = None
    if parent_id:
        parent = get_object_or_404(CategoryNode, pk=parent_id, user=request.user)

    if node_id:
        node = get_object_or_404(CategoryNode, pk=node_id, user=request.user)
        node.name, node.color, node.parent = name, color, parent
        # Children must stay in the same group as their parent.
        if parent:
            node.group = parent.group
    else:
        group_slug = request.POST.get('group', '')
        if parent:
            group = parent.group
        elif group_slug in dict(CategoryGroup.SLUG_CHOICES) and group_slug != CategoryGroup.UNCLASSIFIED:
            group = CategoryGroup.get_group(group_slug)
        else:
            messages.error(request, 'Invalid group.')
            return redirect('core:category_v2_list')
        node = CategoryNode(name=name, color=color, group=group, parent=parent, user=request.user)

    try:
        node.save()
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
        return redirect('core:category_v2_list')

    messages.success(request, f'Saved "{node.name}".')
    return redirect('core:category_v2_list')


@login_required
@require_POST
def category_v2_group(request):
    """Create a new parent at the selected siblings' level and move them under it."""
    name = request.POST.get('name', '').strip()
    color = request.POST.get('color', '').strip() or DEFAULT_COLOR
    ids = request.POST.getlist('ids')

    if not name:
        messages.error(request, 'Parent name is required.')
        return redirect('core:category_v2_list')
    if len(color) != HEX_COLOR_LENGTH or not color.startswith('#'):
        messages.error(request, 'Color must be a hex value like #6c757d.')
        return redirect('core:category_v2_list')

    selected = list(CategoryNode.objects.filter(user=request.user, pk__in=ids))
    if len(selected) < 2 or len(selected) != len(set(ids)):
        messages.error(request, 'Select at least two categories to group.')
        return redirect('core:category_v2_list')
    if len({(n.group_id, n.parent_id) for n in selected}) != 1:
        messages.error(request, 'Selected categories must share the same group and parent.')
        return redirect('core:category_v2_list')

    first = selected[0]
    try:
        with transaction.atomic():
            new_parent = CategoryNode.objects.create(
                name=name, color=color, group=first.group, parent=first.parent, user=request.user,
            )
            for node in selected:
                node.parent = new_parent
                node.save()
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
        return redirect('core:category_v2_list')

    messages.success(request, f'Grouped {len(selected)} categories under "{name}".')
    return redirect('core:category_v2_list')


@login_required
@require_POST
def category_v2_move(request):
    """Move the selected nodes under a single parent (or to the top level)."""
    ids = request.POST.getlist('ids')
    parent_id = request.POST.get('parent') or None

    selected = list(CategoryNode.objects.filter(user=request.user, pk__in=ids).select_related('group'))
    if not selected or len(selected) != len(set(ids)):
        messages.error(request, 'Select at least one category to move.')
        return redirect('core:category_v2_list')

    parent = None
    if parent_id:
        parent = get_object_or_404(CategoryNode, pk=parent_id, user=request.user)

    group_ids = {n.group_id for n in selected}
    if len(group_ids) != 1 or (parent and parent.group_id not in group_ids):
        messages.error(request, 'Categories can only be moved within their own group.')
        return redirect('core:category_v2_list')
    if parent and parent.pk in {n.pk for n in selected}:
        messages.error(request, 'A category cannot be moved under itself.')
        return redirect('core:category_v2_list')

    try:
        with transaction.atomic():
            for node in selected:
                node.parent = parent
                node.save()
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
        return redirect('core:category_v2_list')

    target = f'"{parent.name}"' if parent else 'the top level'
    messages.success(request, f'Moved {len(selected)} categories under {target}.')
    return redirect('core:category_v2_list')


@login_required
@require_POST
def category_v2_import_v1(request):
    """Copy the user's V1 categories into V2 as top-level nodes. Idempotent; V1 data is untouched."""
    existing = set(
        CategoryNode.objects.filter(user=request.user).values_list('group_id', 'name')
    )
    created = 0
    with transaction.atomic():
        v1 = (Category.objects.filter(user=request.user)
              .exclude(group__slug=CategoryGroup.UNCLASSIFIED)
              .exclude(name__in=Category.PROTECTED_NAMES))
        for cat in v1.select_related('group').order_by('group__name', 'name'):
            if (cat.group_id, cat.name) in existing:
                continue
            CategoryNode.objects.create(name=cat.name, color=cat.color, group=cat.group, user=request.user)
            created += 1
    skipped = v1.count() - created
    if created:
        messages.success(request, f'Imported {created} categories from V1' +
                         (f' ({skipped} already existed).' if skipped else '.'))
    else:
        messages.info(request, 'Nothing to import: all V1 categories already exist in V2.')
    return redirect('core:category_v2_list')


@login_required
@require_POST
def category_v2_delete(request):
    ids = request.POST.getlist('ids') or [request.POST.get('id')]
    nodes = list(CategoryNode.objects.filter(pk__in=[i for i in ids if i and i.isdigit()], user=request.user))
    if not nodes or len(nodes) != len(set(ids)):
        raise Http404('Category not found')

    with_children = [n.name for n in nodes if n.children.exists()]
    if with_children:
        messages.error(
            request,
            f'Nothing deleted. These have subcategories (move or delete them first): {", ".join(with_children)}.',
        )
        return redirect('core:category_v2_list')

    with transaction.atomic():
        for node in nodes:
            node.delete()
    if len(nodes) == 1:
        messages.success(request, f'Deleted "{nodes[0].name}".')
    else:
        messages.success(request, f'Deleted {len(nodes)} categories.')
    return redirect('core:category_v2_list')
