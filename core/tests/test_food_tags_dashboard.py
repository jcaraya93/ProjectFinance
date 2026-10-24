import json
from datetime import date
from decimal import Decimal
from html import unescape
from urllib.parse import parse_qs, urlsplit

import pytest
from django.urls import reverse

from core.models import Tag, TagGroup
from core.tests.factories import CategoryNodeFactory, CurrencyLedgerFactory, LogicalTransactionFactory, UserFactory


@pytest.fixture
def food_tags(user):
    ledger = CurrencyLedgerFactory(
        user=user, statement_import__user=user, statement_import__account__user=user,
    )
    food = CategoryNodeFactory(user=user, name='Food')
    restaurants = CategoryNodeFactory(user=user, name='Restaurants', parent=food)
    cafes = CategoryNodeFactory(user=user, name='Cafes', parent=restaurants)
    trips = TagGroup.objects.create(user=user, name='Travel Local')
    international = TagGroup.objects.create(user=user, name='Travel International')
    work = TagGroup.objects.create(user=user, name='Work')
    beach = Tag.objects.create(user=user, group=trips, name='Beach', color='#123456')
    city = Tag.objects.create(user=user, group=international, name='City')
    business = Tag.objects.create(user=user, group=work, name='Business')
    ungrouped = Tag.objects.create(user=user, name='Weekend')

    def transaction(value, month=1, tags=(), category=cafes, **kwargs):
        tx = LogicalTransactionFactory(
            user=user, category_v2=category, date=date(2025, month, 15),
            raw_transaction__user=user, raw_transaction__ledger=ledger,
            amount=-Decimal(value), amount_crc=-Decimal(value) * 100,
            amount_usd=-Decimal(value), **kwargs,
        )
        tx.tags.add(*tags)
        return tx

    transactions = [
        transaction('10', tags=[beach, city]),
        transaction('20', tags=[beach]),
        transaction('5', tags=[business]),
        transaction('5', category=food),
        transaction('30', month=2, tags=[city]),
        transaction('10', month=2, tags=[ungrouped]),
    ]
    return {
        'food': food, 'cafes': cafes, 'trips': trips, 'international': international, 'work': work,
        'beach': beach, 'city': city, 'business': business, 'ungrouped': ungrouped,
        'transactions': transactions, 'transaction': transaction,
    }


def get_dashboard(client, **params):
    return client.get(reverse('core:food_tags_dashboard'), {'period_type': 'all', **params})


@pytest.mark.parametrize('currency,factor', [('USD', 1), ('CRC', 100)])
def test_group_totals_charts_averages_and_overlapping_tags(auth_client, food_tags, currency, factor):
    response = get_dashboard(auth_client, tag_group=food_tags['trips'].pk, display_currency=currency)
    assert response.status_code == 200
    context = response.context
    assert context['food_total'] == 80 * factor
    assert context['tagged_total'] == 60 * factor
    assert context['other_food_total'] == 20 * factor
    assert context['tagged_count'] == context['other_food_count'] == 3
    assert context['tagged_share'] == 75
    assert context['tagged_avg'] == 20 * factor
    assert context['other_food_avg'] == Decimal(20 * factor) / 3
    assert round(context['tag_avg_difference_pct']) == 200
    assert context['overlapping_tag_count'] == 1
    data = context['food_tag_data']
    assert data['labels'] == ['2025-01', '2025-02']
    assert [series['name'] for series in data['spend']] == [
        'Travel Local', 'Travel International', 'Travel Local + International', 'Other food',
    ]
    assert data['spend'][0]['data'] == [20 * factor, 0]
    assert data['spend'][1]['data'] == [0, 30 * factor]
    assert data['spend'][2]['data'] == [10 * factor, 0]
    assert data['spend'][3]['data'] == [10 * factor, 10 * factor]
    assert sum(sum(series['data']) for series in data['spend']) == 80 * factor
    assert data['averages'][0]['data'] == [20 * factor, None]
    assert data['averages'][1]['data'] == [None, 30 * factor]
    assert data['averages'][2]['data'] == [10 * factor, None]
    assert data['averages'][3]['data'] == [5 * factor, 10 * factor]
    assert context['mixed_travel_count'] == 1
    rows = {row['name']: row for row in context['tag_rows']}
    assert set(rows) == {'Beach', 'City'}
    assert rows['Beach']['total'] == 30 * factor
    assert rows['City']['total'] == 40 * factor
    assert rows['City']['avg'] == 20 * factor
    assert rows['Beach']['data'] == [30 * factor, 0]
    assert rows['City']['data'] == [10 * factor, 30 * factor]
    assert sum(row['total'] for row in rows.values()) > context['tagged_total']
    content = response.content.decode()
    assert 'id="foodTagSpendChart"' in content
    assert 'id="foodTagAverageChart"' not in content
    assert 'id="foodIndividualTagsChart"' not in content
    assert 'not causes' in content


