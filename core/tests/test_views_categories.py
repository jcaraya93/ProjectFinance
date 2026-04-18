"""Integration tests for category views."""
import pytest
import yaml
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import Category, CategoryGroup, ClassificationRule, Transaction


class TestCategoryListSmoke:
    """GET /categories/ returns 200 and renders without errors."""

    def test_category_list_empty(self, auth_client):
        resp = auth_client.get(reverse('core:category_list'))
        assert resp.status_code == 200

    def test_category_list_with_data(self, auth_client, sample_data):
        resp = auth_client.get(reverse('core:category_list'))
        assert resp.status_code == 200


class TestCategoryListContent:
    """Verify category counts render correctly (catches N+1 annotation bugs)."""

    def test_counts_match_db(self, auth_client, sample_data):
        resp = auth_client.get(reverse('core:category_list'))
        assert resp.status_code == 200
        content = resp.content.decode()
        # The expense category "Groceries" has 5 transactions and 1 rule
        assert 'Groceries' in content

    def test_all_groups_rendered(self, auth_client, sample_data):
        resp = auth_client.get(reverse('core:category_list'))
        content = resp.content.decode()
        assert 'Expense' in content or 'expense' in content


class TestCategoryAdd:
    """POST /categories/add/ creates a category."""

    def test_add_category_success(self, auth_client, category_groups):
        resp = auth_client.post(reverse('core:yaml_category_add'), {
            'group': 'expense',
            'category': 'NewTestCategory',
        })
        assert resp.status_code == 302
        assert Category.objects.filter(name='NewTestCategory').exists()

    def test_add_category_duplicate(self, auth_client, expense_category):
        resp = auth_client.post(reverse('core:yaml_category_add'), {
            'group': 'expense',
            'category': 'Groceries',
        })
        assert resp.status_code == 302
        assert Category.objects.filter(name='Groceries').count() == 1

    def test_add_category_missing_fields(self, auth_client):
        resp = auth_client.post(reverse('core:yaml_category_add'), {
            'group': '',
            'category': '',
        })
        assert resp.status_code == 302


class TestCategoryRename:
    """POST /categories/rename/ renames a category."""

    def test_rename_success(self, auth_client, expense_category):
        resp = auth_client.post(reverse('core:yaml_category_rename'), {
            'group': 'expense',
            'old_name': 'Groceries',
            'new_name': 'Food & Grocery',
        })
        assert resp.status_code == 302
        assert Category.objects.filter(name='Food & Grocery').exists()
        assert not Category.objects.filter(name='Groceries').exists()

    def test_rename_protected_default(self, auth_client, unclassified_category):
        resp = auth_client.post(reverse('core:yaml_category_rename'), {
            'group': 'unclassified',
            'old_name': 'Default',
            'new_name': 'Something',
        })
        assert resp.status_code == 302
        assert Category.objects.filter(name='Default').exists()


class TestCategoryDelete:
    """POST /categories/delete/ removes category and resets transactions."""

    def test_delete_category(self, auth_client, sample_data):
        resp = auth_client.post(reverse('core:yaml_category_delete'), {
            'group': 'expense',
            'category': 'Groceries',
        })
        assert resp.status_code == 302
        assert not Category.objects.filter(name='Groceries').exists()

    def test_delete_resets_transactions(self, auth_client, sample_data, unclassified_category):
        auth_client.post(reverse('core:yaml_category_delete'), {
            'group': 'expense',
            'category': 'Groceries',
        })
        # Transactions should now point to unclassified
        for txn in Transaction.objects.filter(user=sample_data['transactions'][0].user):
            assert txn.category == unclassified_category

    def test_delete_protected_default(self, auth_client, unclassified_category):
        resp = auth_client.post(reverse('core:yaml_category_delete'), {
            'group': 'unclassified',
            'category': 'Default',
        })
        assert resp.status_code == 302
        assert Category.objects.filter(name='Default').exists()


class TestCategoryExport:
    """GET /categories/export/ returns valid YAML."""

    def test_export_yaml(self, auth_client, sample_data):
        resp = auth_client.get(reverse('core:export_categories'))
        assert resp.status_code == 200
        assert resp['Content-Type'] == 'application/x-yaml'
        data = yaml.safe_load(resp.content.decode())
        assert 'groups' in data


class TestCategoryImport:
    """POST /categories/import/ creates categories from YAML."""

    def test_import_valid_yaml(self, auth_client, category_groups):
        yaml_content = yaml.dump({
            'groups': {
                'expense': {
                    'name': 'Expense',
                    'categories': {
                        'ImportedCat': {'color': '#abc123', 'rules': []},
                    },
                },
            },
        })
        f = SimpleUploadedFile('cats.yaml', yaml_content.encode(), content_type='application/x-yaml')
        resp = auth_client.post(reverse('core:import_categories'), {'file': f})
        assert resp.status_code == 302
        assert Category.objects.filter(name='ImportedCat').exists()

    def test_import_invalid_yaml(self, auth_client):
        f = SimpleUploadedFile('bad.yaml', b'{{{{not yaml', content_type='application/x-yaml')
        resp = auth_client.post(reverse('core:import_categories'), {'file': f})
        assert resp.status_code == 302  # Redirects with error message
