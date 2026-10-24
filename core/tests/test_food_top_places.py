from datetime import date
from decimal import Decimal
from html import unescape
from urllib.parse import parse_qs

import pytest
from django.urls import reverse

from core.tests.factories import CategoryNodeFactory, CurrencyLedgerFactory, LogicalTransactionFactory, UserFactory


@pytest.fixture
def food_places(user):
    ledger = CurrencyLedgerFactory(
        user=user, statement_import__user=user, statement_import__account__user=user,
    )
    food = CategoryNodeFactory(user=user, name='Food')
    restaurants = CategoryNodeFactory(user=user, name='Restaurants', parent=food)
    cafes = CategoryNodeFactory(user=user, name='Cafes', parent=restaurants)
    groceries = CategoryNodeFactory(user=user, name='Groceries', parent=food)
    empty = CategoryNodeFactory(user=user, name='Empty', parent=food)

    def transaction(node, description, value, when=date(2025, 1, 15), usd=True):
        return LogicalTransactionFactory(
            user=user, category_v2=node, description=description, date=when,
            raw_transaction__user=user, raw_transaction__ledger=ledger,
            amount=-Decimal(value), amount_crc=-Decimal(value) * 100,
            amount_usd=-Decimal(value) if usd else None,
        )

    transaction(restaurants, 'Shared place', 10)
    transaction(cafes, 'Shared place', 30)
    transaction(groceries, 'Shared place', 5)
    transaction(cafes, 'Cafe', 20)
    transaction(groceries, 'Market', 100)
    transaction(food, 'Direct Food', 7)
    transaction(restaurants, 'Old restaurant', 900, date(2024, 1, 15))
    transaction(restaurants, 'Missing conversion', 1, usd=False)
    return {
        'food': food, 'restaurants': restaurants, 'cafes': cafes, 'groceries': groceries,
        'empty': empty, 'transaction': transaction,
    }


def dashboard(client, **params):
    return client.get(reverse('core:food_dashboard'), {
        'period_type': 'month', 'period': '2025-01', 'display_currency': 'USD', **params,
    })


@pytest.mark.parametrize('currency,factor', [('USD', 1), ('CRC', 100)])
def test_category_filter_only_changes_top_places(auth_client, food_places, currency, factor):
    baseline = dashboard(auth_client, display_currency=currency)
    selected = dashboard(
        auth_client, merchant_category=food_places['restaurants'].pk, display_currency=currency,
    )
    assert selected.status_code == 200
    rows = {row['description']: row for row in selected.context['top_merchants']}
    assert rows['Shared place']['total'] == 40 * factor
    assert rows['Shared place']['count'] == 2
    assert rows['Shared place']['avg'] == 20 * factor
    assert rows['Cafe']['total'] == 20 * factor
    assert 'Market' not in rows and 'Old restaurant' not in rows and 'Direct Food' not in rows
    assert ('Missing conversion' in rows) == (currency == 'CRC')
    assert selected.context['food_total'] == baseline.context['food_total']
    assert selected.context['food_count_total'] == baseline.context['food_count_total']
    for key in ('food_last_month', 'food_avg_monthly', 'food_median_monthly', 'food_last_month_count'):
        assert selected.context[key] == baseline.context[key]
    default_rows = baseline.context['top_merchants']
    assert default_rows[0]['description'] == 'Market'
    shared = next(row for row in default_rows if row['description'] == 'Shared place')
    assert shared['total'] == 45 * factor and shared['count'] == 3
    assert baseline.context['selected_merchant_category'] == 'all'


def test_nested_category_options_exclude_food_other(auth_client, food_places):
    response = dashboard(auth_client, merchant_category=food_places['cafes'].pk)
    assert [(row['description'], row['total']) for row in response.context['top_merchants']] == [
        ('Shared place', 30), ('Cafe', 20),
    ]
    options = {option['value']: option for option in response.context['food_merchant_categories']}
    assert options[str(food_places['cafes'].pk)]['name'] == 'Restaurants / Cafes'
    assert options[str(food_places['empty'].pk)]['name'] == 'Empty'
    assert str(food_places['food'].pk) not in options
    assert 'Food (other)</option>' not in response.content.decode()
    all_categories = dashboard(auth_client)
    assert any(row['description'] == 'Direct Food' for row in all_categories.context['top_merchants'])


