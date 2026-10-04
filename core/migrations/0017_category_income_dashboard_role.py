from django.db import migrations, models


def assign_roles(apps, schema_editor):
    CategoryNode = apps.get_model('core', 'CategoryNode')
    nodes = list(CategoryNode.objects.using(schema_editor.connection.alias).filter(group__slug='income'))
    by_id = {node.pk: node for node in nodes}
    legacy = {
        'Work Salary': 'salary', 'Work Bonuses': 'bonus', 'Work Association': 'association',
        'Work Government': 'government', 'Reimbursement': 'reimbursement', 'Bank': 'bank',
        **{f'Reimbursement {name}': 'reimbursement' for name in ('General', 'Housing', 'Insurance', 'Partner')},
        **{f'Bank Interest {name}': 'bank' for name in ('CDP', 'Cashback', 'Reversals', 'Credit')},
    }
    work = {'Salary': 'salary', 'Bonuses': 'bonus', 'Association': 'association', 'Government': 'government'}
    for node in nodes:
        role = legacy.get(node.name, '')
        parent = by_id.get(node.parent_id)
        if not role and parent and parent.name == 'Work':
            role = work.get(node.name, '')
        if role:
            CategoryNode.objects.using(schema_editor.connection.alias).filter(pk=node.pk).update(income_dashboard_role=role)


class Migration(migrations.Migration):
    dependencies = [('core', '0016_logicaltransaction_note')]
    operations = [
        migrations.AddField(
            model_name='categorynode', name='income_dashboard_role',
            field=models.CharField(blank=True, default='', max_length=20, choices=[
                ('salary', 'Salary'), ('bonus', 'Bonuses'), ('association', 'Association'),
                ('government', 'Government'), ('reimbursement', 'Reimbursement'), ('bank', 'Bank Income'),
            ]),
        ),
        migrations.RunPython(assign_roles, migrations.RunPython.noop),
    ]
