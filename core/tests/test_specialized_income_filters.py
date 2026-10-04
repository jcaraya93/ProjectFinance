import json
from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest
from django.urls import reverse

from core.models import CategoryGroup, CategoryNode
from core.tests.factories import LogicalTransactionFactory


@pytest.mark.parametrize('role,route', [
    ('reimbursement', 'reimbursement_overview_dashboard'),
    ('bank', 'bank_income_overview_dashboard'),
])
@pytest.mark.parametrize('currency,factor', [('CRC', 100), ('USD', 1)])
def test_income_period_filters_all_outputs(auth_client, user, monkeypatch, role, route, currency, factor):
    from core.views import dashboards

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2025, 4, 15)
    monkeypatch.setattr(dashboards, 'date', FixedDate)
    node = CategoryNode.objects.create(user=user, group=CategoryGroup.get_group('income'),
                                       name='Renamed Income', income_dashboard_role=role)
    targets = set()
    for when, value in ((date(2024, 4, 14), 99), (date(2024, 4, 15), 1),
                        (date(2025, 4, 15), 2), (date(2025, 4, 16), 99)):
        tx = LogicalTransactionFactory(user=user, category_v2=node, date=when,
                                        amount=value * 100, amount_crc=value * 100, amount_usd=value)
        if value != 99:
            targets.add(tx.pk)
    url = reverse('core:' + route)
    response = auth_client.get(url, {'display_currency': currency})
    context = response.context
    assert context['period_key'] == 'last-12-months'
    assert context['all_time_total'] == 3 * factor
    assert context['last_month_total'] == 2 * factor
    assert context['avg_monthly'] == context['median_monthly'] == 1.5 * factor
    assert sum(json.loads(context['breakdown_data'])['values']) == 3 * factor
    stacked = json.loads(context['stacked_data'])
    assert sum(sum(series['data']) for series in stacked['series']) == 3 * factor
    counts = json.loads(context['count_data'])
    assert sum(sum(series['data']) for series in counts['series']) == 2
    assert 'id="incomeFilters"' in response.content.decode()
    assert context['income_currency_params'] == 'period_type=year&period=last-12-months'
    query = parse_qs(urlsplit(context['income_transactions_url']).query)
    assert query['start_date'] == ['2024-04-15'] and query['end_date'] == ['2025-04-15']
    transactions = auth_client.get(context['income_transactions_url'])
    assert {tx.pk for tx in transactions.context['page_obj']} == targets
    assert transactions.context['return_to'] == response.wsgi_request.get_full_path()
    all_time = auth_client.get(url, {'period_type': 'all', 'display_currency': currency})
    assert all_time.context['all_time_total'] == 201 * factor
    month = auth_client.get(url, {'period_type': 'month', 'period': '2025-04', 'display_currency': currency})
    assert month.context['all_time_total'] == 101 * factor


@pytest.mark.parametrize('route', ['reimbursement_overview_dashboard', 'bank_income_overview_dashboard'])
def test_empty_income_period(auth_client, route):
    response = auth_client.get(reverse('core:' + route))
    assert response.status_code == 200
    assert response.context['all_time_total'] == 0
    assert response.context['last_month_total'] == 0
    assert response.context['period_key'] == 'last-12-months'
