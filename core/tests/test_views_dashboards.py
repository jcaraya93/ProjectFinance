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
