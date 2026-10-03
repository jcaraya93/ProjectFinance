"""Integration tests for transaction views."""
import json
from decimal import Decimal

import pytest
from django.urls import reverse

from core.models import (
    Transaction, LogicalTransaction, CategoryGroup, CategoryNode,
    ClassificationRuleV2, UserPreference,
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


class TestTransactionListFilters:
    """Query params filter results correctly."""

    def test_date_filter(self, auth_client, sample_data):
        resp = auth_client.get(reverse('core:transaction_list'), {
            'start_date': '2025-02-02',
            'end_date': '2025-02-03',
        })
        assert resp.status_code == 200

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
