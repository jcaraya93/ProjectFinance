"""Integration tests for rule management views."""
import pytest
from django.urls import reverse

from core.models import ClassificationRule, Transaction, Category


class TestRuleListSmoke:
    """GET /rules/ returns 200."""

    def test_empty_state(self, auth_client):
        resp = auth_client.get(reverse('core:yaml_rule_list'))
        assert resp.status_code == 200

    def test_with_data(self, auth_client, sample_data):
        resp = auth_client.get(reverse('core:yaml_rule_list'))
        assert resp.status_code == 200

    def test_with_filter(self, auth_client, sample_data):
        resp = auth_client.get(reverse('core:yaml_rule_list'), {
            'group': 'expense',
            'category': 'Groceries',
        })
        assert resp.status_code == 200
        assert 'TRANSACTION' in resp.content.decode()

    def test_with_search(self, auth_client, sample_data):
        resp = auth_client.get(reverse('core:yaml_rule_list'), {'q': 'TRANSACTION'})
        assert resp.status_code == 200


class TestRuleAdd:
    """GET and POST /rules/add/."""

    def test_add_form_renders(self, auth_client):
        resp = auth_client.get(reverse('core:yaml_rule_add'))
        assert resp.status_code == 200

    def test_add_rule_success(self, auth_client, expense_category):
        resp = auth_client.post(reverse('core:yaml_rule_add'), {
            'description': 'STARBUCKS',
            'group': 'expense',
            'category': f'expense:{expense_category.name}',
            'account_type': '',
            'metadata_key': '',
            'metadata_value': '',
            'amount_min': '',
            'amount_max': '',
            'detail': '',
        })
        assert resp.status_code == 302
        assert ClassificationRule.objects.filter(description='STARBUCKS').exists()


class TestRuleEdit:
    """GET and POST /rules/<id>/edit/."""

    def test_edit_form_renders(self, auth_client, sample_data):
        rule = sample_data['rule']
        resp = auth_client.get(reverse('core:yaml_rule_edit', args=[rule.pk]))
        assert resp.status_code == 200

    def test_edit_rule_success(self, auth_client, sample_data, expense_category):
        rule = sample_data['rule']
        resp = auth_client.post(reverse('core:yaml_rule_edit', args=[rule.pk]), {
            'description': 'UPDATED RULE',
            'group': 'expense',
            'category': f'expense:{expense_category.name}',
            'account_type': '',
            'metadata_key': '',
            'metadata_value': '',
            'amount_min': '',
            'amount_max': '',
            'detail': '',
        })
        assert resp.status_code == 302
        rule.refresh_from_db()
        assert rule.description == 'UPDATED RULE'


class TestRuleDelete:
    """POST /rules/<id>/delete/."""

    def test_delete_rule(self, auth_client, sample_data, unclassified_category):
        rule = sample_data['rule']
        rule_pk = rule.pk
        resp = auth_client.post(reverse('core:yaml_rule_delete', args=[rule_pk]))
        assert resp.status_code == 302
        assert not ClassificationRule.objects.filter(pk=rule_pk).exists()

    def test_delete_resets_transactions(self, auth_client, sample_data, unclassified_category):
        rule = sample_data['rule']
        # Ensure transactions are linked to this rule
        txn = sample_data['transactions'][0]
        txn.matched_rule = rule
        txn.classification_method = 'rule'
        txn.save(update_fields=['matched_rule', 'classification_method'])

        auth_client.post(reverse('core:yaml_rule_delete', args=[rule.pk]))
        txn.refresh_from_db()
        assert txn.category == unclassified_category
        assert txn.classification_method == 'unclassified'


class TestReclassify:
    """POST endpoints for reclassification."""

    def test_reclassify_all(self, auth_client, sample_data):
        resp = auth_client.post(reverse('core:reclassify_all'))
        assert resp.status_code == 302

    def test_classify_unclassified(self, auth_client, sample_data):
        resp = auth_client.post(reverse('core:classify_unclassified'))
        assert resp.status_code == 302
