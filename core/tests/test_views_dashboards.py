"""Integration tests for dashboard views."""
import pytest
from django.urls import reverse


DASHBOARD_URLS = [
    'core:dashboard',
    'core:spending_income_dashboard',
    'core:chart_comparison',
    'core:car_dashboard',
    'core:car_gas_dashboard',
    'core:car_parking_dashboard',
    'core:income_salary_dashboard',
    'core:transaction_health_dashboard',
    'core:rule_matching_dashboard',
    'core:default_buckets_dashboard',
]


class TestDashboardSmoke:
    """All dashboard URLs return 200 — catches import/ORM errors."""

    @pytest.mark.parametrize('url_name', DASHBOARD_URLS)
    def test_dashboard_empty(self, auth_client, url_name):
        resp = auth_client.get(reverse(url_name))
        assert resp.status_code == 200, f'{url_name} returned {resp.status_code}'

    @pytest.mark.parametrize('url_name', DASHBOARD_URLS)
    def test_dashboard_with_data(self, auth_client, sample_data, url_name):
        resp = auth_client.get(reverse(url_name))
        assert resp.status_code == 200, f'{url_name} returned {resp.status_code}'


class TestDashboardQueryParams:
    """Dashboards handle query parameters without errors."""

    def test_currency_toggle(self, auth_client, sample_data):
        resp = auth_client.get(reverse('core:dashboard'), {'display_currency': 'USD'})
        assert resp.status_code == 200

    def test_time_group(self, auth_client, sample_data):
        resp = auth_client.get(reverse('core:dashboard'), {'time_group': 'weekly'})
        assert resp.status_code == 200

    def test_date_range(self, auth_client, sample_data):
        resp = auth_client.get(reverse('core:dashboard'), {
            'start_date': '2025-01-01',
            'end_date': '2025-12-31',
        })
        assert resp.status_code == 200

class TestSpendingIncomeLevel:
    """The Expense page can roll categories up to a tree level."""

    def _breakdown(self, resp):
        import json
        data = json.loads(resp.context['expense_category_data'])
        return dict(zip(data['labels'], data['values']))

    def test_level_rolls_up_to_ancestor(self, auth_client, sample_data, expense_category, user):
        from core.models import CategoryNode
        parent = CategoryNode.objects.create(name='Food', group=expense_category.group, user=user, color='#111111')
        expense_category.parent = parent
        expense_category.save()
        url = reverse('core:spending_income_dashboard')
        base = {'period_type': 'all'}

        detailed = self._breakdown(auth_client.get(url, base))
        assert list(detailed) == ['Groceries']

        level1 = self._breakdown(auth_client.get(url, {**base, 'level': 1}))
        assert list(level1) == ['Food']
        assert level1['Food'] == detailed['Groceries']

        level2 = self._breakdown(auth_client.get(url, {**base, 'level': 2}))
        assert level2 == detailed

    def test_invalid_level_falls_back_to_detailed(self, auth_client, sample_data):
        url = reverse('core:spending_income_dashboard')
        for bad in ('0', '99', 'abc'):
            resp = auth_client.get(url, {'period_type': 'all', 'level': bad})
            assert resp.status_code == 200
            assert resp.context['category_level'] == 0

    def test_drill_data_lists_children_and_direct_amounts(self, auth_client, sample_data, expense_category, user):
        import json
        from core.models import CategoryNode
        parent = CategoryNode.objects.create(name='Food', group=expense_category.group, user=user, color='#111111')
        expense_category.parent = parent
        expense_category.save()
        for t in sample_data['transactions']:
            t.amount_crc = t.amount
            t.save()
        resp = auth_client.get(reverse('core:spending_income_dashboard'), {'period_type': 'all', 'level': 1})
        drill = json.loads(resp.context['expense_drill_data'])
        entry = drill[str(parent.pk)]
        assert entry['name'] == 'Food'
        assert entry['labels'] == ['Groceries']
        assert entry['ids'] == [expense_category.pk]
        top = json.loads(resp.context['expense_category_data'])
        assert top['ids'] == [parent.pk]
        assert entry['values'][0] == top['values'][0]
