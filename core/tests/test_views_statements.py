"""Integration tests for statement and upload views."""
import hashlib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from core.models import Account, RawTransaction, LogicalTransaction


class TestStatementListSmoke:
    """GET /statements/ returns 200."""

    def test_empty_state(self, auth_client):
        resp = auth_client.get(reverse('core:statement_list'))
        assert resp.status_code == 200

    def test_with_data(self, auth_client, sample_data):
        account = sample_data['account']
        ledger = sample_data['ledger']
        wallet = f'{account.pk}:{ledger.currency}'
        resp = auth_client.get(reverse('core:statement_list'), {'wallet': wallet})
        assert resp.status_code == 200

    def test_with_data_content(self, auth_client, sample_data):
        """Verify annotated counts render (catches N+1 fix regressions)."""
        account = sample_data['account']
        ledger = sample_data['ledger']
        wallet = f'{account.pk}:{ledger.currency}'
        resp = auth_client.get(reverse('core:statement_list'), {'wallet': wallet})
        content = resp.content.decode()
        # Should contain the transaction count badge
        assert '5' in content  # 5 transactions in sample_data


class TestUploadPage:
    """GET /upload/ renders."""

    def test_upload_renders(self, auth_client):
        resp = auth_client.get(reverse('core:upload'))
        assert resp.status_code == 200


class TestUploadFileApi:
    """POST /upload/file/ — CSV upload endpoint."""

    def test_upload_csv_success(self, auth_client, credit_csv, exchange_rates):
        f = SimpleUploadedFile('test.csv', credit_csv.encode(), content_type='text/csv')
        resp = auth_client.post(reverse('core:upload_file_api'), {'file': f})
        assert resp.status_code == 200
        data = resp.json()
        assert data['status'] == 'ok'
        assert data['transaction_count'] > 0

    def test_upload_duplicate(self, auth_client, credit_csv, exchange_rates):
        f1 = SimpleUploadedFile('test.csv', credit_csv.encode(), content_type='text/csv')
        auth_client.post(reverse('core:upload_file_api'), {'file': f1})
        f2 = SimpleUploadedFile('test.csv', credit_csv.encode(), content_type='text/csv')
        resp = auth_client.post(reverse('core:upload_file_api'), {'file': f2})
        data = resp.json()
        assert data['status'] == 'skipped'
        assert data['reason'] == 'duplicate'

    def test_upload_invalid_extension(self, auth_client):
        f = SimpleUploadedFile('test.txt', b'not csv', content_type='text/plain')
        resp = auth_client.post(reverse('core:upload_file_api'), {'file': f})
        assert resp.status_code == 400

    def test_upload_no_file(self, auth_client):
        resp = auth_client.post(reverse('core:upload_file_api'))
        assert resp.status_code == 400


class TestPurge:
    """POST /statements/purge/ — data deletion."""

    def test_purge_requires_confirmation(self, auth_client, sample_data):
        resp = auth_client.post(reverse('core:purge_all_data'), {'confirm': 'nope'})
        assert resp.status_code == 302
        assert Account.objects.filter(user=sample_data['account'].user).exists()

    def test_purge_deletes_data(self, auth_client, sample_data):
        user = sample_data['account'].user
        resp = auth_client.post(reverse('core:purge_all_data'), {'confirm': 'DELETE ALL'})
        assert resp.status_code == 302
        assert Account.objects.filter(user=user).count() == 0

    def test_purge_also_removes_v2_data(self, auth_client, sample_data, category_groups):
        from core.models import CategoryNode, ClassificationRuleV2, CategoryGroup
        user = sample_data['account'].user
        parent = CategoryNode.objects.create(name='Food', user=user, group=CategoryGroup.get_group('expense'))
        kid = CategoryNode.objects.create(name='Snacks', user=user, group=parent.group, parent=parent)
        ClassificationRuleV2.objects.create(category=kid, user=user, description='X')
        auth_client.post(reverse('core:purge_all_data'), {'confirm': 'DELETE ALL'})
        assert not CategoryNode.objects.filter(user=user).exists()
        assert not ClassificationRuleV2.objects.filter(user=user).exists()


class TestDeleteAllTransactions:
    def test_requires_confirmation(self, auth_client, sample_data):
        from core.models import LogicalTransaction
        user = sample_data['account'].user
        auth_client.post(reverse('core:delete_all_transactions'), {'confirm': 'nope'})
        assert LogicalTransaction.objects.filter(user=user).exists()

    def test_deletes_transactions_keeps_accounts_categories_rules(self, auth_client, sample_data):
        from core.models import (
            LogicalTransaction, RawTransaction, StatementImport, CurrencyLedger,
            Category, ClassificationRule,
        )
        user = sample_data['account'].user
        cats, rules = Category.objects.filter(user=user).count(), ClassificationRule.objects.filter(user=user).count()
        resp = auth_client.post(reverse('core:delete_all_transactions'), {'confirm': 'DELETE TRANSACTIONS'})
        assert resp.status_code == 302
        for model in (LogicalTransaction, RawTransaction, StatementImport, CurrencyLedger):
            assert not model.objects.filter(user=user).exists()
        assert Account.objects.filter(user=user).exists()
        assert Category.objects.filter(user=user).count() == cats
        assert ClassificationRule.objects.filter(user=user).count() == rules
