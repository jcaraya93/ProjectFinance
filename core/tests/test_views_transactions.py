"""Integration tests for transaction views."""
import json
from urllib.parse import parse_qs, urlsplit
from decimal import Decimal

import pytest
from django.urls import reverse

from core.models import (
    Transaction, LogicalTransaction, CategoryGroup, CategoryNode,
    ClassificationRuleV2, UserPreference, Tag, TagGroup,
)
from core.tests.factories import RawTransactionFactory, LogicalTransactionFactory


class TestTransactionListSmoke:
    """GET /transactions/ returns 200."""

    def test_empty_state(self, auth_client):
        resp = auth_client.get(reverse('core:transaction_list'))
        assert resp.status_code == 200

    def test_with_data(self, auth_client, sample_data):
        resp = auth_client.get(reverse('core:transaction_list'))
        assert resp.status_code == 200

    def test_with_data_content(self, auth_client, sample_data):
        resp = auth_client.get(reverse('core:transaction_list'))
        content = resp.content.decode()
        assert 'TRANSACTION' in content

    def test_selected_transactions_are_restored_from_url(self, auth_client, sample_data):
        selected = sample_data['transactions'][:2]
        response = auth_client.get(reverse('core:transaction_list'), {
            'selected_txn': [str(txn.pk) for txn in selected],
        })
        content = response.content.decode()

        assert response.context['selected_transactions'] == [str(txn.pk) for txn in selected]
        for txn in selected:
            assert f'value="{txn.pk}" checked' in content
        assert 'id="bulkBar" class="d-none' in content

    def test_all_matching_selection_is_restored_from_url(self, auth_client, sample_data):
        response = auth_client.get(reverse('core:transaction_list'), {'selected_all': '1'})
        content = response.content.decode()

        assert response.context['selected_all_matching'] is True
        assert 'id="selectAllMatchingInput" value="1"' in content
        assert all(f'value="{txn.pk}" checked' in content for txn in sample_data['transactions'])

    def test_filter_form_preserves_current_sort(self, auth_client):
        response = auth_client.get(reverse('core:transaction_list'), {
            'sort': 'amount',
            'dir': 'asc',
        })
        content = response.content.decode()

        assert response.context['sort_col'] == 'amount'
        assert response.context['sort_dir'] == 'asc'
        assert '<input type="hidden" name="sort" value="amount">' in content
        assert '<input type="hidden" name="dir" value="asc">' in content

    def test_date_dropdown_has_explicit_clear_dates_button(self, auth_client):
        content = auth_client.get(reverse('core:transaction_list')).content.decode()

        assert 'class="btn btn-sm btn-outline-danger date-preset" data-range="all">Clear dates</button>' in content

    def test_tags_have_a_dedicated_column(self, auth_client, user, sample_data):
        txn = sample_data['transactions'][0]
        tag = Tag.objects.create(user=user, name='Trip')
        txn.tags.add(tag)

        content = auth_client.get(reverse('core:transaction_list')).content.decode()

        assert content.index('>Category') < content.index('<th>Ungrouped tags</th>') < content.index('>Description')
        assert f'<td>{txn.description}</td>' in content
        assert f'href="/transactions/?tag={tag.pk}" class="badge' in content
        assert 'data-col="11" data-cell="6" checked> Ungrouped tags' in content

    def test_tag_groups_have_separate_columns(self, auth_client, user, sample_data):
        txn = sample_data['transactions'][-1]
        trips = TagGroup.objects.create(user=user, name='Trips')
        work = TagGroup.objects.create(user=user, name='Work')
        trip_tag = Tag.objects.create(user=user, group=trips, name='Vacation')
        work_tag = Tag.objects.create(user=user, group=work, name='Reimbursement')
        txn.tags.add(trip_tag, work_tag)

        response = auth_client.get(reverse('core:transaction_list'))
        content = response.content.decode()

        assert f'<th data-tag-group="{trips.pk}">Trips</th>' in content
        assert f'<th data-tag-group="{work.pk}">Work</th>' in content
        assert f'data-col="tag-group-column-{trips.pk}"' in content
        assert f'data-col="tag-group-column-{work.pk}"' in content
        trips_cell = content.split(f'<td class="text-nowrap" data-tag-group="{trips.pk}">', 1)[1].split('</td>', 1)[0]
        work_cell = content.split(f'<td class="text-nowrap" data-tag-group="{work.pk}">', 1)[1].split('</td>', 1)[0]
        assert 'Vacation' in trips_cell and 'Reimbursement' not in trips_cell
        assert 'Reimbursement' in work_cell and 'Vacation' not in work_cell


