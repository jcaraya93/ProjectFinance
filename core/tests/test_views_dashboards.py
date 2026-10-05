"""Integration tests for dashboard views."""
import pytest
from django.urls import reverse


DASHBOARD_URLS = [
    'core:dashboard',
    'core:spending_income_dashboard',
    'core:expense_composition_over_time_dashboard',
    'core:expense_range_dashboard',
    'core:chart_comparison',
    'core:car_dashboard',
    'core:car_gas_dashboard',
    'core:car_parking_dashboard',
    'core:food_dashboard',
    'core:income_salary_dashboard',
    'core:income_overview_dashboard',
    'core:income_composition_over_time_dashboard',
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


class TestIncomeComposition:
    def _transaction(self, user, node, when, amount, usd):
        from core.tests.factories import LogicalTransactionFactory
        return LogicalTransactionFactory(
            user=user, category_v2=node, date=when, amount=amount,
            amount_crc=amount, amount_usd=usd,
        )

    @pytest.mark.parametrize('route', ['income_overview_dashboard', 'income_composition_over_time_dashboard'])
    def test_navigation_and_login(self, auth_client, client, route):
        url = reverse('core:' + route)
        assert client.get(url).status_code == 302
        response = auth_client.get(url)
        assert response.context['dashboard_category'] == 'income'
        content = response.content.decode()
        sidebar = content.split('list-group list-group-flush')[1].split('</div>')[0]
        assert '>Composition</a>' in sidebar
        assert '>Time Composition</a>' in sidebar
        assert '>Overview</a>' not in sidebar
        assert sidebar.count(' active"') == 1
        for name in ('income_salary_dashboard', 'income_bonus_dashboard',
                     'reimbursement_overview_dashboard', 'bank_income_overview_dashboard'):
            assert f'href="{reverse("core:" + name)}"' in sidebar
        assert 'timelineCategoryForm' not in content
        assert 'Level 2' in content

    def test_composition_rollup_summary_currency_and_latest_income_month(self, auth_client, user, expense_category):
        import json
        from datetime import date
        from urllib.parse import parse_qs, urlsplit
        from core.models import CategoryGroup, CategoryNode
        from core.tests.factories import UserFactory

        group = CategoryGroup.get_group('income')
        parent = CategoryNode.objects.create(user=user, group=group, name='Work', color='#123456')
        child = CategoryNode.objects.create(user=user, group=group, parent=parent, name='Work Bonuses')
        unclassified = CategoryNode.objects.get(user=user, group=group, name='Unclassified')
        self._transaction(user, parent, date(2025, 1, 1), 100, 1)
        self._transaction(user, child, date(2025, 2, 1), 300, 3)
        self._transaction(user, unclassified, date(2025, 2, 2), 100, 1)
        self._transaction(user, expense_category, date(2025, 3, 1), -9999, -99)
        other = UserFactory()
        other_node = CategoryNode.objects.create(user=other, group=group, name='Other Income')
        self._transaction(other, other_node, date(2025, 4, 1), 99999, 999)
        url = reverse('core:income_overview_dashboard')
        response = auth_client.get(url, {'level': 1})
        assert response.context['period_key'] == '2025-02'
        assert response.context['category_level'] == 1
        data = json.loads(response.context['composition_category_data'])
        assert data['labels'] == ['Work', 'Unclassified']
        assert data['values'] == [300, 100]
        assert data['ids'] == [parent.pk, unclassified.pk]
        drill = json.loads(response.context['composition_drill_data'])
        assert drill[str(parent.pk)]['ids'] == [child.pk]
        summary = response.context['composition_summary']
        assert summary['total'] == 400
        assert summary['unclassified_pct'] == 25
        assert summary['median'] == 250
        assert summary['change_pct'] == 60
        assert '<h4 class="mb-0">Income Composition</h4>' in response.content.decode()
        assert 'text-success' in response.content.decode()
        params = parse_qs(urlsplit(response.context['composition_transactions_url']).query)
        assert params['group'] == ['income']
        assert params['start_date'] == ['2025-02-01'] and params['end_date'] == ['2025-02-28']
        assert params['return_to'] == [url + '?level=1']
        detailed = auth_client.get(url, {'level': 2})
        assert detailed.context['category_level'] == 2
        assert json.loads(detailed.context['composition_category_data'])['labels'] == ['Work Bonuses', 'Unclassified']
        assert detailed.context['composition_summary'] == summary
        assert '&level=2' in detailed.content.decode()
        usd = auth_client.get(url, {'period_type': 'all', 'display_currency': 'USD', 'level': 1})
        assert json.loads(usd.context['composition_category_data'])['values'] == [4, 1]
        assert usd.context['composition_summary']['total'] == 5

    @pytest.mark.parametrize('period_type,period,start,end', [
        ('month', '2025-02', '2025-02-08', '2025-02-14'),
        ('quarter', 'last-3-months', '2025-01-15', '2025-01-31'),
    ])
    def test_timeline_segment_exact_transactions_and_return(
        self, auth_client, user, expense_category, monkeypatch, period_type, period, start, end,
    ):
        import json
        from datetime import date
        from urllib.parse import parse_qs, urlsplit
        from core.models import CategoryGroup, CategoryNode
        from core.views import dashboards

        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2025, 4, 15)
        monkeypatch.setattr(dashboards, 'date', FixedDate)
        group = CategoryGroup.get_group('income')
        parent = CategoryNode.objects.create(user=user, group=group, name='Work')
        child = CategoryNode.objects.create(user=user, group=group, parent=parent, name='Salary')
        target = self._transaction(user, child, date.fromisoformat(start), 100, 1)
        direct = self._transaction(user, parent, date.fromisoformat(end), 200, 2)
        self._transaction(user, child, date(2025, 3, 1), 1000, 10)
        self._transaction(user, expense_category, date.fromisoformat(start), -9999, -99)
        url = reverse('core:income_composition_over_time_dashboard')
        response = auth_client.get(url, {'period_type': period_type, 'period': period, 'display_currency': 'USD', 'level': 1})
        data = json.loads(response.context['composition_timeline_data'])
        assert data['category_ids'] == [parent.pk]
        assert data['series'][0]['name'] == 'Work'
        index = next(i for i, interval in enumerate(data['intervals']) if interval['start'] == start)
        segment = data['transaction_urls'][0][index]
        params = parse_qs(urlsplit(segment).query)
        assert params['group'] == ['income'] and params['category'] == [str(parent.pk)]
        assert params['start_date'] == [start] and params['end_date'] == [end]
        assert 'category_scope' not in params
        transactions = auth_client.get(segment)
        assert {tx.pk for tx in transactions.context['page_obj']} == {target.pk, direct.pk}
        assert data['series'][0]['data'][index] == 3
        assert transactions.context['return_label'] == 'Income Time Composition'
        assert transactions.context['return_to'] == response.wsgi_request.get_full_path()
        assert 'Amount received (' in response.content.decode()
        assert 'data-timeline-visibility="all"' in response.content.decode()
        assert 'data-timeline-visibility="none"' in response.content.decode()
        default = auth_client.get(url)
        assert default.context['period_key'] == 'last-12-months'
        assert default.context['start_date'] == date(2024, 4, 15)
        assert default.context['end_date'] == date(2025, 4, 15)

    @pytest.mark.parametrize('level', [1, 2])
    def test_timeline_includes_all_categories_and_empty_intervals(self, auth_client, user, level):
        import json
        from datetime import date
        from core.models import CategoryGroup, CategoryNode
        group = CategoryGroup.get_group('income')
        for index in range(12):
            node = CategoryNode.objects.create(user=user, group=group, name=f'Income {index}')
            child = CategoryNode.objects.create(user=user, group=group, parent=node, name=f'Child {index}')
            grandchild = CategoryNode.objects.create(user=user, group=group, parent=child, name=f'Grandchild {index}')
            self._transaction(user, grandchild, date(2025, 1, 1), index + 1, index + 1)
        response = auth_client.get(reverse('core:income_composition_over_time_dashboard'),
                                   {'period_type': 'quarter', 'period': '2025-Q1', 'level': level})
        data = json.loads(response.context['composition_timeline_data'])
        assert len(data['series']) == 12
        assert all(series['name'].startswith('Income' if level == 1 else 'Child') for series in data['series'])
        assert response.context['category_level'] == level
        assert f'level={level}' in response.context['timeline_currency_params']
        assert data['labels'] == ['Jan 2025', 'Feb 2025', 'Mar 2025']
        assert [sum(series['data'][i] for series in data['series']) for i in range(3)] == [78, 0, 0]
        assert len(data['transaction_urls']) == len(data['category_ids']) == len(data['colors']) == 12
        assert all(len(urls) == len(data['intervals']) for urls in data['transaction_urls'])

    @pytest.mark.parametrize('route', ['income_overview_dashboard', 'income_composition_over_time_dashboard'])
    def test_default_level_two_and_explicit_level_one(self, auth_client, route):
        url = reverse('core:' + route)
        default = auth_client.get(url)
        assert default.context['category_level'] == 2
        assert 'Level 2' in default.content.decode()
        explicit = auth_client.get(url, {'level': 1})
        assert explicit.context['category_level'] == 1

    @pytest.mark.parametrize('route', ['income_overview_dashboard', 'income_composition_over_time_dashboard'])
    @pytest.mark.parametrize('level', ['bad', '0', '3'])
    def test_invalid_level_defaults_to_one(self, auth_client, route, level):
        response = auth_client.get(reverse('core:' + route), {'level': level})
        assert response.context['category_level'] == 1


