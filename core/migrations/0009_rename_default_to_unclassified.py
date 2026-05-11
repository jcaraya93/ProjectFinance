from django.db import migrations


def rename_default_to_unclassified(apps, schema_editor):
    Category = apps.get_model('core', 'Category')
    Category.objects.filter(name='Default').update(name='Unclassified Unclassified')


def rename_unclassified_to_default(apps, schema_editor):
    Category = apps.get_model('core', 'Category')
    Category.objects.filter(name='Unclassified Unclassified').update(name='Default')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_backfill_sign_factor_and_normalized_amount'),
    ]

    operations = [
        migrations.RunPython(
            rename_default_to_unclassified,
            rename_unclassified_to_default,
        ),
    ]