class TestTransactionReturnLink:
    def test_direct_visit_has_no_return_link_even_with_referrer(self, auth_client):
        response = auth_client.get(reverse('core:transaction_list'),
                                   HTTP_REFERER='http://testserver/categories-v2/')
        assert response.context['return_to'] == ''
        assert 'transaction-return-link' not in response.content.decode()

    @pytest.mark.parametrize('source,label', [
        ('category_v2_list', 'Categories'),
        ('statement_list', 'Statements'),
        ('spending_income_dashboard', 'Expense Composition'),
        ('expense_composition_over_time_dashboard', 'Expense Time Composition'),
        ('income_overview_dashboard', 'Income Composition'),
        ('income_composition_over_time_dashboard', 'Income Time Composition'),
        ('income_salary_dashboard', 'Salary'),
        ('income_bonus_dashboard', 'Bonuses'),
        ('bank_income_overview_dashboard', 'Bank Income'),
        ('reimbursement_overview_dashboard', 'Refunds'),
        ('manual_classification_dashboard', 'Manual Classification'),
        ('transaction_health_dashboard', 'Data Quality'),
    ])
    def test_return_link_preserves_source_filters(self, auth_client, source, label):
        return_to = reverse(f'core:{source}') + '?period_type=year&period_key=2025'
        if source == 'spending_income_dashboard':
            return_to += '#drill=12,34'
        response = auth_client.get(reverse('core:transaction_list'), {
            'return_to': return_to, 'sort': 'amount', 'dir': 'asc', 'page': '2',
        })
        assert response.context['return_to'] == return_to
        assert response.context['return_label'] == label
        content = response.content.decode()
        assert f'Back to {label}' in content
        assert 'name="return_to"' in content
        for key in ('filter_qs', 'pagination_qs'):
            assert parse_qs(response.context[key])['return_to'] == [return_to]
        assert '?return_to=' in content

    @pytest.mark.parametrize('return_to', [
        'https://example.com/categories-v2/', '//example.com/categories-v2/',
        '/transactions/', '/account/', '/categories-v2/delete/', '/unknown/',
        'javascript:alert(1)', '/\\example.com/categories-v2/',
    ])
    def test_invalid_return_link_is_removed_with_warning(self, auth_client, return_to):
        response = auth_client.get(reverse('core:transaction_list'), {'return_to': return_to})
        assert response.context['return_to'] == ''
        assert 'return_to' not in parse_qs(response.context['pagination_qs'])
        assert 'transaction-return-link' not in response.content.decode()
        assert 'The transaction return link is invalid' in response.content.decode()

    @pytest.mark.parametrize('source', [
        'category_v2_list', 'statement_list', 'spending_income_dashboard',
        'income_overview_dashboard', 'income_salary_dashboard', 'income_bonus_dashboard',
        'bank_income_overview_dashboard', 'reimbursement_overview_dashboard',
        'manual_classification_dashboard', 'transaction_health_dashboard',
    ])
    def test_source_links_carry_return_destination(self, auth_client, user, sample_data, source):
        import re
        from html import unescape

        if source == 'transaction_health_dashboard':
            LogicalTransactionFactory.create_batch(21, user=user,
                                                   category_v2=None, classification_method_v2='unclassified')
        roles = {
            'income_salary_dashboard': 'salary', 'income_bonus_dashboard': 'bonus',
            'bank_income_overview_dashboard': 'bank', 'reimbursement_overview_dashboard': 'reimbursement',
        }
        if source in roles:
            from core.models import CategoryGroup, CategoryNode
            CategoryNode.objects.create(user=user, group=CategoryGroup.get_group('income'),
                                        name='Assigned Income', income_dashboard_role=roles[source])
        source_url = reverse(f'core:{source}') + '?period_type=all'
        response = auth_client.get(source_url)
        if source in ('spending_income_dashboard', 'income_overview_dashboard'):
            params = parse_qs(urlsplit(response.context['composition_transactions_url']).query)
            assert params['return_to'] == [source_url]
            assert 'params.set(\'return_to\', location.pathname + location.search' in response.content.decode()
            return
        links = re.findall(r'href="([^"]+)"', response.content.decode())
        transaction_links = [
            unescape(link) for link in links
            if link.startswith(reverse('core:transaction_list') + '?')
        ]
        assert transaction_links
        for link in transaction_links:
            assert parse_qs(urlsplit(link).query)['return_to'] == [source_url]


