"""Round-trip tests for user data export → import."""
import json
from datetime import date
from decimal import Decimal

import pytest
from django.test import Client

from core.models import (
    User, UserPreference, CategoryGroup, Category, ClassificationRule,
    CreditAccount, DebitAccount, Account, StatementImport, CurrencyLedger,
    RawTransaction, LogicalTransaction, ExchangeRate,
)
from core.services.user_data_io import (
    export_user_data, import_user_data, ImportError as DataImportError,
)
from core.tests.factories import (
    CreditAccountFactory, DebitAccountFactory, StatementImportFactory,
    CurrencyLedgerFactory, RawTransactionFactory, LogicalTransactionFactory,
    ClassificationRuleFactory,
)


@pytest.fixture
def full_data(user, category_groups, expense_category, income_category, exchange_rates):
    """Build a complete data set for export testing."""
    # Preferences
    UserPreference.objects.update_or_create(
        user=user, defaults={'transaction_columns': {'1': True, '2': False}},
    )

    # Rule
    rule = ClassificationRuleFactory(
        category=expense_category, user=user,
        description='WALMART', detail='Grocery purchases',
    )

    # Credit account + statement + ledger + transactions
    credit_acct = CreditAccountFactory(user=user)
    stmt = StatementImportFactory(account=credit_acct, user=user, filename='jan.csv')
    ledger = CurrencyLedgerFactory(statement_import=stmt, user=user, currency='CRC')

    raw1 = RawTransactionFactory(
        ledger=ledger, user=user,
        date=date(2025, 2, 1), description='WALMART ESCAZU', amount=Decimal('-25000'),
    )
    ltxn1 = LogicalTransactionFactory(
        raw_transaction=raw1, user=user,
        date=raw1.date, description=raw1.description, amount=raw1.amount,
        amount_crc=Decimal('-25000'), amount_usd=Decimal('-48.97'),
        category=expense_category, classification_method='rule', matched_rule=rule,
    )

    raw2 = RawTransactionFactory(
        ledger=ledger, user=user,
        date=date(2025, 2, 5), description='SALARY DEPOSIT', amount=Decimal('500000'),
    )
    ltxn2 = LogicalTransactionFactory(
        raw_transaction=raw2, user=user,
        date=raw2.date, description=raw2.description, amount=raw2.amount,
        amount_crc=Decimal('500000'), amount_usd=Decimal('979.43'),
        category=income_category, classification_method='manual',
    )

    # Debit account
    debit_acct = DebitAccountFactory(user=user)
    stmt2 = StatementImportFactory(account=debit_acct, user=user, filename='feb.csv')
    ledger2 = CurrencyLedgerFactory(statement_import=stmt2, user=user, currency='USD')
    raw3 = RawTransactionFactory(
        ledger=ledger2, user=user,
        date=date(2025, 2, 10), description='AMAZON PRIME', amount=Decimal('-14.99'),
        account_metadata={'transaction_code': 'CO', 'reference_number': '12345'},
    )
    ltxn3 = LogicalTransactionFactory(
        raw_transaction=raw3, user=user,
        date=raw3.date, description=raw3.description, amount=raw3.amount,
        amount_crc=Decimal('-7652'), amount_usd=Decimal('-14.99'),
        category=expense_category, classification_method='unclassified',
    )

    return {
        'rule': rule,
        'credit_acct': credit_acct,
        'debit_acct': debit_acct,
        'transactions': [ltxn1, ltxn2, ltxn3],
    }


class TestExport:
    def test_export_produces_valid_structure(self, user, full_data):
        data = export_user_data(user)

        assert data['version'] == 1
        assert 'exported_at' in data
        assert data['user']['email'] == 'test@example.com'
        assert data['preferences']['transaction_columns'] == {'1': True, '2': False}

    def test_export_categories(self, user, full_data):
        data = export_user_data(user)
        cat_names = {c['name'] for c in data['categories']}
        assert 'Groceries' in cat_names
        assert 'Salary Main' in cat_names
        # Default categories are also included
        assert 'Default' in cat_names

    def test_export_rules(self, user, full_data):
        data = export_user_data(user)
        assert len(data['classification_rules']) == 1
        rule = data['classification_rules'][0]
        assert rule['description'] == 'WALMART'
        assert rule['category_name'] == 'Groceries'
        assert rule['category_group_slug'] == 'expense'

    def test_export_accounts_and_transactions(self, user, full_data):
        data = export_user_data(user)
        assert len(data['accounts']) == 2

        # Find credit account
        credit = next(a for a in data['accounts'] if a['account_type'] == 'credit_account')
        assert 'credit_account' in credit
        assert len(credit['statement_imports']) == 1
        assert len(credit['statement_imports'][0]['ledgers']) == 1
        assert len(credit['statement_imports'][0]['ledgers'][0]['raw_transactions']) == 2

        # Find debit account
        debit = next(a for a in data['accounts'] if a['account_type'] == 'debit_account')
        assert 'debit_account' in debit
        raw_txns = debit['statement_imports'][0]['ledgers'][0]['raw_transactions']
        assert raw_txns[0]['account_metadata']['transaction_code'] == 'CO'

    def test_export_exchange_rates(self, user, full_data):
        data = export_user_data(user)
        assert len(data['exchange_rates']) > 0
        assert all('date' in er and 'usd_to_crc' in er for er in data['exchange_rates'])

    def test_export_logical_transaction_details(self, user, full_data):
        data = export_user_data(user)
        credit = next(a for a in data['accounts'] if a['account_type'] == 'credit_account')
        raw_txns = credit['statement_imports'][0]['ledgers'][0]['raw_transactions']
        walmart_raw = next(r for r in raw_txns if 'WALMART' in r['description'])
        ltxn = walmart_raw['logical_transactions'][0]

        assert ltxn['classification_method'] == 'rule'
        assert ltxn['matched_rule_description'] == 'WALMART'
        assert ltxn['category_name'] == 'Groceries'
        assert Decimal(ltxn['amount_crc']) == Decimal('-25000')
        assert ltxn['amount_usd'] is not None