class TestExpenseCompositionTimeline:
    @pytest.mark.parametrize('period_type,period,start,end', [
        ('month', '2025-02', '2025-02-08', '2025-02-14'),
        ('quarter', 'last-3-months', '2025-01-15', '2025-01-31'),
    ])
    def test_segment_links_filter_exact_category_interval_and_restore_source(
        self, auth_client, user, expense_category, monkeypatch, period_type, period, start, end,
    ):
        import json
        from datetime import date
        from urllib.parse import parse_qs, urlsplit
        from core.models import CategoryNode
        from core.views import dashboards

        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2025, 4, 15)
        monkeypatch.setattr(dashboards, 'date', FixedDate)
        parent = CategoryNode.objects.create(name='Food', user=user, group=expense_category.group)
        expense_category.parent = parent
        expense_category.save()
        target = self._transaction(user, expense_category, date.fromisoformat(start), 100, 1)
        direct = self._transaction(user, parent, date.fromisoformat(end), 200, 2)
        self._transaction(user, expense_category, date.fromisoformat(end).replace(month=3), 1000, 10)
        params = {'period_type': period_type, 'period': period, 'display_currency': 'USD',
                  'category_selection': 'custom', 'category': parent.pk}
        source = auth_client.get(reverse('core:expense_composition_over_time_dashboard'), params)
        data = json.loads(source.context['expense_timeline_data'])
        index = next(i for i, interval in enumerate(data['intervals']) if interval['start'] == start)
        url = data['transaction_urls'][0][index]
        query = parse_qs(urlsplit(url).query)
        assert query['category'] == [str(parent.pk)]
        assert query['start_date'] == [start] and query['end_date'] == [end]
        assert 'category_scope' not in query
        transactions = auth_client.get(url)
        assert {txn.pk for txn in transactions.context['page_obj']} == {target.pk, direct.pk}
        assert transactions.context['return_label'] == 'Expense Time Composition'
        assert transactions.context['return_to'] == source.wsgi_request.get_full_path()
        assert sum(abs(txn.amount_usd) for txn in transactions.context['page_obj']) == data['series'][0]['data'][index]

    def _transaction(self, user, node, when, amount, usd=None):
        from core.tests.factories import LogicalTransactionFactory
        return LogicalTransactionFactory(
            user=user, category_v2=node, date=when, amount=-amount,
            amount_crc=-amount, amount_usd=-usd if usd is not None else None,
        )

    def _timeline(self, auth_client, **params):
        import json
        response = auth_client.get(reverse('core:expense_composition_over_time_dashboard'), params)
        assert response.status_code == 200
        assert 'id="expenseTimelineChart"' in response.content.decode()
        return json.loads(response.context['expense_timeline_data'])

    def test_timeline_is_only_on_separate_dashboard(self, auth_client):
        composition = auth_client.get(reverse('core:spending_income_dashboard'))
        content = composition.content.decode()
        assert 'id="expenseTimelineChart"' not in content
        assert 'expense_timeline_data' not in composition.context
        assert 'id="expenseChart"' in content
        assert 'id="topCategoriesChart"' in content
        url = reverse('core:expense_composition_over_time_dashboard')
        assert f'href="{url}"' in content
        timeline = auth_client.get(url)
        assert 'Expense Time Composition' in timeline.content.decode()
        assert timeline.context['dashboard_category'] == 'expense'
        assert 'id="expenseChart"' not in timeline.content.decode()
        assert 'id="expenseFilters"' in timeline.content.decode()
        assert '>Level</span>' not in timeline.content.decode()
        assert 'Level 2 (top)' not in timeline.content.decode()

    def test_requires_login(self, client):
        response = client.get(reverse('core:expense_composition_over_time_dashboard'))
        assert response.status_code == 302

    def test_legacy_selection_does_not_filter_descendant_spending(self, auth_client, user, expense_category):
        import json
        from datetime import date
        from core.models import CategoryNode
        parent = CategoryNode.objects.create(name='Food', user=user, group=expense_category.group)
        expense_category.parent = parent
        expense_category.save()
        self._transaction(user, parent, date(2025, 2, 1), 20)
        self._transaction(user, expense_category, date(2025, 2, 1), 100)
        url = reverse('core:expense_composition_over_time_dashboard')
        response = auth_client.get(url, {'period_type': 'all', 'level': 1,
                                       'category_selection': 'custom', 'category': parent.pk})
        assert 'timelineCategoryForm' not in response.content.decode()
        assert 'data-timeline-visibility="all"' in response.content.decode()
        assert 'data-timeline-visibility="none"' in response.content.decode()
        data = json.loads(response.context['expense_timeline_data'])
        assert data['series'] == [{'name': 'Food', 'data': [120]}]
        response = auth_client.get(url, {'period_type': 'all', 'level': 2,
                                       'category_selection': 'custom', 'category': expense_category.pk})
        assert response.context['category_level'] == 1
        assert json.loads(response.context['expense_timeline_data'])['series'] == [{'name': 'Food', 'data': [120]}]
        assert 'category_selection' not in response.context['timeline_currency_params']

    def test_legacy_empty_and_invalid_selection_show_all_categories(self, auth_client, user, expense_category):
        from datetime import date
        self._transaction(user, expense_category, date(2025, 2, 1), 100)
        for categories in ([], ['bad', '999999']):
            data = self._timeline(auth_client, period_type='all', category_selection='custom', category=categories)
            assert data['series'] == [{'name': expense_category.name, 'data': [100]}]
            assert data['labels'] == ['Feb 2025']

    def test_legacy_custom_selection_does_not_limit_categories(self, auth_client, user, expense_category):
        from datetime import date
        from core.models import CategoryNode
        ids = []
        for index in range(12):
            node = CategoryNode.objects.create(name=f'Selected {index}', user=user, group=expense_category.group)
            ids.append(node.pk)
            self._transaction(user, node, date(2025, 2, 1), index + 1)
        data = self._timeline(auth_client, period_type='all', level=2,
                              category_selection='custom', category=ids[:1])
        assert len(data['series']) == 12
        assert all(item['name'] != 'Remaining categories' for item in data['series'])

    def test_filters_match_composition_and_default_to_last_twelve_months(self, auth_client, user, expense_category, monkeypatch):
        from datetime import date
        from core.views import dashboards
        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2025, 4, 15)
        monkeypatch.setattr(dashboards, 'date', FixedDate)
        url = reverse('core:expense_composition_over_time_dashboard')
        empty = auth_client.get(url)
        assert empty.context['period_key'] == 'last-12-months'
        self._transaction(user, expense_category, date(2025, 2, 1), 100)
        for params in ({}, {'display_currency': 'USD'}, {'category_selection': 'custom'}):
            response = auth_client.get(url, params)
            assert response.context['period_type'] == 'year'
            assert response.context['period_key'] == 'last-12-months'
            assert response.context['start_date'] == date(2024, 4, 15)
            assert response.context['end_date'] == date(2025, 4, 15)
        composition = auth_client.get(reverse('core:spending_income_dashboard'))
        assert composition.context['period_type'] == 'month'
        assert composition.context['period_key'] == '2025-02'
        for params in ({'period_type': 'bad', 'period': 'bad', 'level': 'bad'},
                       {'period_type': 'month', 'period': '2025-02', 'level': '2'}):
            timeline = auth_client.get(url, params)
            composition = auth_client.get(reverse('core:spending_income_dashboard'), params)
            assert timeline.context['category_level'] == 1
            for key in ('period_type', 'period_key', 'start_date', 'end_date'):
                assert timeline.context[key] == composition.context[key]

    def test_weekly_rollup_boundaries_empty_intervals_and_currency(self, auth_client, user, expense_category):
        from datetime import date
        from core.models import CategoryNode
        parent = CategoryNode.objects.create(name='Food', user=user, group=expense_category.group)
        expense_category.parent = parent
        expense_category.save()
        for day, amount, usd in ((1, 100, 1), (7, 200, 2), (8, 400, 4), (28, 800, 8)):
            self._transaction(user, expense_category, date(2025, 2, day), amount, usd)
        self._transaction(user, expense_category, date(2025, 1, 31), 10000, 100)
        self._transaction(user, expense_category, date(2025, 3, 1), 20000, 200)
        data = self._timeline(auth_client, period_type='month', period='2025-02', level=1)
        assert data['grouping'] == 'Weekly'
        assert data['labels'] == ['Feb 1-7', 'Feb 8-14', 'Feb 15-21', 'Feb 22-28']
        assert data['series'] == [{'name': 'Food', 'data': [300, 400, 0, 800]}]
        usd = self._timeline(auth_client, period_type='month', period='2025-02', level=2, display_currency='USD')
        assert usd['series'] == [{'name': 'Food', 'data': [3, 4, 0, 8]}]

    def test_all_top_level_categories_even_with_level_two_url(self, auth_client, user, expense_category):
        from datetime import date
        from core.models import CategoryNode
        for index in range(12):
            node = CategoryNode.objects.create(name=f'Category {index}', user=user, group=expense_category.group)
            self._transaction(user, node, date(2025, 1, 1), index + 1)
            self._transaction(user, node, date(2025, 3, 31), (index + 1) * 2)
        level1 = self._timeline(auth_client, period_type='quarter', period='2025-Q1', level=1)
        assert level1['grouping'] == 'Monthly'
        assert level1['labels'] == ['Jan 2025', 'Feb 2025', 'Mar 2025']
        assert len(level1['series']) == 12
        assert [item['name'] for item in level1['series']][:2] == ['Category 11', 'Category 10']
        level2 = self._timeline(auth_client, period_type='quarter', period='2025-Q1', level=2)
        assert len(level2['series']) == 12
        assert level2['series'] == level1['series']
        assert level2['category_ids'] == level1['category_ids']
        assert level2['intervals'] == level1['intervals']
        assert [sum(item['data'][i] for item in level2['series']) for i in range(3)] == [78, 0, 156]
        assert len(level2['colors']) == len(level2['series'])

    def test_rolling_boundaries_user_scope_and_expense_group(self, auth_client, user, expense_category, monkeypatch):
        from datetime import date
        from core.models import CategoryNode, CategoryGroup, User
        from core.views import dashboards
        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2025, 4, 15)
        monkeypatch.setattr(dashboards, 'date', FixedDate)
        for when, amount in ((date(2025, 1, 14), 10000), (date(2025, 1, 15), 10),
                             (date(2025, 4, 15), 20), (date(2025, 4, 16), 20000)):
            self._transaction(user, expense_category, when, amount)
        income = CategoryNode.objects.create(name='Income', user=user, group=CategoryGroup.get_group('income'))
        self._transaction(user, income, date(2025, 2, 1), 30000)
        other = User.objects.create_user(email='timeline-other@example.com', password='test')
        other_category = CategoryNode.objects.create(name='Other', user=other, group=expense_category.group)
        self._transaction(other, other_category, date(2025, 2, 1), 40000)
        data = self._timeline(auth_client, period_type='quarter', period='last-3-months')
        assert data['labels'] == ['Jan 2025', 'Feb 2025', 'Mar 2025', 'Apr 2025']
        assert data['series'] == [{'name': expense_category.name, 'data': [10, 0, 0, 20]}]

    @pytest.mark.parametrize('count', [9, 10, 11])
    def test_top_level_categories_are_not_capped(self, auth_client, user, expense_category, count):
        from datetime import date
        from core.models import CategoryNode
        for index in range(count):
            node = CategoryNode.objects.create(name=f'Category {index}', user=user, group=expense_category.group)
            self._transaction(user, node, date(2025, 2, 1), index + 1)
        data = self._timeline(auth_client, period_type='all', level=2)
        assert len(data['series']) == count
        assert all(item['name'] != 'Remaining categories' for item in data['series'])
        assert sum(item['data'][0] for item in data['series']) == count * (count + 1) / 2

    def test_empty_all_time_and_short_month_tail(self, auth_client, user, expense_category):
        from datetime import date
        data = self._timeline(auth_client, period_type='all')
        assert data['labels'] == [] and data['series'] == []
        self._transaction(user, expense_category, date(2025, 3, 31), 100)
        month = self._timeline(auth_client, period_type='month', period='2025-03')
        assert month['labels'][-1] == 'Mar 29-31'
        assert month['series'][0]['data'] == [0, 0, 0, 0, 100]
        all_time = self._timeline(auth_client, period_type='all')
        assert all_time['labels'] == ['Mar 2025']
        assert all_time['series'][0]['data'] == [100]


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
    """Expense Composition always starts with top-level categories."""

    @pytest.mark.parametrize('route,group,links', [
        ('dashboard', 'overview', ['dashboard']),
        ('spending_income_dashboard', 'expense', [
            'spending_income_dashboard', 'expense_composition_over_time_dashboard', 'expense_range_dashboard',
        ]),
        ('expense_composition_over_time_dashboard', 'expense', [
            'spending_income_dashboard', 'expense_composition_over_time_dashboard', 'expense_range_dashboard',
        ]),
        ('expense_range_dashboard', 'expense', [
            'spending_income_dashboard', 'expense_composition_over_time_dashboard', 'expense_range_dashboard',
        ]),
        ('car_dashboard', 'category', ['car_dashboard', 'car_gas_dashboard', 'car_parking_dashboard', 'food_dashboard']),
    ])
    def test_dashboard_sections_have_separate_sidebars(self, auth_client, route, group, links):
        response = auth_client.get(reverse('core:' + route))
        assert response.status_code == 200
        assert response.context['dashboard_category'] == group
        content = response.content.decode()
        sidebar = content.split('list-group list-group-flush')[1].split('</div>')[0]
        for link in links:
            assert f'href="{reverse("core:" + link)}"' in sidebar
        assert sidebar.count('list-group-item-action') == len(links)
        assert sidebar.count(' active"') == 1
        assert f'href="{reverse("core:spending_income_dashboard")}">Expense</a>' in content
        assert f'href="{reverse("core:car_dashboard")}">Expense Category</a>' in content

    def _breakdown(self, resp):
        import json
        data = json.loads(resp.context['expense_category_data'])
        return dict(zip(data['labels'], data['values']))

    def test_period_buttons_use_styled_toolbar(self, auth_client, sample_data):
        content = auth_client.get(reverse('core:spending_income_dashboard')).content.decode()
        assert 'id="expenseFilters" class="dashboard-filter-toolbar d-flex flex-wrap align-items-center gap-3 p-3 bg-light border rounded mb-3"' in content
        toolbar = content.split('id="expenseFilters"')[1].split('<!-- ═══ SPENDING ═══ -->')[0]
        assert '>Period</span>' not in toolbar
        assert '>Level</span>' not in toolbar
        assert 'Level 1' not in toolbar
        assert 'Level 2' not in toolbar
        assert 'Level 1' not in content.split('id="expenseFilters"')[0]
        assert '<div class="btn-group btn-group-sm" role="group">' in toolbar
        assert '<select' not in toolbar
        for period in ('month', 'quarter', 'semester', 'year'):
            assert f'period_type={period}' in toolbar

    def test_legacy_level_two_rolls_up_to_top_level(self, auth_client, sample_data, expense_category, user):
        from core.models import CategoryNode
        parent = CategoryNode.objects.create(name='Food', group=expense_category.group, user=user, color='#111111')
        expense_category.parent = parent
        expense_category.save()
        url = reverse('core:spending_income_dashboard')
        base = {'period_type': 'all'}

        default = self._breakdown(auth_client.get(url, base))
        assert list(default) == ['Food']  # level 1 is the default

        response = auth_client.get(url, {**base, 'level': 2})
        detailed = self._breakdown(response)
        assert response.context['category_level'] == 1
        assert response.context['hide_category_level'] is True
        assert detailed == default
        assert 'Level 2' not in response.content.decode()
        assert '&level=2' not in response.content.decode()

        level1 = self._breakdown(auth_client.get(url, {**base, 'level': 1}))
        assert list(level1) == ['Food']
        assert level1 == detailed


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
        assert "legend: { position: 'right' }" in resp.content.decode()
        assert 'id="drillCrumb" class="small mb-2 clearfix"' in resp.content.decode()
        assert 'class="row g-3 mb-4" style="clear: both;"' in resp.content.decode()
        assert 'width: barsWidth' in resp.content.decode()
        assert 'width: donutWidth' in resp.content.decode()
        assert 'new ResizeObserver' in resp.content.decode()
        assert 'donut.updateOptions({ series: t.values.map(Math.round), labels: t.labels, colors: t.colors });' in resp.content.decode()
        assert 'var total = current().values.reduce(' in resp.content.decode()
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


