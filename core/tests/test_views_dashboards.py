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

        default = self._breakdown(auth_client.get(url, base))
        assert list(default) == ['Food']  # level 1 is the default

        detailed = self._breakdown(auth_client.get(url, {**base, 'level': 2}))
        assert list(detailed) == ['Groceries']

        level1 = self._breakdown(auth_client.get(url, {**base, 'level': 1}))
        assert list(level1) == ['Food']
        assert level1['Food'] == detailed['Groceries']


    def test_invalid_level_falls_back_to_level_one(self, auth_client, sample_data):
        url = reverse('core:spending_income_dashboard')
        for bad in ('0', '3', '99', 'abc'):
            resp = auth_client.get(url, {'period_type': 'all', 'level': bad})
            assert resp.status_code == 200
            assert resp.context['category_level'] == 1

    def test_transactions_link_carries_period_and_group(self, auth_client, sample_data):
        url = reverse('core:spending_income_dashboard')
        resp = auth_client.get(url, {'period_type': 'all'})
        assert resp.context['expense_transactions_url'] == reverse('core:transaction_list') + '?group=expense'
        resp = auth_client.get(url)
        link = resp.context['expense_transactions_url']
        assert 'group=expense' in link and 'start_date=' in link and 'end_date=' in link
        assert 'group=expense' in resp.context['expense_transactions_url']

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

    def test_summary_total_change_and_unclassified(self, auth_client, sample_data, expense_category, user):
        from datetime import date
        from core.models import CategoryNode
        unclassified = CategoryNode.objects.get(user=user, name='Unclassified', group=expense_category.group)
        txns = sample_data['transactions']
        for t in txns:
            t.amount_crc = t.amount  # normalized negative amounts; the summary uses absolute values
            t.save()
        txns[4].category_v2 = unclassified
        txns[4].save()
        resp = auth_client.get(reverse('core:spending_income_dashboard'), {'period_type': 'month', 'period': '2025-02'})
        sm = resp.context['expense_summary']
        total = sum(abs(float(t.amount_crc)) for t in txns)
        assert sm['total'] == total
        assert sm['unclassified'] == abs(float(txns[4].amount_crc))
        assert round(sm['unclassified_pct'], 4) == round(sm['unclassified'] / total * 100, 4)
        assert sm['median'] == total
        assert sm['median_label'] == 'Median Month'
        assert sm['change_pct'] == 0

    def test_last_12_months_year_filter(self, auth_client, sample_data):
        from datetime import date, timedelta
        from core.views.dashboards import _months_before

        today = date.today()
        rolling_start = _months_before(today, 12)
        previous_start = _months_before(rolling_start, 12)
        transactions = sample_data['transactions']
        for transaction in transactions:
            transaction.date = previous_start - timedelta(days=1)
            transaction.amount_crc = transaction.amount
            transaction.save()
        transactions[0].date = today
        transactions[0].save()
        transactions[1].date = previous_start
        transactions[1].save()

        response = auth_client.get(reverse('core:spending_income_dashboard'), {
            'period_type': 'year',
            'period': 'last-12-months',
        })

        assert response.status_code == 200
        assert response.context['period_label'] == 'Last 12 Months'
        assert response.context['period_years'][0]['key'] == 'last-12-months'
        assert 'Last 12 Months' in response.content.decode()
        assert response.context['expense_summary']['total'] == abs(float(transactions[0].amount))
        assert response.context['expense_summary']['median'] is not None
        assert response.context['expense_summary']['median_label'] == 'Median Year'

    @pytest.mark.parametrize(
        ('period_type', 'period_key', 'label', 'window_months'),
        [
            ('semester', 'last-6-months', 'Last 6 Months', 6),
            ('quarter', 'last-3-months', 'Last 3 Months', 3),
        ],
    )
    def test_rolling_semester_and_quarter_filters(
        self, auth_client, sample_data, period_type, period_key, label, window_months,
    ):
        from datetime import date, timedelta
        from core.views.dashboards import _months_before

        today = date.today()
        rolling_start = _months_before(today, window_months)
        previous_start = _months_before(rolling_start, window_months)
        transactions = sample_data['transactions']
        for transaction in transactions:
            transaction.date = previous_start - timedelta(days=1)
            transaction.amount_crc = transaction.amount
            transaction.save()
        transactions[0].date = today
        transactions[0].save()
        transactions[1].date = previous_start
        transactions[1].save()

        response = auth_client.get(reverse('core:spending_income_dashboard'), {
            'period_type': period_type,
            'period': period_key,
        })

        assert response.status_code == 200
        assert response.context['period_label'] == label
        assert response.context[f'period_{period_type}s'][0]['key'] == period_key
        assert label in response.content.decode()
        assert response.context['expense_summary']['total'] == abs(float(transactions[0].amount))
        assert response.context['expense_summary']['median'] is not None
        assert response.context['expense_summary']['median_label'] == f'Median {period_type.title()}'