class TestImport:
    def _export_and_clear(self, user):
        """Export user data, then wipe everything so the user is fresh."""
        data = export_user_data(user)

        # Delete all user-owned data
        LogicalTransaction.objects.filter(user=user).delete()
        RawTransaction.objects.filter(user=user).delete()
        CurrencyLedger.objects.filter(user=user).delete()
        StatementImport.objects.filter(user=user).delete()
        Account.objects.filter(user=user).delete()
        ClassificationRule.objects.filter(user=user).delete()
        Category.objects.filter(user=user).exclude(name=Category.UNCLASSIFIED_NAME).delete()
        UserPreference.objects.filter(user=user).delete()

        return data

    def test_round_trip(self, user, full_data):
        data = self._export_and_clear(user)
        counts = import_user_data(user, data)

        assert counts['categories'] > 0
        assert counts['rules'] == 1
        assert counts['accounts'] == 2
        assert counts['statements'] == 2
        assert counts['logical_transactions'] == 3

    def test_round_trip_preserves_categories(self, user, full_data):
        data = self._export_and_clear(user)
        import_user_data(user, data)

        cat_names = set(Category.objects.filter(user=user).values_list('name', flat=True))
        assert 'Groceries' in cat_names
        assert 'Salary Main' in cat_names

    def test_round_trip_preserves_rules(self, user, full_data):
        data = self._export_and_clear(user)
        import_user_data(user, data)

        rules = ClassificationRule.objects.filter(user=user)
        assert rules.count() == 1
        assert rules.first().description == 'WALMART'
        assert rules.first().category.name == 'Groceries'

    def test_round_trip_preserves_accounts(self, user, full_data):
        data = self._export_and_clear(user)
        import_user_data(user, data)

        accts = Account.objects.filter(user=user)
        assert accts.count() == 2
        assert CreditAccount.objects.filter(user=user).exists()
        assert DebitAccount.objects.filter(user=user).exists()

    def test_round_trip_preserves_transactions(self, user, full_data):
        data = self._export_and_clear(user)
        import_user_data(user, data)

        ltxns = LogicalTransaction.objects.filter(user=user).order_by('date')
        assert ltxns.count() == 3

        walmart = ltxns.filter(description__contains='WALMART').first()
        assert walmart.classification_method == 'rule'
        assert walmart.matched_rule is not None
        assert walmart.matched_rule.description == 'WALMART'
        assert walmart.amount_crc == Decimal('-25000')

    def test_round_trip_preserves_metadata(self, user, full_data):
        data = self._export_and_clear(user)
        import_user_data(user, data)

        amazon = RawTransaction.objects.filter(user=user, description__contains='AMAZON').first()
        assert amazon.account_metadata['transaction_code'] == 'CO'
        assert amazon.account_metadata['reference_number'] == '12345'

    def test_import_rejects_wrong_version(self, user):
        data = {'version': 999}
        with pytest.raises(DataImportError, match='Unsupported export version'):
            import_user_data(user, data)

    def test_import_rejects_non_fresh_user(self, user, full_data):
        data = export_user_data(user)
        with pytest.raises(DataImportError, match='already has'):
            import_user_data(user, data)

    def test_import_atomic_rollback(self, user, full_data):
        """If import fails mid-way, no partial data should remain."""
        data = self._export_and_clear(user)

        # Corrupt an account to trigger failure
        data['accounts'][0]['account_type'] = 'invalid_type'

        with pytest.raises(DataImportError):
            import_user_data(user, data)

        # Verify nothing was created (atomic rollback)
        assert Account.objects.filter(user=user).count() == 0
        assert StatementImport.objects.filter(user=user).count() == 0


class TestAccountViews:
    def test_account_page_loads(self, auth_client):
        resp = auth_client.get('/account/')
        assert resp.status_code == 200
        assert b'Account' in resp.content

    def test_export_downloads_json(self, auth_client, user, full_data):
        resp = auth_client.get('/account/export/')
        assert resp.status_code == 200
        assert resp['Content-Type'] == 'application/json'
        assert 'attachment' in resp['Content-Disposition']

        data = json.loads(resp.content)
        assert data['version'] == 1
        assert data['user']['email'] == user.email

    def test_import_requires_post(self, auth_client):
        resp = auth_client.get('/account/import/')
        assert resp.status_code == 405

    def test_import_requires_file(self, auth_client):
        resp = auth_client.post('/account/import/')
        assert resp.status_code == 400

    def test_account_page_shows_import_disabled_with_data(self, auth_client, full_data):
        resp = auth_client.get('/account/')
        assert resp.status_code == 200
        assert b'Unavailable' in resp.content

    def test_account_page_shows_import_enabled_without_data(self, auth_client, user):
        # User has only default categories — import should be available
        resp = auth_client.get('/account/')
        assert resp.status_code == 200
        assert b'Import disabled' not in resp.content
        assert b'import-file' in resp.content