class TestTransactionListFilters:
    """Query params filter results correctly."""

    def test_date_filter(self, auth_client, sample_data):
        resp = auth_client.get(reverse('core:transaction_list'), {
            'start_date': '2025-02-02',
            'end_date': '2025-02-03',
        })
        assert resp.status_code == 200

    def test_direct_category_toggle_is_inside_categories_dropdown(self, auth_client):
        content = auth_client.get(reverse('core:transaction_list')).content.decode()
        categories_dropdown = content.split('            Categories ')[1].split('            Split ')[0]
        assert 'id="directCategoryScope"' in categories_dropdown
        assert 'Selected only' in categories_dropdown
        assert 'transaction-category-controls' in categories_dropdown
        assert 'transaction-category-group' in categories_dropdown
        assert 'category-group-items' in categories_dropdown
        assert 'category-root-item fw-bold' in categories_dropdown
        assert 'category-name' in categories_dropdown
        assert 'id="category-tab-expense"' in categories_dropdown
        assert 'id="category-tab-income"' in categories_dropdown
        assert 'id="category-tab-transfer"' in categories_dropdown
        assert 'id="category-pane-expense" role="tabpanel"' in categories_dropdown
        assert 'id="category-pane-income" role="tabpanel"' in categories_dropdown
        assert 'id="category-pane-transfer" role="tabpanel"' in categories_dropdown
        assert 'name="group" value="unclassified"' in categories_dropdown
        assert 'aria-describedby="directCategoryScopeHelp"' in categories_dropdown
        assert 'Exclude subcategories' in categories_dropdown
        assert content.count('id="directCategoryScope"') == 1
        assert 'id="directCategoryScope" aria-describedby="directCategoryScopeHelp" checked' not in content

    def test_category_dropdown_keeps_each_parent_with_its_descendants(self, auth_client, user, sample_data):
        food, snacks, _, _ = self._v2_setup(user, sample_data)

        response = auth_client.get(reverse('core:transaction_list'))

        expense = next(group for group in response.context['category_groups'] if group['group'].slug == 'expense')
        food_root = next(root for root in expense['roots'] if root['rows'][0]['node'] == food)
        assert [row['node'].pk for row in food_root['rows']] == [food.pk, snacks.pk]
        content = response.content.decode()
        assert 'transaction-category-root' in content
        assert 'category-root-item fw-bold' in content
        assert 'transaction-category-descendants' in content
        assert 'category-tree-branch' in content
        assert 'padding-left: calc(12px + ' in content

    def _v2_setup(self, user, sample_data):
        group = CategoryGroup.get_group('expense')
        food = CategoryNode.objects.create(name='Food', user=user, group=group)
        snacks = CategoryNode.objects.create(name='Snacks', user=user, group=group, parent=food)
        rent = CategoryNode.objects.create(name='Rent', user=user, group=group)
        t = sample_data['transactions']
        for txn, node, method in ((t[0], food, 'manual'), (t[1], snacks, 'rule'), (t[2], rent, 'rule')):
            txn.category_v2, txn.classification_method_v2 = node, method
            txn.save()
        return food, snacks, rent, t

    @staticmethod
    def _shown(resp):
        return {tx.pk for tx in resp.context['page_obj']}

    def test_category_filter_uses_v2_and_includes_subtree(self, auth_client, user, sample_data):
        food, snacks, rent, t = self._v2_setup(user, sample_data)
        url = reverse('core:transaction_list')
        assert self._shown(auth_client.get(url, {'category': [food.pk]})) == {t[0].pk, t[1].pk}
        assert self._shown(auth_client.get(url, {'category': [snacks.pk]})) == {t[1].pk}
        assert self._shown(auth_client.get(url, {'category': [rent.pk]})) == {t[2].pk}

    def test_direct_category_filter_excludes_subtree(self, auth_client, user, sample_data):
        food, snacks, rent, t = self._v2_setup(user, sample_data)
        response = auth_client.get(reverse('core:transaction_list'), {
            'category': [food.pk], 'category_scope': 'direct',
        })
        assert self._shown(response) == {t[0].pk}
        assert 'category_scope=direct' in response.context['pagination_qs']
        assert 'category_scope=direct' in response.context['filter_qs']
        assert 'id="directCategoryScope" aria-describedby="directCategoryScopeHelp" checked' in response.content.decode()

    def test_method_and_group_filters_use_v2_fields(self, auth_client, user, sample_data):
        food, snacks, rent, t = self._v2_setup(user, sample_data)
        url = reverse('core:transaction_list')
        assert self._shown(auth_client.get(url, {'cls_method': 'manual'})) == {t[0].pk}
        assert self._shown(auth_client.get(url, {'group': 'expense'})) == {x.pk for x in t}

    def test_rule_filter_uses_v2_rule(self, auth_client, user, sample_data):
        food, snacks, rent, t = self._v2_setup(user, sample_data)
        rule = ClassificationRuleV2.objects.create(category=food, user=user, description='X')
        t[3].matched_rule_v2 = rule
        t[3].save()
        resp = auth_client.get(reverse('core:transaction_list'), {'rule': rule.pk})
        assert self._shown(resp) == {t[3].pk}

    def _search_setup(self, sample_data):
        t = sample_data['transactions']
        t[0].description, t[0].note = 'GROCERY STORE', ''
        t[1].description, t[1].note = 'SINPE MOVIL', 'grocery for party'
        t[2].description, t[2].note = 'GROCERY MARKET', 'party'
        t[3].description, t[3].note = 'OTHER', ''
        t[4].description, t[4].note = 'OTHER 2', ''
        for x in t:
            x.save()
        return t

    def test_search_matches_description_or_note(self, auth_client, sample_data):
        t = self._search_setup(sample_data)
        url = reverse('core:transaction_list')
        assert self._shown(auth_client.get(url, {'search': 'grocery'})) == {t[0].pk, t[1].pk, t[2].pk}
        assert self._shown(auth_client.get(url, {'search': 'party'})) == {t[1].pk, t[2].pk}

    def test_note_filter(self, auth_client, sample_data):
        t = self._search_setup(sample_data)
        url = reverse('core:transaction_list')
        assert self._shown(auth_client.get(url, {'note': 'has'})) == {t[1].pk, t[2].pk}
        assert self._shown(auth_client.get(url, {'note': 'none'})) == {t[0].pk, t[3].pk, t[4].pk}
        assert self._shown(auth_client.get(url, {'note': ''})) == {x.pk for x in t}

    def test_sort(self, auth_client, sample_data):
        resp = auth_client.get(reverse('core:transaction_list'), {
            'sort': 'date',
            'dir': 'asc',
        })
        assert resp.status_code == 200

    def test_sort_by_note(self, auth_client, sample_data):
        t = sample_data['transactions']
        for txn, note in zip(t, ['b', 'a', 'd', 'c', 'e']):
            txn.note = note
            txn.save()
        resp = auth_client.get(reverse('core:transaction_list'), {'sort': 'note', 'dir': 'asc'})
        assert [x.note for x in resp.context['page_obj']] == ['a', 'b', 'c', 'd', 'e']

    def test_search(self, auth_client, sample_data):
        resp = auth_client.get(reverse('core:transaction_list'), {
            'search': 'TRANSACTION 1',
        })
        assert resp.status_code == 200


