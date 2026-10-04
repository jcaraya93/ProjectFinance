"""Integration tests for dashboard views."""
import pytest
from django.urls import reverse


DASHBOARD_URLS = [
    'core:dashboard',
    'core:spending_income_dashboard',
    'core:expense_range_dashboard',
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

    def test_period_buttons_use_styled_toolbar(self, auth_client, sample_data):
        content = auth_client.get(reverse('core:spending_income_dashboard')).content.decode()
        assert 'id="expenseFilters" class="dashboard-filter-toolbar d-flex flex-wrap align-items-center gap-3 p-3 bg-light border rounded mb-3"' in content
        toolbar = content.split('id="expenseFilters"')[1].split('<!-- ═══ SPENDING ═══ -->')[0]
        assert '>Period</span>' in toolbar
        assert '>Level</span>' in toolbar
        assert 'Level 1' in toolbar
        assert 'Level 1' not in content.split('id="expenseFilters"')[0]
        assert '<div class="btn-group btn-group-sm" role="group">' in toolbar
        assert '<select' not in toolbar
        for period in ('month', 'quarter', 'semester', 'year'):
            assert f'period_type={period}' in toolbar

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
        assert '<title>Expense Composition — Project Finance</title>' in resp.content.decode()
        assert '<h4 class="mb-0">Expense Composition</h4>' in resp.content.decode()
        from urllib.parse import parse_qs, urlsplit
        params = parse_qs(urlsplit(resp.context['expense_transactions_url']).query)
        assert params == {'group': ['expense'], 'return_to': [url + '?period_type=all']}
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


class TestExpenseRangePeriods:
    def test_percentiles_are_not_displayed(self, auth_client, sample_data):
        import json

        for transaction in sample_data['transactions']:
            transaction.amount_crc = transaction.amount
            transaction.save()
        response = auth_client.get(reverse('core:expense_range_dashboard'), {'period_type': 'all'})
        content = response.content.decode()
        assert 'P25' not in content
        assert 'P75' not in content
        assert 'Period Median' in content
        for category in json.loads(response.context['expense_data']):
            assert 'p25' not in category
            assert 'p75' not in category

    def test_category_levels_roll_up_before_statistics(self, auth_client, sample_data, expense_category, user):
        import json
        from datetime import date
        from core.models import CategoryNode

        parent = CategoryNode.objects.create(user=user, name='Food', group=expense_category.group, color='#123456')
        expense_category.parent = parent
        expense_category.save()
        grandchild = CategoryNode.objects.create(
            user=user, name='Produce', group=expense_category.group, parent=expense_category,
        )
        transactions = sample_data['transactions']
        assignments = [parent, expense_category, grandchild, expense_category, grandchild]
        for index, (transaction, category) in enumerate(zip(transactions, assignments)):
            transaction.category_v2 = category
            transaction.date = date(2025, 2 if index < 3 else 3, 1)
            transaction.amount_crc = -(index + 1) * 100
            transaction.save()
        url = reverse('core:expense_range_dashboard')
        for level in (1, 2):
            response = auth_client.get(url, {
                'period_type': 'all', 'level': level, 'compare_month': '2025-02',
            })
            data = {row['name']: row for row in json.loads(response.context['expense_data'])}
            assert response.context['compare_total'] == 600
            assert response.context['overall_median'] == 750
            assert 'id="rangeLevel"' in response.content.decode()
            assert "['rangeMonth', 'rangePeriod', 'rangeLevel']" in response.content.decode()
            assert "onchange=\"var url=new URL" not in response.content.decode()
            assert f'&amp;level={level}' in response.content.decode() or f'&level={level}' in response.content.decode()
            if level == 1:
                assert set(data) == {'Food'}
                assert data['Food']['median'] == 750
                assert data['Food']['compare'] == 600
                assert data['Food']['color'] == '#123456'
            else:
                assert set(data) == {'Food', 'Groceries'}
                assert data['Groceries']['median'] == 700
                assert data['Groceries']['compare'] == 500

    @pytest.mark.parametrize('level', [None, '0', '3', 'bad'])
    def test_category_level_defaults_to_one(self, auth_client, level):
        params = {'level': level} if level is not None else {}
        response = auth_client.get(reverse('core:expense_range_dashboard'), params)
        assert response.context['category_level'] == 1

    def test_compare_control_comes_first(self, auth_client):
        content = auth_client.get(reverse('core:expense_range_dashboard')).content.decode()
        toolbar = content.split('id="rangeFilters"')[1].split('<!-- Overall summary cards -->')[0]
        ids = ['rangeMonth', 'rangePeriod', 'rangeLevel', 'rangeSort', 'onlySelectedMonth']
        positions = [toolbar.index(f'id="{field}"') for field in ids]
        assert positions == sorted(positions)
        assert toolbar.count('class="d-flex align-items-center gap-2"') == 4

    def test_selected_month_is_default_sort(self, auth_client):
        content = auth_client.get(reverse('core:expense_range_dashboard')).content.decode()
        assert '<select id="rangeSort"' in content
        assert '<option value="compare" selected>Month amount</option>' in content
        assert '<option value="deviation">Deviation</option>' in content
        assert '<option value="median">Median</option>' in content
        assert "sortSelect.addEventListener('change', refresh);" in content
        assert 'sort-btn' not in content

    def test_default_is_last_12_months(self, auth_client):
        response = auth_client.get(reverse('core:expense_range_dashboard'))
        assert response.context['period_type'] == 'year'
        assert response.context['period_key'] == 'last-12-months'
        assert response.context['period_label'] == 'Last 12 Months'
        content = response.content.decode()
        assert content.index('<option value="last-12-months" selected>') < content.index('<option value="all"')

    def test_explicit_all_time_overrides_default(self, auth_client, sample_data):
        for transaction in sample_data['transactions']:
            transaction.amount_crc = transaction.amount
            transaction.save()
        response = auth_client.get(reverse('core:expense_range_dashboard'), {'period_type': 'all'})
        assert response.context['period_type'] == 'all'
        assert response.context['period_label'] == 'All Time'
        assert response.context['range_months'] == ['2025-02']

    def test_last_12_months_filters_exact_dates(self, auth_client, sample_data, monkeypatch):
        import json
        from datetime import date
        from core.views import dashboards

        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 10, 3)

        monkeypatch.setattr(dashboards, 'date', FixedDate)
        dates = [
            date(2025, 10, 2), date(2025, 10, 3), date(2026, 5, 1),
            date(2026, 10, 3), date(2026, 10, 4),
        ]
        for index, (transaction, transaction_date) in enumerate(zip(sample_data['transactions'], dates)):
            transaction.date = transaction_date
            transaction.amount_crc = -(index + 1) * 100
            transaction.save()
        response = auth_client.get(reverse('core:expense_range_dashboard'), {
            'period_type': 'year', 'period': 'last-12-months',
        })
        assert response.status_code == 200
        assert response.context['period_label'] == 'Last 12 Months'
        assert response.context['period_years'][0]['key'] == 'last-12-months'
        assert response.context['range_months'] == ['2025-10', '2026-05', '2026-10']
        assert response.context['selected_month'] == '2026-10'
        assert response.context['compare_total'] == 400
        category = json.loads(response.context['expense_data'])[0]
        assert (category['min'], category['median'], category['max']) == (200, 300, 400)
        assert 'Last 12 Months' in response.content.decode()

    def test_last_12_months_available_without_data(self, auth_client):
        response = auth_client.get(reverse('core:expense_range_dashboard'), {
            'period_type': 'year', 'period': 'last-12-months',
        })
        assert response.status_code == 200
        assert response.context['period_label'] == 'Last 12 Months'
        assert response.context['range_months'] == []
        assert response.context['compare_total'] == 0

    def test_only_all_time_and_year_controls(self, auth_client, sample_data):
        response = auth_client.get(reverse('core:expense_range_dashboard'))
        content = response.content.decode()
        assert '<option value="all"' in content
        assert '<option value="2025"' in content
        assert 'period_type=semester' not in content
        assert 'period_type=quarter' not in content

    def test_year_filter_still_works(self, auth_client, sample_data):
        for transaction in sample_data['transactions']:
            transaction.amount_crc = transaction.amount
            transaction.save()
        response = auth_client.get(reverse('core:expense_range_dashboard'), {
            'period_type': 'year', 'period': '2025',
        })
        assert response.context['period_type'] == 'year'
        assert response.context['period_label'] == '2025'
        assert response.context['range_months'] == ['2025-02']

    @pytest.mark.parametrize(
        ('period_type', 'period_key'),
        [('quarter', '2025-Q1'), ('semester', '2025-H1')],
    )
    def test_removed_periods_fall_back_to_all_time(self, auth_client, sample_data, period_type, period_key):
        response = auth_client.get(reverse('core:expense_range_dashboard'), {
            'period_type': period_type, 'period': period_key,
        })
        assert response.context['period_type'] == 'all'
        assert response.context['period_key'] == ''


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
