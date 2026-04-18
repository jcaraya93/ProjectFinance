from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email

    def create_default_categories(self):
        """Create a protected Default category in each group for this user."""
        for slug, _ in CategoryGroup.SLUG_CHOICES:
            group = CategoryGroup.get_group(slug)
            Category.objects.get_or_create(
                name=Category.UNCLASSIFIED_NAME,
                group=group,
                user=self,
                defaults={'color': '#adb5bd'},
            )


class CategoryGroup(models.Model):
    EXPENSE = 'expense'
    INCOME = 'income'
    TRANSACTION = 'transaction'
    UNCLASSIFIED = 'unclassified'

    SLUG_CHOICES = [
        (EXPENSE, 'Expense'),
        (INCOME, 'Income'),
        (TRANSACTION, 'Transfer'),
        (UNCLASSIFIED, 'Unclassified'),
    ]

    name = models.CharField(max_length=50, unique=True)
    slug = models.CharField(max_length=20, unique=True, choices=SLUG_CHOICES)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @classmethod
    def get_group(cls, slug):
        group, _ = cls.objects.get_or_create(slug=slug, defaults={'name': dict(cls.SLUG_CHOICES)[slug]})
        return group


class Category(models.Model):
    UNCLASSIFIED_NAME = 'Default'

    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, default='#6c757d', help_text='Hex color for charts')
    group = models.ForeignKey(CategoryGroup, on_delete=models.PROTECT, related_name='categories')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='categories')

    PROTECTED_NAMES = {'Default'}

    class Meta:
        verbose_name_plural = 'categories'
        ordering = ['group', 'name']
        unique_together = [['name', 'group', 'user']]

    @property
    def is_protected(self):
        return self.name in self.PROTECTED_NAMES

    def __str__(self):
        return self.name

    @classmethod
    def get_unclassified(cls, user):
        group = CategoryGroup.get_group(CategoryGroup.UNCLASSIFIED)
        cat, _ = cls.objects.get_or_create(
            name=cls.UNCLASSIFIED_NAME,
            group=group,
            user=user,
            defaults={'color': '#adb5bd'},
        )
        return cat


class Account(models.Model):
    ACCOUNT_TYPES = [
        ('credit_account', 'Credit Account'),
        ('debit_account', 'Debit Account'),
    ]

    card_holder = models.CharField(max_length=100, blank=True)
    nickname = models.CharField(max_length=100, blank=True, help_text='Optional friendly name')
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPES)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='accounts')

    class Meta:
        ordering = ['-account_type']

    def __str__(self):
        return self.nickname or f"Account {self.pk}"


class CreditAccount(Account):
    card_number_hash = models.CharField(max_length=64, unique=True, help_text='SHA-256 hash of full card number')
    card_number_last4 = models.CharField(max_length=4, blank=True, help_text='Last 4 digits for display')

    def save(self, *args, **kwargs):
        if not self.account_type:
            self.account_type = 'credit_account'
        if not self.nickname and self.card_number_last4:
            self.nickname = f"Credit {self.card_number_last4}"
        super().save(*args, **kwargs)

    @classmethod
    def hash_card_number(cls, card_number):
        import hashlib
        return hashlib.sha256(card_number.encode()).hexdigest()

    def __str__(self):
        return self.nickname or f"Credit ****{self.card_number_last4}"


class DebitAccount(Account):
    iban = models.CharField(max_length=34, unique=True)
    client_number = models.CharField(max_length=20, blank=True)

    def save(self, *args, **kwargs):
        if not self.account_type:
            self.account_type = 'debit_account'
        if not self.nickname and self.iban:
            self.nickname = f"Debit {self.iban[-5:-1]}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nickname or f"Debit ****{self.iban[-4:]}"


class StatementImport(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='statements')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='statement_imports')
    filename = models.CharField(max_length=255)
    file_hash = models.CharField(max_length=64, blank=True, db_index=True, help_text='SHA-256 hash of the file content')
    statement_date = models.DateField(null=True, blank=True)
    points_assigned = models.PositiveIntegerField(default=0)
    points_redeemable = models.PositiveIntegerField(default=0)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-statement_date']

    def __str__(self):
        return f"{self.filename} ({self.account})"