class TestEditTransaction:
    """GET and POST /transactions/<id>/edit/."""

    def test_edit_renders(self, auth_client, sample_data):
        raw_id = sample_data['transactions'][0].raw_transaction_id
        resp = auth_client.get(reverse('core:edit_transaction', args=[raw_id]))
        assert resp.status_code == 200

    def test_edit_save_assigns_manual_category(self, auth_client, user, sample_data):
        txn = sample_data['transactions'][0]
        node = CategoryNode.objects.create(name='Food', user=user, group=CategoryGroup.get_group('expense'))
        resp = auth_client.post(reverse('core:edit_transaction', args=[txn.raw_transaction_id]), {
            'action': 'save',
            'split_description': ['Updated Description'],
            'split_amount': [str(txn.amount)],
            'split_category': [node.pk],
        })
        assert resp.status_code == 302
        txn.refresh_from_db()
        assert txn.description == 'Updated Description'
        assert (txn.category_v2, txn.classification_method_v2, txn.matched_rule_v2) == (node, 'manual', None)

    def test_edit_saves_note_per_split(self, auth_client, user, sample_data, exchange_rates):
        raw = sample_data['transactions'][0].raw_transaction
        node = CategoryNode.objects.create(name='Food', user=user, group=CategoryGroup.get_group('expense'))
        half = raw.normalized_amount / 2
        auth_client.post(reverse('core:edit_transaction', args=[raw.pk]), {
            'action': 'save', 'split_description': ['A', 'B'], 'split_amount': [str(half), str(half)],
            'split_category': [node.pk, node.pk], 'split_note': ['first note', ''],
        })
        assert [lt.note for lt in raw.logical_transactions.order_by('pk')] == ['first note', '']
        resp = auth_client.get(reverse('core:edit_transaction', args=[raw.pk]))
        assert 'first note' in resp.content.decode()

    @pytest.mark.parametrize('method', ['rule', 'manual', 'unclassified'])
    def test_same_category_preserves_classification(self, auth_client, user, sample_data, method):
        txn = sample_data['transactions'][0]
        node = CategoryNode.objects.create(name='Food', user=user, group=CategoryGroup.get_group('expense'))
        rule = ClassificationRuleV2.objects.create(user=user, category=node, description='MATCH')
        txn.category_v2 = node
        txn.classification_method_v2 = method
        txn.matched_rule_v2 = rule if method == 'rule' else None
        txn.save()
        response = auth_client.post(reverse('core:edit_transaction', args=[txn.raw_transaction_id]), {
            'split_id': [txn.pk], 'split_description': ['Updated Description'],
            'split_amount': [str(txn.amount)], 'split_category': [node.pk], 'split_note': ['My note'],
        })
        assert response.status_code == 302
        txn.refresh_from_db()
        assert txn.note == 'My note' and txn.description == 'Updated Description'
        assert txn.classification_method_v2 == method
        assert txn.matched_rule_v2_id == (rule.pk if method == 'rule' else None)

    def test_split_notes_preserve_each_rows_rule_when_rows_removed(self, auth_client, user, sample_data, exchange_rates):
        from core.models import LogicalTransaction
        txn = sample_data['transactions'][0]
        raw = txn.raw_transaction
        node = CategoryNode.objects.create(name='Food', user=user, group=CategoryGroup.get_group('expense'))
        rule = ClassificationRuleV2.objects.create(user=user, category=node, description='MATCH')
        txn.amount = raw.normalized_amount / 2
        txn.category_v2 = node
        txn.classification_method_v2 = 'manual'
        txn.save()
        second = LogicalTransaction.objects.create(
            user=user, raw_transaction=raw, date=raw.date, description='Second',
            amount=raw.normalized_amount / 2, category_v2=node,
            classification_method_v2='rule', matched_rule_v2=rule,
        )
        url = reverse('core:edit_transaction', args=[raw.pk])
        response = auth_client.post(url, {
            'split_id': [txn.pk, second.pk], 'split_description': ['First', 'Second'],
            'split_amount': [str(txn.amount), str(second.amount)], 'split_category': [node.pk, node.pk],
            'split_note': ['First note', 'Second note'],
        })
        assert response.status_code == 302
        txn.refresh_from_db()
        second.refresh_from_db()
        assert txn.classification_method_v2 == 'manual' and txn.note == 'First note'
        assert second.classification_method_v2 == 'rule' and second.matched_rule_v2 == rule
        assert second.note == 'Second note'
        auth_client.post(url, {
            'split_id': [second.pk], 'split_description': ['Second'],
            'split_amount': [str(raw.normalized_amount)], 'split_category': [node.pk],
            'split_note': ['Retained note'],
        })
        second.refresh_from_db()
        assert raw.logical_transactions.count() == 1
        assert second.note == 'Retained note'
        assert second.classification_method_v2 == 'rule' and second.matched_rule_v2 == rule

    def test_category_change_to_protected_resets_method(self, auth_client, user, sample_data):
        txn = sample_data['transactions'][0]
        CategoryNode.ensure_protected(user)
        protected = CategoryNode.objects.get(user=user, group__slug='unclassified', name='Unclassified')
        response = auth_client.post(reverse('core:edit_transaction', args=[txn.raw_transaction_id]), {
            'split_id': [txn.pk], 'split_description': [txn.description],
            'split_amount': [str(txn.amount)], 'split_category': [protected.pk], 'split_note': ['Note'],
        })
        assert response.status_code == 302
        txn.refresh_from_db()
        assert txn.classification_method_v2 == 'unclassified' and txn.matched_rule_v2 is None

    def test_invalid_split_identity_does_not_modify_transaction(self, auth_client, sample_data):
        txn = sample_data['transactions'][0]
        before = (txn.description, txn.category_v2_id, txn.classification_method_v2, txn.matched_rule_v2_id)
        response = auth_client.post(reverse('core:edit_transaction', args=[txn.raw_transaction_id]), {
            'split_id': [sample_data['transactions'][1].pk], 'split_description': ['Wrong row'],
            'split_amount': [str(txn.amount)], 'split_category': [txn.category_v2_id],
        }, follow=True)
        assert 'Invalid transaction entries.' in response.content.decode()
        txn.refresh_from_db()
        assert (txn.description, txn.category_v2_id, txn.classification_method_v2, txn.matched_rule_v2_id) == before

    def test_edit_rejects_other_users_node(self, auth_client, sample_data):
        from core.models import User
        other = User.objects.create_user(email='o@example.com', password='x')
        node = CategoryNode.objects.create(name='Food', user=other, group=CategoryGroup.get_group('expense'))
        txn = sample_data['transactions'][0]
        resp = auth_client.post(reverse('core:edit_transaction', args=[txn.raw_transaction_id]), {
            'action': 'save', 'split_description': ['X'], 'split_amount': [str(txn.amount)],
            'split_category': [node.pk],
        })
        assert resp.status_code == 404


