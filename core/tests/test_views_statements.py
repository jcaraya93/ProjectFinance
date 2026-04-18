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