@pytest.mark.parametrize('selection', ['all', 'ungrouped', 'invalid'])
def test_only_travel_groups_count_even_with_legacy_selection(auth_client, food_tags, selection):
    context = get_dashboard(auth_client, tag_group=selection, display_currency='USD').context
    assert context['tagged_total'] == 60
    assert context['tagged_count'] == 3
    assert context['tagged_total'] + context['other_food_total'] == 80
    assert {row['name'] for row in context['tag_rows']} == {'Beach', 'City'}
    assert context['missing_travel_groups'] == []


def test_original_is_unchanged_and_not_embedded_in_copy(auth_client, food_tags):
    params = {'period_type': 'all', 'display_currency': 'USD'}
    original = auth_client.get(reverse('core:food_dashboard'), params)
    copy = get_dashboard(auth_client, display_currency='USD')
    for key in (
        'food_total', 'food_count_total', 'food_last_month', 'food_last_month_count',
        'food_avg_monthly', 'food_median_monthly', 'food_avg_count_monthly',
        'food_median_count_monthly', 'top_merchants',
    ):
        assert copy.context[key] == original.context[key]
    original_data = json.loads(original.context['food_data'])
    copy_data = json.loads(copy.context['food_data'])
    assert original_data['labels'] == copy_data['labels']
    for kind in ('spend_datasets', 'count_datasets'):
        assert [(ds['label'], ds['data']) for ds in original_data[kind]] == [
            (ds['label'], ds['data']) for ds in copy_data[kind]
        ]
    assert 'id="foodTagSpendChart"' not in original.content.decode()
    assert 'food_tag_data' not in original.context
    assert 'active">Food &middot; Travel</a>' in copy.content.decode()
    for chart_id in ('foodSpendChart', 'foodBreakdownChart', 'foodFreqChart'):
        assert f'id="{chart_id}"' in original.content.decode()
        assert chart_id not in copy.content.decode()
    for removed in (
        'Original Food Overview', 'Monthly Average Cost per Food Transaction',
        'Monthly Food Spend by Trip', 'id="foodData"', 'Top Places by Spend',
    ):
        assert removed not in copy.content.decode()
    assert 'Food by Trip' in copy.content.decode()
    assert 'Open original Food dashboard' in copy.content.decode()


def test_period_and_currency_links(auth_client, food_tags):
    response = get_dashboard(
        auth_client, period_type='month', period='2025-02', display_currency='USD',
    )
    context = response.context
    assert context['tagged_total'] == 30
    assert context['other_food_total'] == 10
    assert context['food_tag_data']['labels'] == ['2025-02']
    assert parse_qs(context['category_currency_params']) == {
        'period_type': ['month'], 'period': ['2025-02'],
    }
    content = unescape(response.content.decode())
    assert 'display_currency=USD&period_type=all' in content
    assert 'display_currency=CRC&period_type=month&period=2025-02' in content
    assert 'foodTagGroup' not in content


def test_drilldowns_filter_exact_transactions_and_restore_dashboard(auth_client, food_tags):
    response = get_dashboard(auth_client, tag_group=food_tags['trips'].pk, display_currency='USD')
    context = response.context
    transactions = food_tags['transactions']
    targets = {transactions[index].pk for index in (0, 1, 4)}
    drilldown = auth_client.get(context['tagged_transactions_url'])
    assert {tx.pk for tx in drilldown.context['page_obj']} == targets
    assert drilldown.context['return_to'] == response.wsgi_request.get_full_path()
    assert 'Food · Travel' in drilldown.content.decode()
    assert drilldown.context['display_currency'] == 'USD'
    for row in context['tag_rows']:
        tag = food_tags[row['name'].lower()]
        expected = {tx.pk for tx in transactions if tx.tags.filter(pk=tag.pk).exists()}
        result = auth_client.get(row['url'])
        assert {tx.pk for tx in result.context['page_obj']} == expected
    city_january_url = context['tag_rows'][0]['urls'][0]
    assert {tx.pk for tx in auth_client.get(city_january_url).context['page_obj']} == {transactions[0].pk}


def test_month_drilldown_clips_to_rolling_period(auth_client, food_tags, monkeypatch):
    from core.views import dashboards

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2025, 2, 20)

    monkeypatch.setattr(dashboards, 'date', FixedDate)
    old = food_tags['transaction']('99', tags=[food_tags['beach']])
    old.date = date(2024, 2, 19)
    old.save()
    boundary = food_tags['transaction']('7', tags=[food_tags['beach']])
    boundary.date = date(2024, 2, 20)
    boundary.save()
    response = auth_client.get(reverse('core:food_tags_dashboard'), {
        'tag_group': food_tags['trips'].pk, 'display_currency': 'USD',
    })
    assert response.context['period_key'] == 'last-12-months'
    assert response.context['tagged_total'] == 67
    url = next(row for row in response.context['tag_rows'] if row['name'] == 'Beach')['urls'][0]
    params = parse_qs(urlsplit(url).query)
    assert params['start_date'] == ['2024-02-20']
    assert params['end_date'] == ['2024-02-29']
    assert {tx.pk for tx in auth_client.get(url).context['page_obj']} == {boundary.pk}


