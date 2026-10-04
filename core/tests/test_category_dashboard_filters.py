import json
from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest
from django.urls import reverse

from core.models import CategoryGroup, CategoryNode
from core.tests.factories import LogicalTransactionFactory


@pytest.mark.parametrize('route,name,total_key,data_key', [
    ('car_dashboard', 'Car Maintenance', 'car_last_year', 'trend_data'),
    ('car_gas_dashboard', 'Car Gas', 'gas_last_year', 'gas_data'),
    ('car_parking_dashboard', 'Car Parking & Toll', 'park_last_year', 'parking_data'),
])
@pytest.mark.parametrize('currency,factor', [('CRC', 100), ('USD', 1)])
def test_category_period_filters_all_outputs(
    auth_client, user, monkeypatch, route, name, total_key, data_key, currency, factor,
):
    from core.views import dashboards

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2025, 4, 15)

    monkeypatch.setattr(dashboards, 'date', FixedDate)
    node = CategoryNode.objects.create(user=user, group=CategoryGroup.get_group('expense'), name=name)
    targets = set()
    for when, value in ((date(2024, 4, 14), 99), (date(2024, 4, 15), 1),
                        (date(2025, 4, 15), 2), (date(2025, 4, 16), 99)):
        tx = LogicalTransactionFactory(user=user, category_v2=node, date=when, description='Selected location',
                                       amount=-value * 100, amount_crc=-value * 100, amount_usd=-value)
        if value != 99:
            targets.add(tx.pk)
    response = auth_client.get(reverse('core:' + route), {'display_currency': currency})
    assert response.status_code == 200
    context = response.context
    assert context['period_key'] == 'last-12-months'
    assert context[total_key] == 3 * factor
    payload = json.loads(context[data_key])
    assert payload['labels'] == ['2024-04', '2025-04']
    if route == 'car_dashboard':
        assert sum(sum(values) for values in payload['datasets'].values()) == 3 * factor
        assert context['maint_12m_total'] == 3 * factor
        assert context['maint_12m_count'] == 2
        assert [event['amount'] for event in context['periodic_timeline']] == [2 * factor, factor]
        assert sum(row['total'] for row in context['table_rows']) == 3 * factor
    elif route == 'car_gas_dashboard':
        assert sum(payload['spend']) == 3 * factor
        assert context['gas_count_last_year'] == 2
        assert context['all_fillup_avg'] == 1.5 * factor
        assert len(context['fillup_table']) == 2
    else:
        assert sum(sum(series['data']) for series in payload['spend_datasets']) == 3 * factor
        assert context['park_count_last_year'] == 2
        assert context['park_locations'][0]['total'] == 3 * factor
        assert context['park_locations'][0]['count'] == 2
    assert 'id="categoryFilters"' in response.content.decode()
    assert context['category_currency_params'] == 'period_type=year&period=last-12-months'
    query = parse_qs(urlsplit(context['category_transactions_url']).query)
    assert query['start_date'] == ['2024-04-15']
    assert query['end_date'] == ['2025-04-15']
    transactions = auth_client.get(context['category_transactions_url'])
    assert {tx.pk for tx in transactions.context['page_obj']} == targets
    assert transactions.context['return_to'] == response.wsgi_request.get_full_path()
    all_time = auth_client.get(reverse('core:' + route), {'period_type': 'all', 'display_currency': currency})
    assert all_time.context[total_key] == 201 * factor
    month = auth_client.get(reverse('core:' + route), {
        'period_type': 'month', 'period': '2025-04', 'display_currency': currency,
    })
    assert month.context[total_key] == 101 * factor


def test_car_all_time_totals_include_more_than_twelve_months(auth_client, user):
    expense = CategoryGroup.get_group('expense')
    income = CategoryGroup.get_group('income')
    gas = CategoryNode.objects.create(user=user, group=expense, name='Car Gas')
    maintenance = CategoryNode.objects.create(user=user, group=expense, name='Car Maintenance')
    salary = CategoryNode.objects.create(user=user, group=income, name='Renamed salary', income_dashboard_role='salary')
    for year, month in [(2023, month) for month in range(1, 13)] + [(2024, 1)]:
        for node, value in ((gas, -10), (maintenance, -20), (salary, 100)):
            LogicalTransactionFactory(user=user, category_v2=node, date=date(year, month, 15),
                                      amount=value, amount_crc=value, amount_usd=value)
    response = auth_client.get(reverse('core:car_dashboard'), {'period_type': 'all'})
    context = response.context
    assert context['car_last_year'] == 390
    assert context['running_last_year'] == 130
    assert context['ownership_last_year'] == 260
    assert context['maint_12m_total'] == 260
    assert context['maint_12m_count'] == 13
    january = auth_client.get(reverse('core:car_dashboard'), {'period_type': 'month', 'period': '2024-01'})
    assert january.context['last_month_salary'] == 100
    assert january.context['salary_pct'] == 30
    assert len(january.context['periodic_timeline']) == 1


@pytest.mark.parametrize('route,total_key', [
    ('car_dashboard', 'car_last_year'),
    ('car_gas_dashboard', 'gas_last_year'),
    ('car_parking_dashboard', 'park_last_year'),
])
def test_empty_category_period(auth_client, route, total_key):
    response = auth_client.get(reverse('core:' + route))
    assert response.status_code == 200
    assert response.context[total_key] == 0
    assert response.context['period_key'] == 'last-12-months'
    assert response.context['category_transactions_url'] == ''