class CurrencyLedger(models.Model):
    CURRENCY_CHOICES = [
        ('CRC', 'Costa Rican Colón'),
        ('USD', 'US Dollar'),
    ]

    statement_import = models.ForeignKey(
        StatementImport, on_delete=models.CASCADE, related_name='ledgers'
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='currency_ledgers')
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES)
    previous_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    balance_at_cutoff = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        unique_together = ['statement_import', 'currency']

    def __str__(self):
        return f"{self.statement_import.filename} — {self.currency}"


class ExchangeRate(models.Model):
    date = models.DateField(unique=True)
    usd_to_crc = models.DecimalField(max_digits=10, decimal_places=4, help_text='1 USD = X CRC')

    class Meta:
        ordering = ['date']

    def __str__(self):
        return f"{self.date}: 1 USD = {self.usd_to_crc} CRC"


class RawTransaction(models.Model):
    """Immutable record from the bank statement."""
    date = models.DateField()
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    ledger = models.ForeignKey(
        CurrencyLedger, on_delete=models.CASCADE, related_name='raw_transactions'
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='raw_transactions')
    account_metadata = models.JSONField(default=dict, blank=True, help_text='Account-type-specific data (e.g. transaction_code, reference_number for debit)')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    @property
    def currency(self):
        return self.ledger.currency

    @property
    def account_type(self):
        return self.ledger.statement_import.account.account_type

    def __str__(self):
        return f"{self.date} | {self.description[:40]} | {self.amount} {self.currency}"


class LogicalTransaction(models.Model):
    """Mutable, derived record for classification and analysis."""
    CLASSIFICATION_METHODS = [
        ('unclassified', 'Unclassified'),
        ('rule', 'Rule'),
        ('manual', 'Manual'),
    ]

    raw_transaction = models.ForeignKey(
        RawTransaction, on_delete=models.CASCADE, related_name='logical_transactions'
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='logical_transactions')
    date = models.DateField()
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    amount_crc = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    amount_usd = models.DecimalField(max_digits=14, decimal_places=6, null=True, blank=True)
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='logical_transactions'
    )
    classification_method = models.CharField(max_length=15, choices=CLASSIFICATION_METHODS, default='unclassified')
    matched_rule = models.ForeignKey(
        'ClassificationRule', on_delete=models.SET_NULL, null=True, blank=True, related_name='matched_transactions'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    @property
    def currency(self):
        return self.raw_transaction.ledger.currency

    @property
    def account_type(self):
        return self.raw_transaction.ledger.statement_import.account.account_type

    @property
    def ledger(self):
        return self.raw_transaction.ledger

    @property
    def account_metadata(self):
        return self.raw_transaction.account_metadata

    def __str__(self):
        return f"{self.date} | {self.description[:40]} | {self.amount} {self.currency}"


# Keep Transaction as an alias during migration
Transaction = LogicalTransaction


class ClassificationRule(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='classification_rules')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='classification_rules')
    description = models.CharField(max_length=200, blank=True, help_text='Case-insensitive substring match')
    account_type = models.CharField(max_length=20, blank=True, choices=Account.ACCOUNT_TYPES)
    amount_min = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    amount_max = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True, help_text='Key-value conditions, e.g. {"transaction_code": "PT"}')
    detail = models.CharField(max_length=500, blank=True, help_text='Documentation note')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['category__group__slug', 'category__name', 'description']

    def __str__(self):
        parts = []
        if self.description:
            parts.append(self.description)
        if self.account_type:
            parts.append(self.account_type)
        for k, v in self.metadata.items():
            parts.append(f'{k}={v}')
        return f"{' | '.join(parts) or '?'} → {self.category.name}"

    def to_flat_dict(self):
        """Convert to the flat dict format used by the classifier."""
        d = {'group': self.category.group.slug, 'category': self.category.name}
        if self.description:
            d['description'] = self.description
        if self.account_type:
            d['account_type'] = self.account_type
        if self.amount_min is not None:
            d['amount_min'] = float(self.amount_min)
        if self.amount_max is not None:
            d['amount_max'] = float(self.amount_max)
        for k, v in self.metadata.items():
            d[f'metadata.{k}'] = v
        if self.detail:
            d['detail'] = self.detail
        return d


class UserPreference(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='preferences')
    transaction_columns = models.JSONField(
        default=dict, blank=True,
        help_text='Column visibility settings for the transaction list, e.g. {"1": true, "2": false}',
    )

    def __str__(self):
        return f"Preferences for {self.user}"