def test_user_category_and_missing_conversion_scoping(auth_client, user, food_tags):
    other = UserFactory()
    other_group = TagGroup.objects.create(user=other, name='Private trips')
    other_tag = Tag.objects.create(user=other, group=other_group, name='Private tag')
    foreign_food = CategoryNodeFactory(user=other, name='Food')
    foreign = LogicalTransactionFactory(
        user=other, category_v2=foreign_food, amount_crc=-99999, amount_usd=-99999,
    )
    foreign.tags.add(other_tag)
    # Even a malformed cross-user tag link must not appear in this user's analysis.
    food_tags['transactions'][3].tags.add(other_tag)
    outside_food = CategoryNodeFactory(user=user, name='Car')
    food_tags['transaction']('999', category=outside_food, tags=[food_tags['beach']])
    LogicalTransactionFactory(
        user=user, category_v2=food_tags['cafes'], amount=-100, amount_crc=-100, amount_usd=None,
    )
    response = get_dashboard(auth_client, display_currency='USD')
    assert response.context['food_total'] == response.context['tagged_total'] + response.context['other_food_total'] == 80
    assert 'Private' not in response.content.decode()
    assert response.context['other_food_total'] == 20


def test_foreign_travel_groups_are_not_included(auth_client, food_tags):
    other = UserFactory()
    group = TagGroup.objects.create(user=other, name='Travel Local')
    tag = Tag.objects.create(user=other, group=group, name='Private')
    food_tags['transactions'][3].tags.add(tag)
    response = get_dashboard(auth_client, tag_group=group.pk, display_currency='USD')
    assert response.context['tagged_total'] == 60
    assert 'Private' not in response.content.decode()


def test_missing_baselines_and_date_ranges_do_not_infer_tags(auth_client, food_tags):
    food_tags['beach'].start_date = date(2025, 1, 1)
    food_tags['beach'].end_date = date(2025, 2, 28)
    food_tags['beach'].save()
    for tx in food_tags['transactions']:
        tx.tags.clear()
    context = get_dashboard(auth_client, display_currency='USD').context
    assert context['tagged_total'] == 0
    assert context['tagged_avg'] is None
    assert context['tag_avg_difference_pct'] is None
    assert context['food_tag_data']['averages'][0]['data'] == [None, None]
    assert context['tag_rows'] == []
    all_tags = list(Tag.objects.filter(user=food_tags['beach'].user))
    for tx in food_tags['transactions']:
        tx.tags.set(all_tags)
    tagged_only = get_dashboard(auth_client, display_currency='USD').context
    assert tagged_only['other_food_avg'] is None
    assert tagged_only['tag_avg_difference_pct'] is None
    assert all(row['vs_other_pct'] is None for row in tagged_only['tag_rows'])


def test_case_insensitive_group_names_and_same_group_overlap(auth_client, food_tags):
    group = food_tags['trips']
    group.name = 'travel local'
    group.save()
    tag = Tag.objects.create(user=group.user, group=group, name='Second local trip')
    food_tags['transactions'][1].tags.add(tag)
    context = get_dashboard(auth_client, display_currency='USD').context
    assert context['tagged_total'] == 60
    assert context['food_tag_data']['spend'][0]['data'] == [20, 0]
    assert context['overlapping_tag_count'] == 2
    assert context['mixed_travel_count'] == 1


def test_missing_groups_are_explicit(auth_client, food_tags):
    food_tags['trips'].delete()
    response = get_dashboard(auth_client, display_currency='USD')
    assert response.context['missing_travel_groups'] == ['Travel Local']
    assert 'Travel tag groups not found: Travel Local' in response.content.decode()
    assert response.context['tagged_total'] == 40
    assert [series['name'] for series in response.context['food_tag_data']['spend']] == [
        'Travel Local', 'Travel International', 'Other food',
    ]


def test_empty_food_and_zero_amount(auth_client, user):
    response = get_dashboard(auth_client)
    assert response.status_code == 200
    assert response.context['food_found'] is False
    assert response.context['food_tag_data']['labels'] == []
    food = CategoryNodeFactory(user=user, name='Food')
    group = TagGroup.objects.create(user=user, name='Travel Local')
    tag = Tag.objects.create(user=user, group=group, name='Zero')
    tx = LogicalTransactionFactory(user=user, category_v2=food, amount=0, amount_crc=0, amount_usd=0)
    tx.tags.add(tag)
    context = get_dashboard(auth_client).context
    assert context['tagged_count'] == 1
    assert context['tagged_avg'] == 0
    assert context['tagged_share'] == 0
    assert context['tag_rows'][0]['total'] == 0
    assert context['tag_avg_difference_pct'] is None


def test_authentication_and_escaped_tag_chart_data(client, auth_client, food_tags):
    assert client.get(reverse('core:food_tags_dashboard')).status_code == 302
    tag = food_tags['beach']
    tag.name = '</script><script>alert("tag")</script>'
    tag.save()
    content = get_dashboard(auth_client).content.decode()
    assert tag.name not in content
    assert '\\u003C/script\\u003E' in content
    assert 'food_tag_charts.js' in content

