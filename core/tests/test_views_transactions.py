"""Integration tests for transaction views."""
import json
from decimal import Decimal

import pytest
from django.urls import reverse

from core.models import Transaction, LogicalTransaction, Category, UserPreference
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

    def test_category_filter(self, auth_client, sample_data, expense_category):
        resp = auth_client.get(reverse('core:transaction_list'), {
            'category': [expense_category.pk],
        })
        assert resp.status_code == 200

    def test_sort(self, auth_client, sample_data):
        resp = auth_client.get(reverse('core:transaction_list'), {
            'sort': 'date',
            'dir': 'asc',
        })
        assert resp.status_code == 200

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

    def test_edit_save(self, auth_client, sample_data, expense_category):
        txn = sample_data['transactions'][0]
        raw_id = txn.raw_transaction_id
        resp = auth_client.post(reverse('core:edit_transaction', args=[raw_id]), {
            'action': 'save',
            'split_description': ['Updated Description'],
            'split_amount': [str(txn.amount)],
            'split_category': [expense_category.pk],
        })
        assert resp.status_code == 302
        txn.refresh_from_db()
        assert txn.description == 'Updated Description'


class TestSplitTransaction:
    """Split a transaction into multiple logical transactions."""

    def test_split_and_unsplit(self, auth_client, sample_data, expense_category, exchange_rates):
        txn = sample_data['transactions'][0]
        raw = txn.raw_transaction
        half = raw.amount / 2

        # Split into 2
        resp = auth_client.post(reverse('core:edit_transaction', args=[raw.pk]), {
            'action': 'save',
            'split_description': ['Split A', 'Split B'],
            'split_amount': [str(half), str(half)],
            'split_category': [expense_category.pk, expense_category.pk],
        })
        assert resp.status_code == 302
        assert raw.logical_transactions.count() == 2

        # Unsplit
        resp = auth_client.post(reverse('core:edit_transaction', args=[raw.pk]), {
            'action': 'unsplit',
        })
        assert resp.status_code == 302
        assert raw.logical_transactions.count() == 1


class TestBulkUpdateCategory:
    """POST /transactions/bulk-update-category/."""

    def test_bulk_update(self, auth_client, sample_data, transfer_category):
        txn_ids = [t.pk for t in sample_data['transactions'][:2]]
        resp = auth_client.post(reverse('core:bulk_update_category'), {
            'txn_ids': txn_ids,
            'category_id': transfer_category.pk,
        })
        assert resp.status_code == 302
        for txn in Transaction.objects.filter(pk__in=txn_ids):
            assert txn.category == transfer_category
            assert txn.classification_method == 'manual'

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