@pytest.mark.django_db
class TestCreditTransferMatching:
    def _txn(self, user, account, category, day, crc, usd):
        from decimal import Decimal
        from core.tests.factories import (
            CurrencyLedgerFactory, LogicalTransactionFactory, RawTransactionFactory, StatementImportFactory,
        )
        ledger = CurrencyLedgerFactory(
            statement_import=StatementImportFactory(account=account, user=user), user=user)
        raw = RawTransactionFactory(ledger=ledger, user=user, date=day, amount=Decimal(crc))
        txn = LogicalTransactionFactory(raw_transaction=raw, user=user, date=day, category_v2=category)
        type(txn).objects.filter(pk=txn.pk).update(amount_crc=Decimal(crc), amount_usd=Decimal(usd))
        return txn

    def test_closest_pairs_win_and_tolerance_is_relative(self, auth_client, user, transfer_category):
        from datetime import date
        from core.tests.factories import CreditAccountFactory, DebitAccountFactory

        card, bank = CreditAccountFactory(user=user), DebitAccountFactory(user=user)
        day = date(2025, 2, 10)
        # A small payment must not lose its debit to a larger, loosely similar credit seen first.
        self._txn(user, card, transfer_category, day, '287382', '563')
        self._txn(user, card, transfer_category, day, '38861', '76')
        self._txn(user, bank, transfer_category, day, '-38861', '-76')
        # Cross-currency payment drifting ~1.3% with the exchange rate still pairs.
        self._txn(user, card, transfer_category, day, '191339', '375')
        self._txn(user, bank, transfer_category, day, '-193640', '-380')

        ctx = auth_client.get(reverse('core:credit_transfers_dashboard')).context
        assert ctx['pair_count'] == 2
        assert [round(u['abs_amount']) for u in ctx['unmatched_credit']] == [287382]
        assert ctx['unmatched_debit_count'] == 0