class TestExpenseMedianComparison:
    @pytest.mark.parametrize(
        ('period_type', 'period_key', 'period_months'),
        [
            ('month', '2020-01', 1),
            ('quarter', '2020-Q1', 3),
            ('semester', '2020-H1', 6),
            ('year', '2020', 12),
        ],
    )
    @pytest.mark.parametrize('odd_period_count', [False, True])
    def test_median_of_period_totals(
        self, auth_client, sample_data, period_type, period_key, period_months, odd_period_count,
    ):
        from datetime import date

        offsets = [0, 0, 1, 2, 2 if odd_period_count else 3]
        amounts = [100, 300, 900, 2000, 4000]
        for transaction, offset, amount in zip(sample_data['transactions'], offsets, amounts):
            month_index = offset * period_months * 2
            transaction.date = date(2020 + month_index // 12, month_index % 12 + 1, 1)
            transaction.amount_crc = -amount * 2
            transaction.amount_usd = -amount
            transaction.save()

        expected_median = 900 if odd_period_count else 1450
        for currency, factor in [('CRC', 2), ('USD', 1)]:
            response = auth_client.get(reverse('core:spending_income_dashboard'), {
                'period_type': period_type,
                'period': period_key,
                'display_currency': currency,
            })
            summary = response.context['expense_summary']
            assert summary['total'] == 400 * factor
            assert summary['median'] == expected_median * factor
            assert summary['change_pct'] == pytest.approx((400 - expected_median) / expected_median * 100)
            assert f'vs Median {period_type.title()}' in response.content.decode()
            assert 'median expense' in response.content.decode()
            assert 'previously' not in response.content.decode()

    @pytest.mark.parametrize(
        ('period_type', 'period_key'),
        [
            ('quarter', 'last-3-months'),
            ('semester', 'last-6-months'),
            ('year', 'last-12-months'),
        ],
    )
    def test_rolling_window_uses_calendar_median(
        self, auth_client, sample_data, monkeypatch, period_type, period_key,
    ):
        from datetime import date
        from core.views import dashboards

        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 10, 3)

        monkeypatch.setattr(dashboards, 'date', FixedDate)
        amounts = [100, 300, 900, 2000, 4000]
        for index, (transaction, amount) in enumerate(zip(sample_data['transactions'], amounts)):
            transaction.date = date(2026 if index == 0 else 2020 + index, 10, 3)
            transaction.amount_crc = -amount
            transaction.save()

        response = auth_client.get(reverse('core:spending_income_dashboard'), {
            'period_type': period_type,
            'period': period_key,
        })
        summary = response.context['expense_summary']
        assert summary['total'] == 100
        assert summary['median'] == 900
        assert summary['change_pct'] == pytest.approx((100 - 900) / 900 * 100)
        assert summary['median_label'] == f'Median {period_type.title()}'

    def test_median_excludes_other_users_and_non_expenses(
        self, auth_client, sample_data, income_category,
    ):
        from core.tests.factories import LogicalTransactionFactory, CategoryNodeFactory

        for transaction in sample_data['transactions']:
            transaction.amount_crc = transaction.amount
            transaction.save()
        sample_data['transactions'][4].category_v2 = income_category
        sample_data['transactions'][4].save()
        other_category = CategoryNodeFactory()
        LogicalTransactionFactory(
            user=other_category.user, category_v2=other_category, amount_crc=-1000000,
        )
        response = auth_client.get(reverse('core:spending_income_dashboard'), {
            'period_type': 'month', 'period': '2025-02',
        })
        assert response.context['expense_summary']['median'] == 10000

    def test_zero_median_has_no_percentage(self, auth_client, sample_data):
        for transaction in sample_data['transactions']:
            transaction.amount_crc = 0
            transaction.save()
        response = auth_client.get(reverse('core:spending_income_dashboard'), {
            'period_type': 'month', 'period': '2025-02',
        })
        summary = response.context['expense_summary']
        assert summary['median'] == 0
        assert summary['change_pct'] is None
        assert 'median expense' in response.content.decode()

    def test_empty_rolling_period_has_no_median(self, auth_client):
        response = auth_client.get(reverse('core:spending_income_dashboard'), {
            'period_type': 'year', 'period': 'last-12-months',
        })
        summary = response.context['expense_summary']
        assert summary['median'] is None
        assert summary['change_pct'] is None
        assert 'No expense data for comparison' in response.content.decode()

    def test_all_time_has_no_median_comparison(self, auth_client, sample_data):
        response = auth_client.get(reverse('core:spending_income_dashboard'), {'period_type': 'all'})
        summary = response.context['expense_summary']
        assert summary['median'] is None
        assert summary['median_label'] is None
        assert summary['change_pct'] is None
        assert 'Select a month, quarter, semester, or year' in response.content.decode()
