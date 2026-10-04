import json
from datetime import date
from html import unescape
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from django.urls import reverse

from core.models import CategoryGroup, CategoryNode
from core.tests.factories import LogicalTransactionFactory


@pytest.mark.parametrize('currency,multiplier', [('CRC', 100), ('USD', 1)])
@pytest.mark.parametrize('grouping', ['monthly', 'biweekly'])
def test_salary_period_filters_entire_page(auth_client, user, monkeypatch, currency, multiplier, grouping):
    from core.views import dashboards

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2025, 4, 15)
    monkeypatch.setattr(dashboards, 'date', FixedDate)
    node = CategoryNode.objects.create(user=user, group=CategoryGroup.get_group('income'),
                                       name='Renamed Pay', income_dashboard_role='salary')
    transactions = []
    for when, value in ((date(2024, 4, 14), 99), (date(2024, 4, 15), 1),
                        (date(2025, 4, 14), 2), (date(2025, 4, 15), 3), (date(2025, 4, 16), 99)):
        transactions.append(LogicalTransactionFactory(user=user, category_v2=node, date=when,
                                                       amount=value * 100, amount_crc=value * 100, amount_usd=value))
    url = reverse('core:income_salary_dashboard')
    params = {'display_currency': currency, 'time_group': grouping}
    response = auth_client.get(url, params)
    context = response.context
    assert context['period_key'] == 'last-12-months'
    assert context['start_date'] == date(2024, 4, 15)
    assert context['end_date'] == date(2025, 4, 15)
    assert context['salary_total'] == 6 * multiplier
    assert context['last_month_total'] == 5 * multiplier
    assert context['avg_monthly'] == context['median_monthly'] == 3 * multiplier
    data = json.loads(context['trend_data'])
    assert sum(data['values']) == context['salary_total']
    expected = ['2024-04', '2025-04'] if grouping == 'monthly' else ['2024-04-15', '2025-04-01', '2025-04-15']
    assert data['labels'] == expected
    tx_response = auth_client.get(context['salary_transactions_url'])
    assert {tx.pk for tx in tx_response.context['page_obj']} == {tx.pk for tx in transactions[1:4]}
    assert tx_response.context['return_to'] == response.wsgi_request.get_full_path()

    content = response.content.decode()
    assert 'id="salaryFilters"' in content
    assert 'deltaBarChart' not in content
    assert 'Difference from Median' not in content
    links = [unescape(link) for link in re.findall(r'href="([^"]+)"', content)]
    period_links = [link for link in links if link.startswith('?display_currency=') and 'period_type=' in link]
    assert period_links
    for link in period_links:
        query = parse_qs(urlsplit(link).query)
        if query.get('time_group') == [grouping]:
            assert query['display_currency'][0] in ('CRC', 'USD')
    assert f'time_group={grouping}' in context['salary_currency_params']
    assert 'period=last-12-months' in context['salary_grouping_params']

    month = auth_client.get(url, {**params, 'period_type': 'month', 'period': '2025-04'})
    assert month.context['salary_total'] == 104 * multiplier
    assert json.loads(month.context['trend_data'])['values']
    all_time = auth_client.get(url, {**params, 'period_type': 'all'})
    assert all_time.context['salary_total'] == 204 * multiplier
    assert 'start_date' not in parse_qs(urlsplit(all_time.context['salary_transactions_url']).query)


def test_salary_empty_and_invalid_grouping(auth_client):
    response = auth_client.get(reverse('core:income_salary_dashboard'), {'time_group': 'bad'})
    assert response.context['time_group'] == 'biweekly'
    assert response.context['salary_total'] == 0
    assert json.loads(response.context['trend_data'])['values'] == []
    assert 'Invalid salary chart grouping' in response.content.decode()
