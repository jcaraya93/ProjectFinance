import json
from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest
from django.urls import reverse

from core.models import CategoryGroup, CategoryNode
from core.tests.factories import LogicalTransactionFactory


@pytest.mark.parametrize('currency,factor', [('CRC', 100), ('USD', 1)])
def test_bonus_period_filters_cards_events_and_transactions(auth_client, user, monkeypatch, currency, factor):
    from core.views import dashboards

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2025, 4, 15)
    monkeypatch.setattr(dashboards, 'date', FixedDate)
    group = CategoryGroup.get_group('income')
    targets = set()
    for role in ('bonus', 'association', 'government'):
        node = CategoryNode.objects.create(user=user, group=group, name=role, income_dashboard_role=role)
        for when, amount in ((date(2025, 1, 14), 99), (date(2025, 1, 15), 1),
                             (date(2025, 4, 15), 2), (date(2025, 4, 16), 99)):
            tx = LogicalTransactionFactory(user=user, category_v2=node, date=when,
                                            amount=amount * 100, amount_crc=amount * 100, amount_usd=amount)
            if amount != 99:
                targets.add(tx.pk)
    url = reverse('core:income_bonus_dashboard')
    default = auth_client.get(url, {'display_currency': currency})
    assert default.context['period_type'] == 'all'
    assert default.context['extra_combined'] == 603 * factor
    assert 'start_date' not in parse_qs(urlsplit(default.context['bonus_transactions_url']).query)
    response = auth_client.get(url, {'display_currency': currency, 'period_type': 'quarter', 'period': 'last-3-months'})
    for key in ('bonus_total', 'association_total', 'goverment_total'):
        assert response.context[key] == 3 * factor
    assert response.context['extra_combined'] == 9 * factor
    assert len(response.context['extra_events']) == 6
    assert sum(event['amount'] for event in json.loads(response.context['extra_events_json'])) == 9 * factor
    assert 'id="bonusFilters"' in response.content.decode()
    assert response.context['bonus_currency_params'] == 'period_type=quarter&period=last-3-months'
    link = response.context['bonus_transactions_url']
    query = parse_qs(urlsplit(link).query)
    assert query['start_date'] == ['2025-01-15'] and query['end_date'] == ['2025-04-15']
    transactions = auth_client.get(link)
    assert {tx.pk for tx in transactions.context['page_obj']} == targets
    assert transactions.context['return_to'] == response.wsgi_request.get_full_path()
    month = auth_client.get(url, {'period_type': 'month', 'period': '2025-01', 'display_currency': currency})
    assert month.context['extra_combined'] == 300 * factor


def test_empty_bonus_dashboard_defaults_all_time(auth_client):
    response = auth_client.get(reverse('core:income_bonus_dashboard'))
    assert response.context['period_type'] == 'all'
    assert response.context['extra_combined'] == 0
    assert response.context['extra_events'] == []
    assert 'for this period' in response.content.decode()
