"""Food tag comparisons without multiplying amounts across many-to-many joins."""

from calendar import monthrange
from collections import defaultdict
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qsl, urlencode, urlsplit

from django.db.models import Prefetch
from django.urls import reverse

ZERO = Decimal('0')
TRAVEL_GROUP_NAMES = ('Travel Local', 'Travel International')
BUCKETS = [
    ('local', 'Travel Local', '#18BC9C'),
    ('international', 'Travel International', '#8E44AD'),
    ('both', 'Travel Local + International', '#F39C12'),
    ('other', 'Other food', '#95A5A6'),
]


def build_food_tag_analysis(food_qs, tags, currency, period, return_to):
    amount_field = 'amount_crc' if currency == 'CRC' else 'amount_usd'
    selected_tags = list(tags)
    tag_map = {tag.pk: tag for tag in selected_tags}
    months = set()
    group_months = defaultdict(lambda: defaultdict(lambda: {'total': ZERO, 'count': 0}))
    totals = {'tagged': ZERO, 'other': ZERO, 'tagged_count': 0, 'other_count': 0}
    per_tag = {
        tag.pk: {'total': ZERO, 'count': 0, 'months': defaultdict(lambda: ZERO)}
        for tag in selected_tags
    }
    overlapping_count = 0
    transactions = food_qs.order_by('date', 'pk').prefetch_related(
        Prefetch('tags', queryset=tags, to_attr='comparison_tags'),
    )
    for transaction in transactions:
        amount = abs(getattr(transaction, amount_field))
        month = transaction.date.strftime('%Y-%m')
        linked_tags = transaction.comparison_tags
        bucket = 'tagged' if linked_tags else 'other'
        months.add(month)
        totals[bucket] += amount
        totals[bucket + '_count'] += 1
        overlapping_count += len(linked_tags) > 1
        linked_groups = {tag.group.name.casefold() for tag in linked_tags}
        if len(linked_groups) > 1:
            group_bucket = 'both'
        elif TRAVEL_GROUP_NAMES[0].casefold() in linked_groups:
            group_bucket = 'local'
        elif TRAVEL_GROUP_NAMES[1].casefold() in linked_groups:
            group_bucket = 'international'
        else:
            group_bucket = 'other'
        group_months[group_bucket][month]['total'] += amount
        group_months[group_bucket][month]['count'] += 1
        for tag in linked_tags:
            per_tag[tag.pk]['total'] += amount
            per_tag[tag.pk]['count'] += 1
            per_tag[tag.pk]['months'][month] += amount

    labels = sorted(months)
    base_params = [
        (key, value) for key, value in parse_qsl(urlsplit(period['category_transactions_url']).query)
        if key != 'return_to'
    ]
    base_params.extend([('return_to', return_to), ('display_currency', currency)])

    def tag_url(tag_ids, month=None):
        if not tag_ids or not period['category_transactions_url']:
            return ''
        params = list(base_params)
        if month:
            year, mon = map(int, month.split('-'))
            start, end = date(year, mon, 1), date(year, mon, monthrange(year, mon)[1])
            if period['start_date']:
                start = max(start, period['start_date'])
            if period['end_date']:
                end = min(end, period['end_date'])
            params = [(key, value) for key, value in params if key not in ('start_date', 'end_date')]
            params.extend([('start_date', start.isoformat()), ('end_date', end.isoformat())])
        params.extend(('tag', tag_id) for tag_id in tag_ids)
        return reverse('core:transaction_list') + '?' + urlencode(params)

    def average(amount, count):
        return amount / count if count else None

    tagged_avg = average(totals['tagged'], totals['tagged_count'])
    other_avg = average(totals['other'], totals['other_count'])
    total = totals['tagged'] + totals['other']
    rows = []
    for tag_id, values in per_tag.items():
        if not values['count']:
            continue
        tag = tag_map[tag_id]
        avg = average(values['total'], values['count'])
        rows.append({
            'name': tag.name, 'color': tag.color, 'group_name': tag.group.name if tag.group else 'Ungrouped',
            'total': values['total'], 'count': values['count'], 'avg': avg,
            'share': values['total'] / total * 100 if total else ZERO,
            'vs_other_pct': (avg / other_avg - 1) * 100 if other_avg else None,
            'url': tag_url([tag_id]),
            'data': [values['months'][month] for month in labels],
            'urls': [tag_url([tag_id], month) for month in labels],
        })
    rows.sort(key=lambda row: (-row['total'], row['name']))
    active_buckets = [bucket for bucket in BUCKETS if bucket[0] != 'both' or group_months['both']]
    return {
        'tagged_total': totals['tagged'], 'other_food_total': totals['other'],
        'tagged_count': totals['tagged_count'], 'other_food_count': totals['other_count'],
        'tagged_avg': tagged_avg, 'other_food_avg': other_avg,
        'tagged_share': totals['tagged'] / total * 100 if total else ZERO,
        'tag_avg_difference_pct': (tagged_avg / other_avg - 1) * 100 if tagged_avg is not None and other_avg else None,
        'tag_rows': rows, 'overlapping_tag_count': overlapping_count,
        'mixed_travel_count': sum(values['count'] for values in group_months['both'].values()),
        'tagged_transactions_url': tag_url(list(tag_map)),
        'food_tag_data': {
            'labels': labels,
            'spend': [
                {'name': name, 'data': [group_months[key][m]['total'] for m in labels]}
                for key, name, _ in active_buckets
            ],
            'averages': [
                {'name': name, 'data': [
                    average(group_months[key][m]['total'], group_months[key][m]['count']) for m in labels
                ]}
                for key, name, _ in active_buckets
            ],
            'group_colors': [color for _, _, color in active_buckets],
            'tags': [{'name': row['name'], 'data': row['data'], 'urls': row['urls']} for row in rows],
            'tag_colors': [row['color'] for row in rows],
        },
    }