def test_empty_category_keeps_selector_available(auth_client, food_places):
    response = dashboard(auth_client, merchant_category=food_places['empty'].pk)
    assert response.context['top_merchants'] == []
    html = response.content.decode()
    assert 'id="foodMerchantCategory"' in html
    assert 'No food expenses in this sub-category for the selected period.' in html
    assert f'value="{food_places["empty"].pk}" selected' in html
    reset = dashboard(auth_client, merchant_category='all')
    assert len(reset.context['top_merchants']) == 4


def test_period_currency_and_form_preserve_selection(auth_client, food_places):
    selected = str(food_places['restaurants'].pk)
    response = dashboard(auth_client, merchant_category=selected)
    context = response.context
    assert parse_qs(context['category_currency_params']) == {
        'period_type': ['month'], 'period': ['2025-01'], 'merchant_category': [selected],
    }
    assert context['period_extra_params'] == f'merchant_category={selected}'
    html = unescape(response.content.decode())
    assert f'?display_currency=CRC&period_type=month&period=2025-01&merchant_category={selected}' in html
    assert f'?display_currency=USD&period_type=all&merchant_category={selected}' in html
    assert 'action="/food/#foodTopPlaces"' in html
    for name, value in [('display_currency', 'USD'), ('period_type', 'month'), ('period', '2025-01')]:
        assert f'name="{name}" value="{value}"' in html
    all_time = dashboard(auth_client, merchant_category=selected, period_type='all')
    assert all_time.context['top_merchants'][0]['description'] == 'Old restaurant'


@pytest.mark.parametrize('invalid', ['garbage', '-1', '999999999999999999999'])
def test_invalid_category_warns_and_resets(auth_client, food_places, invalid):
    response = dashboard(auth_client, merchant_category=invalid)
    assert response.status_code == 200
    assert response.context['selected_merchant_category'] == 'all'
    assert 'Invalid food sub-category' in response.content.decode()
    assert response.context['top_merchants'][0]['description'] == 'Market'


def test_foreign_and_nonfood_categories_are_not_selectable(auth_client, food_places, user):
    foreign_user = UserFactory()
    foreign_food = CategoryNodeFactory(user=foreign_user, name='Food')
    foreign = CategoryNodeFactory(user=foreign_user, parent=foreign_food, name='Private restaurant')
    nonfood = CategoryNodeFactory(user=user, name='Car')
    for node in (foreign, nonfood):
        response = dashboard(auth_client, merchant_category=node.pk)
        assert response.context['selected_merchant_category'] == 'all'
        assert 'Invalid food sub-category' in response.content.decode()
        assert str(node.pk) not in [option['value'] for option in response.context['food_merchant_categories']]
    foreign_tx = LogicalTransactionFactory(
        user=foreign_user, category_v2=food_places['restaurants'], description='Private place',
        date=date(2025, 1, 15), amount_usd=-999999, amount_crc=-999999,
    )
    response = dashboard(auth_client, merchant_category=food_places['restaurants'].pk)
    assert foreign_tx.description not in response.content.decode()


def test_top_ten_ranking_is_applied_after_category_filter(auth_client, food_places):
    for index in range(12):
        food_places['transaction'](food_places['groceries'], f'Store {index}', 1000 + index)
    response = dashboard(auth_client, merchant_category=food_places['groceries'].pk)
    rows = response.context['top_merchants']
    assert len(rows) == 10
    assert [row['description'] for row in rows] == [f'Store {index}' for index in range(11, 1, -1)]
    restaurants = dashboard(auth_client, merchant_category=food_places['restaurants'].pk)
    assert restaurants.context['top_merchants'][0]['description'] == 'Shared place'


