import re
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.db.models import Count
from django.http import QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from ..models import LogicalTransaction, Tag, TagGroup
from ..ratelimit import ratelimit
from ._helpers import _safe_next_url

__all__ = ['tag_list', 'tag_save', 'tag_delete', 'tag_group_save', 'tag_group_delete', 'bulk_tag']

COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
MAX_NAME = 50


def parse_tag_names(raw):
    """Split a comma-separated string into distinct tag names (case-insensitive), preserving order."""
    seen, names = set(), []
    for part in (raw or '').split(','):
        name = ' '.join(part.split())
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name[:MAX_NAME])
    return names


def get_or_create_tag(user, name):
    tag = Tag.objects.filter(user=user, name__iexact=name).first()
    if tag:
        return tag
    try:
        return Tag.objects.create(user=user, name=name)
    except IntegrityError:
        return Tag.objects.get(user=user, name__iexact=name)


def _parse_date(value):
    try:
        return date.fromisoformat((value or '').strip())
    except ValueError:
        return None


@login_required
def tag_list(request):
    tags = list(Tag.objects.filter(user=request.user).select_related('group').annotate(
        txn_count=Count('logical_transactions', distinct=True), rule_count=Count('rules', distinct=True)))
    all_txns = LogicalTransaction.objects.filter(user=request.user)
    for tag in tags:
        tag.txn_url = reverse('core:transaction_list') + '?' + urlencode([
            ('tag', tag.pk), ('return_to', request.get_full_path()),
        ])
        tag.rules_url = reverse('core:rules_v2_list') + '?' + urlencode([('tag', tag.pk)])
        if tag.has_dates:
            tag.range_count = all_txns.filter(date__range=(tag.start_date, tag.end_date)).count()
            tag.range_url = reverse('core:transaction_list') + '?' + urlencode([
                ('start_date', tag.start_date.isoformat()), ('end_date', tag.end_date.isoformat()),
                ('return_to', request.get_full_path()),
            ])
    groups = list(TagGroup.objects.filter(user=request.user))
    sections = [{'group': g, 'tags': [t for t in tags if t.group_id == g.pk]} for g in groups]
    ungrouped = [t for t in tags if t.group_id is None]
    if ungrouped or not sections:
        sections.append({'group': None, 'tags': ungrouped})
    return render(request, 'core/tag_list.html', {
        'sections': sections, 'groups': groups, 'default_color': Tag.DEFAULT_COLOR,
    })


@login_required
@require_POST
def tag_save(request):
    """Create a tag, or update one when `id` is supplied."""
    tag_id = request.POST.get('id')
    names = parse_tag_names(request.POST.get('name'))
    name = names[0] if names else ''
    color = request.POST.get('color', '').strip() or Tag.DEFAULT_COLOR
    notes = request.POST.get('notes', '').strip()
    raw_start = request.POST.get('start_date', '').strip()
    raw_end = request.POST.get('end_date', '').strip()
    start, end = _parse_date(raw_start), _parse_date(raw_end)
    group_id = request.POST.get('group') or None
    group = None
    if group_id:
        group = TagGroup.objects.filter(pk=group_id, user=request.user).first() if group_id.isdigit() else None

    error = None
    if not name:
        error = 'Tag name is required.'
    elif len((request.POST.get('name') or '').strip()) > MAX_NAME:
        error = f'Tag names are limited to {MAX_NAME} characters.'
    elif not COLOR_RE.match(color):
        error = 'Invalid colour.'
    elif ',' in (request.POST.get('name') or ''):
        error = 'Tag names cannot contain commas.'
    elif Tag.objects.filter(user=request.user, name__iexact=name).exclude(pk=tag_id or 0).exists():
        error = f'A tag named "{name}" already exists.'
    elif group_id and group is None:
        error = 'Unknown tag group.'
    elif bool(raw_start) != bool(raw_end) or (raw_start and not (start and end)):
        error = 'Provide both a valid start and end date, or leave both empty.'
    elif start and end < start:
        error = 'The end date cannot be before the start date.'
    elif len(notes) > 500:
        error = 'Notes are too long.'
    if error:
        messages.error(request, error)
        return redirect('core:tag_list')

    if tag_id:
        tag = get_object_or_404(Tag, pk=tag_id, user=request.user)
    else:
        tag = Tag(user=request.user)
    tag.name, tag.color, tag.group, tag.notes = name, color.lower(), group, notes
    tag.start_date, tag.end_date = start, end
    tag.save()
    messages.success(request, f'Tag "{tag.name}" saved.')
    return redirect('core:tag_list')


