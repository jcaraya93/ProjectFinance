import pytest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from core.models import User, CategoryGroup, Category, ExchangeRate

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture
def user(db):
    u = User.objects.create_user(email='test@example.com', password='testpass123!')
    u.create_default_categories()
    return u


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