class TestSplitTransaction:
    """Split a transaction into multiple logical transactions."""

    def test_split_and_unsplit(self, auth_client, user, sample_data, exchange_rates):
        txn = sample_data['transactions'][0]
        raw = txn.raw_transaction
        half = raw.normalized_amount / 2
        node = CategoryNode.objects.create(name='Food', user=user, group=CategoryGroup.get_group('expense'))

        resp = auth_client.post(reverse('core:edit_transaction', args=[raw.pk]), {
            'action': 'save',
            'split_description': ['Split A', 'Split B'],
            'split_amount': [str(half), str(half)],
            'split_category': [node.pk, node.pk],
        })
        assert resp.status_code == 302
        assert raw.logical_transactions.count() == 2
        assert {lt.category_v2 for lt in raw.logical_transactions.all()} == {node}

        resp = auth_client.post(reverse('core:edit_transaction', args=[raw.pk]), {
            'action': 'unsplit',
        })
        assert resp.status_code == 302
        assert raw.logical_transactions.count() == 1
        only = raw.logical_transactions.get()
        assert only.category_v2.is_protected and only.classification_method_v2 == 'unclassified'


class TestBulkUpdateCategory:
    """POST /transactions/bulk-update-category/."""

    def test_bulk_update_assigns_manual_category(self, auth_client, user, sample_data):
        node = CategoryNode.objects.create(name='Food', user=user, group=CategoryGroup.get_group('expense'))
        txns = sample_data['transactions']
        txn_ids = [t.pk for t in txns[:2]]
        resp = auth_client.post(reverse('core:bulk_update_category'), {
            'txn_ids': txn_ids,
            'category_id': node.pk,
        })
        assert resp.status_code == 302
        for txn in Transaction.objects.filter(pk__in=txn_ids):
            assert txn.category_v2 == node
            assert txn.classification_method_v2 == 'manual'
            assert txn.matched_rule_v2 is None

    def test_bulk_update_redirect_preserves_selected_transactions(self, auth_client, user, sample_data):
        node = CategoryNode.objects.create(name='Food', user=user, group=CategoryGroup.get_group('expense'))
        txns = sample_data['transactions'][:2]
        response = auth_client.post(reverse('core:bulk_update_category'), {
            'txn_ids': [str(txn.pk) for txn in txns],
            'category_id': node.pk,
            'next': '/transactions/?search=keep',
        })

        assert response['Location'] == (
            f'/transactions/?search=keep&selected_txn={txns[0].pk}&selected_txn={txns[1].pk}'
        )

    def test_select_all_matching_redirect_preserves_selection_mode(self, auth_client, user, sample_data):
        node = CategoryNode.objects.create(name='Food', user=user, group=CategoryGroup.get_group('expense'))
        response = auth_client.post(reverse('core:bulk_update_category'), {
            'select_all_matching': '1',
            'filter_qs': 'search=TEST',
            'category_id': node.pk,
            'next': '/transactions/?search=TEST',
        })

        assert response['Location'] == '/transactions/?search=TEST&selected_all=1'

    def test_bulk_unclassified_node_sets_unclassified(self, auth_client, user, sample_data):
        CategoryNode.ensure_protected(user)
        node = CategoryNode.objects.get(user=user, group__slug='unclassified', name='Unclassified')
        txn = sample_data['transactions'][0]
        auth_client.post(reverse('core:bulk_update_category'), {'txn_ids': [txn.pk], 'category_id': node.pk})
        txn.refresh_from_db()
        assert (txn.category_v2, txn.classification_method_v2) == (node, 'unclassified')

    def test_bulk_rejects_other_users_node(self, auth_client, sample_data):
        from core.models import User
        other = User.objects.create_user(email='o@example.com', password='x')
        node = CategoryNode.objects.create(name='Food', user=other, group=CategoryGroup.get_group('expense'))
        resp = auth_client.post(reverse('core:bulk_update_category'), {
            'txn_ids': [sample_data['transactions'][0].pk], 'category_id': node.pk})
        assert resp.status_code == 404

    def test_bulk_update_missing_ids(self, auth_client):
        resp = auth_client.post(reverse('core:bulk_update_category'), {})
        assert resp.status_code == 302


class TestSaveColumnPreferences:
    """POST /preferences/transaction-columns/."""

    def test_save_columns(self, auth_client, user):
        columns = {'date': True, 'description': True, 'amount': True, 'category': False}
        resp = auth_client.post(
            reverse('core:save_transaction_columns'),
            json.dumps(columns),
            content_type='application/json',
        )
        assert resp.status_code == 200
        pref = UserPreference.objects.get(user=user)
        assert pref.transaction_columns == columns

    def test_save_invalid_json(self, auth_client):
        resp = auth_client.post(
            reverse('core:save_transaction_columns'),
            'not json',
            content_type='application/json',
        )
        assert resp.status_code == 400
