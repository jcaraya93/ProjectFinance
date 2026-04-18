import pytest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.test import Client

from core.models import User, CategoryGroup, Category, ExchangeRate
from core.tests.factories import (
    CreditAccountFactory, StatementImportFactory, CurrencyLedgerFactory,
    RawTransactionFactory, LogicalTransactionFactory, ClassificationRuleFactory,
)

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture(autouse=True)
def _mock_yaml_sync(tmp_path):
    """Prevent tests from writing to the real classification_rules.yaml."""
    yaml_path = tmp_path / 'classification_rules.yaml'
    yaml_path.write_text('')
    with patch('core.services.yaml_classifier.get_rules_path', return_value=yaml_path):
        yield

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture
def user(db):
    u = User.objects.create_user(email='test@example.com', password='testpass123!')
    u.create_default_categories()
    return u


@pytest.fixture
def auth_client(user):
    """Authenticated Django test client."""
    c = Client()
    c.login(username='test@example.com', password='testpass123!')
    return c


@pytest.fixture
def category_groups(db):
    """Ensure all 4 category groups exist."""
    return {slug: CategoryGroup.get_group(slug) for slug, _ in CategoryGroup.SLUG_CHOICES}


@pytest.fixture
def unclassified_category(user):
    return Category.get_unclassified(user)


@pytest.fixture
def expense_category(user, category_groups):
    cat, _ = Category.objects.get_or_create(
        name='Groceries', group=category_groups['expense'], user=user,
        defaults={'color': '#ff6384'},
    )
    return cat


@pytest.fixture
def transfer_category(user, category_groups):
    cat, _ = Category.objects.get_or_create(
        name='Transfer', group=category_groups['transaction'], user=user,
        defaults={'color': '#36a2eb'},
    )
    return cat


@pytest.fixture
def income_category(user, category_groups):
    cat, _ = Category.objects.get_or_create(
        name='Salary Main', group=category_groups['income'], user=user,
        defaults={'color': '#4bc0c0'},
    )
    return cat


@pytest.fixture
def sample_data(user, expense_category, transfer_category, income_category, exchange_rates):
    """Full data set: account → statement → ledger → raw → logical transactions."""
    account = CreditAccountFactory(user=user)
    statement = StatementImportFactory(account=account, user=user)
    ledger = CurrencyLedgerFactory(statement_import=statement, user=user, currency='CRC')

    txns = []
    for i in range(5):
        raw = RawTransactionFactory(
            ledger=ledger, user=user,
            date=date(2025, 2, 1 + i),
            description=f'TRANSACTION {i}',
            amount=Decimal(f'{(i + 1) * 1000}'),
        )
        logical = LogicalTransactionFactory(
            raw_transaction=raw, user=user,
            date=raw.date, description=raw.description, amount=raw.amount,
            category=expense_category,
            classification_method='rule',
        )
        txns.append(logical)

    rule = ClassificationRuleFactory(
        category=expense_category, user=user, description='TRANSACTION',
    )

    return {
        'account': account,
        'statement': statement,
        'ledger': ledger,
        'transactions': txns,
        'rule': rule,
    }


@pytest.fixture
def exchange_rates(db):
    """Seed exchange rates for Feb 2025 (matches test fixtures)."""
    rates = []
    d = date(2025, 1, 25)
    while d <= date(2025, 3, 20):
        rates.append(ExchangeRate(date=d, usd_to_crc=Decimal('510.50')))
        d += timedelta(days=1)
    ExchangeRate.objects.bulk_create(rates, ignore_conflicts=True)


@pytest.fixture
def credit_csv():
    return (FIXTURES / 'credit_basic.csv').read_text(encoding='utf-8')


@pytest.fixture
def debit_csv():
    return (FIXTURES / 'debit_basic.csv').read_text(encoding='utf-8')