def test_empty_food_still_allows_selection(auth_client, user):
    food = CategoryNodeFactory(user=user, name='Food')
    child = CategoryNodeFactory(user=user, name='Restaurants', parent=food)
    response = dashboard(auth_client, merchant_category=child.pk)
    assert response.status_code == 200
    assert response.context['selected_merchant_category'] == str(child.pk)
    assert 'id="foodMerchantCategory"' in response.content.decode()
    assert response.context['top_merchants'] == []


def test_top_places_selector_does_not_change_travel_copy(auth_client, food_places):
    baseline = auth_client.get(reverse('core:food_tags_dashboard'), {'period_type': 'all'})
    selected = auth_client.get(reverse('core:food_tags_dashboard'), {
        'period_type': 'all', 'merchant_category': food_places['restaurants'].pk,
    })
    assert selected.context['food_tag_data'] == baseline.context['food_tag_data']
    assert selected.context['top_merchants'] == baseline.context['top_merchants']
    assert 'id="foodMerchantCategory"' not in selected.content.decode()


@pytest.mark.parametrize('selection', ['restaurants', 'empty'])
def test_partial_response_contains_only_table(auth_client, food_places, selection):
    params = {'merchant_category': food_places[selection].pk}
    full = dashboard(auth_client, **params)
    partial = dashboard(auth_client, partial='top-places', **params)
    assert partial.status_code == 200
    assert partial.context['top_merchants'] == full.context['top_merchants']
    html = partial.content.decode()
    assert f'data-selected-category="{food_places[selection].pk}"' in html
    assert '<table' in html
    assert '<html' not in html
    assert 'foodSpendChart' not in html
    assert 'foodTopPlacesForm' not in html


def test_partial_invalid_selection_displays_warning(auth_client, food_places):
    response = dashboard(auth_client, partial='top-places', merchant_category='invalid')
    html = response.content.decode()
    assert 'data-selected-category="all"' in html
    assert 'role="alert"' in html
    assert 'Invalid food sub-category' in html


def test_partial_requires_authentication(client, db):
    response = client.get(reverse('core:food_dashboard'), {'partial': 'top-places'})
    assert response.status_code == 302
    assert '/auth/login/' in response.url


def test_top_places_sortable_headers_and_unformatted_numeric_values(auth_client, food_places):
    food_places['transaction'](food_places['cafes'], 'Decimal place', '1234.56')
    for partial in ('', 'top-places'):
        response = dashboard(auth_client, partial=partial)
        html = response.content.decode()
        for key in ('count', 'avg', 'total'):
            assert f'data-place-sort="{key}"' in html
        assert 'aria-sort="descending"' in html
        assert 'data-count="1" data-avg="1234.56" data-total="1234.56"' in html


def test_subcategory_table_removed_but_charts_and_top_places_remain(auth_client, food_places):
    html = dashboard(auth_client).content.decode()
    assert 'Sub Categories' not in html
    assert 'View Transactions' not in html
    assert html.count('<table') == 1
    for chart_id in ('foodSpendChart', 'foodBreakdownChart', 'foodFreqChart'):
        assert f'id="{chart_id}"' in html
    assert 'id="foodTopPlaces"' in html


@pytest.mark.parametrize('period_type,expected_count', [('month', 6), ('all', 7)])
def test_events_period_total_transactions(auth_client, food_places, period_type, expected_count):
    response = dashboard(
        auth_client, period_type=period_type, merchant_category=food_places['groceries'].pk,
    )
    assert response.context['food_count_total'] == expected_count
    events = response.content.decode().split('>Events</h5>', 1)[1]
    card = events.split('id="foodPeriodTransactionCount"', 1)[1].split('</div>', 1)[0]
    assert f'{expected_count} transactions' in card
    assert f'Total · {response.context["period_label"]}' in card
