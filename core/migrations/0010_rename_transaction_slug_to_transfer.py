from django.db import migrations, models


def rename_transaction_to_transfer(apps, schema_editor):
    CategoryGroup = apps.get_model('core', 'CategoryGroup')
    CategoryGroup.objects.filter(slug='transaction').update(slug='transfer')


def rename_transfer_to_transaction(apps, schema_editor):
    CategoryGroup = apps.get_model('core', 'CategoryGroup')
    CategoryGroup.objects.filter(slug='transfer').update(slug='transaction')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_rename_default_to_unclassified'),
    ]

    operations = [
        migrations.AlterField(
            model_name='categorygroup',
            name='slug',
            field=models.CharField(
                choices=[
                    ('expense', 'Expense'),
                    ('income', 'Income'),
                    ('transfer', 'Transfer'),
                    ('unclassified', 'Unclassified'),
                ],
                max_length=20,
                unique=True,
            ),
        ),
        migrations.RunPython(
            rename_transaction_to_transfer,
            rename_transfer_to_transaction,
        ),
    ]
