import factory
import hashlib
from datetime import date
from decimal import Decimal

from core.models import (
    User, CategoryGroup, Category, CreditAccount, DebitAccount,
    StatementImport, CurrencyLedger, RawTransaction, LogicalTransaction,
    ClassificationRule, ExchangeRate,
)


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
    email = factory.Sequence(lambda n: f'user{n}@example.com')
    password = factory.PostGenerationMethodCall('set_password', 'testpass123!')


class CategoryGroupFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CategoryGroup
        django_get_or_create = ('slug',)
    name = factory.LazyAttribute(lambda o: dict(CategoryGroup.SLUG_CHOICES).get(o.slug, o.slug))
    slug = 'expense'


class CategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Category
        django_get_or_create = ('name', 'group', 'user')
    name = 'Test Category'
    color = '#6c757d'
    group = factory.SubFactory(CategoryGroupFactory)
    user = factory.SubFactory(UserFactory)


class CreditAccountFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CreditAccount
    card_holder = 'TEST HOLDER'
    card_number_hash = factory.Sequence(lambda n: hashlib.sha256(f'card-{n}'.encode()).hexdigest())
    card_number_last4 = '1234'
    account_type = 'credit_account'
    user = factory.SubFactory(UserFactory)


class DebitAccountFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DebitAccount
    card_holder = 'TEST HOLDER'
    iban = factory.Sequence(lambda n: f'CR6101020000{n:010d}')
    client_number = '1234567'
    account_type = 'debit_account'
    user = factory.SubFactory(UserFactory)


class StatementImportFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = StatementImport
    account = factory.SubFactory(CreditAccountFactory)
    user = factory.SubFactory(UserFactory)
    filename = 'test.csv'
    file_hash = factory.Sequence(lambda n: hashlib.sha256(f'file-{n}'.encode()).hexdigest())
    statement_date = date(2025, 3, 15)


class CurrencyLedgerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CurrencyLedger
    statement_import = factory.SubFactory(StatementImportFactory)
    user = factory.SubFactory(UserFactory)
    currency = 'CRC'
    previous_balance = Decimal('500000.00')
    balance_at_cutoff = Decimal('351910.00')


class RawTransactionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = RawTransaction
    date = date(2025, 2, 1)
    description = 'TEST TRANSACTION'
    amount = Decimal('5000.00')
    ledger = factory.SubFactory(CurrencyLedgerFactory)
    user = factory.SubFactory(UserFactory)


class LogicalTransactionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = LogicalTransaction
    raw_transaction = factory.SubFactory(RawTransactionFactory)
    user = factory.SubFactory(UserFactory)
    date = date(2025, 2, 1)
    description = 'TEST TRANSACTION'
    amount = Decimal('5000.00')


class ClassificationRuleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ClassificationRule
    category = factory.SubFactory(CategoryFactory)
    user = factory.SubFactory(UserFactory)
    description = 'STARBUCKS'


class ExchangeRateFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ExchangeRate
        django_get_or_create = ('date',)
    date = date(2025, 2, 1)
    usd_to_crc = Decimal('510.50')