@login_required
@require_POST
def tag_delete(request):
    tag = get_object_or_404(Tag, pk=request.POST.get('id') or 0, user=request.user)
    tag.delete()
    messages.success(request, 'Tag deleted. Transactions were not changed.')
    return redirect('core:tag_list')


@login_required
@require_POST
def tag_group_save(request):
    """Create a tag group, or rename one when `id` is supplied."""
    group_id = request.POST.get('id')
    name = ' '.join((request.POST.get('name') or '').split())
    if not name or len(name) > MAX_NAME:
        messages.error(request, f'Group name is required (max {MAX_NAME} characters).')
    elif TagGroup.objects.filter(user=request.user, name__iexact=name).exclude(pk=group_id or 0).exists():
        messages.error(request, f'A group named "{name}" already exists.')
    else:
        group = get_object_or_404(TagGroup, pk=group_id, user=request.user) if group_id else TagGroup(user=request.user)
        group.name = name
        group.save()
        messages.success(request, f'Group "{name}" saved.')
    return redirect('core:tag_list')


@login_required
@require_POST
def tag_group_delete(request):
    group = get_object_or_404(TagGroup, pk=request.POST.get('id') or 0, user=request.user)
    group.delete()
    messages.success(request, 'Group deleted. Its tags are now ungrouped.')
    return redirect('core:tag_list')

@login_required
@require_POST
@ratelimit(key='bulk_tag', rate='30/m', method='POST')
def bulk_tag(request):
    """Add or remove one tag on the selected transactions (or on all matching the current filters)."""
    from .transactions import _apply_transaction_filters

    next_url = _safe_next_url(request)
    destination = next_url or 'core:transaction_list'
    mode = request.POST.get('mode')
    if mode not in ('add', 'remove'):
        messages.error(request, 'Unknown tag action.')
        return redirect(destination)

    tag_id = request.POST.get('tag_id')
    if tag_id:
        tag = get_object_or_404(Tag, pk=tag_id, user=request.user)
    else:
        messages.error(request, 'Choose a tag first.')
        return redirect(destination)

    if request.POST.get('select_all_matching') == '1':
        params = QueryDict(request.POST.get('filter_qs', ''))
        qs = _apply_transaction_filters(LogicalTransaction.objects.filter(user=request.user), params, request.user)
    else:
        txn_ids = request.POST.getlist('txn_ids')
        if not txn_ids:
            messages.error(request, 'No transactions selected.')
            return redirect(destination)
        qs = LogicalTransaction.objects.filter(user=request.user, pk__in=txn_ids)

    ids = list(qs.values_list('pk', flat=True))
    through = LogicalTransaction.tags.through
    if mode == 'add':
        existing = set(through.objects.filter(tag=tag, logicaltransaction_id__in=ids).values_list('logicaltransaction_id', flat=True))
        through.objects.bulk_create(
            [through(logicaltransaction_id=pk, tag_id=tag.pk) for pk in ids if pk not in existing],
            ignore_conflicts=True,
        )
        messages.success(request, f'Tagged {len(ids) - len(existing)} transaction(s) with "{tag.name}".')
    else:
        removed, _ = through.objects.filter(tag=tag, logicaltransaction_id__in=ids).delete()
        messages.success(request, f'Removed "{tag.name}" from {removed} transaction(s).')
    return redirect(destination)