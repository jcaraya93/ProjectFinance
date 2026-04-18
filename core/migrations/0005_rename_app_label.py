"""
Data migration to update content types and rename database tables
after app rename from 'transactions' to 'core'.
Run `python manage.py rename_app_prep` BEFORE this migration.
"""

from django.db import migrations


def rename_content_types(apps, schema_editor):
    db = schema_editor.connection.alias
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ContentType.objects.using(db).filter(app_label='transactions').update(app_label='core')


def revert_content_types(apps, schema_editor):
    db = schema_editor.connection.alias
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ContentType.objects.using(db).filter(app_label='core').update(app_label='transactions')


TABLES = [
    'user',
    'user_groups',
    'user_user_permissions',
    'categorygroup',
    'category',
    'account',
    'creditaccount',
    'debitaccount',
    'statementimport',
    'currencyledger',
    'exchangerate',
    'rawtransaction',
    'logicaltransaction',
    'classificationrule',
    'userpreference',
]


def rename_tables(apps, schema_editor):
    """Rename tables from transactions_ to core_ prefix (PostgreSQL only)."""
    if schema_editor.connection.vendor != 'postgresql':
        return
    for t in TABLES:
        schema_editor.execute(
            f'ALTER TABLE IF EXISTS transactions_{t} RENAME TO core_{t};'
        )


def revert_tables(apps, schema_editor):
    """Revert table renames from core_ to transactions_ prefix (PostgreSQL only)."""
    if schema_editor.connection.vendor != 'postgresql':
        return
    for t in TABLES:
        schema_editor.execute(
            f'ALTER TABLE IF EXISTS core_{t} RENAME TO transactions_{t};'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_mask_credit_card_number'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(rename_content_types, revert_content_types),
        migrations.RunPython(rename_tables, revert_tables),
    ]
