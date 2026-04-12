from django.core.management.base import BaseCommand
from transactions.models import Category, CategoryGroup, ClassificationRule, User
from transactions.services.yaml_classifier import load_yaml
from transactions.instrumentation import tracer


class Command(BaseCommand):
    help = 'Sync categories, groups, and rules from classification_rules.yaml'

    def add_arguments(self, parser):
        parser.add_argument('--user', type=str, help='User email to assign categories/rules to')

    def handle(self, *args, **options):
        with tracer.start_as_current_span("seed_categories.handle") as span:
            user_email = options.get('user')
            if user_email:
                user = User.objects.get(email=user_email)
            else:
                user = User.objects.first()
                if not user:
                    self.stderr.write('No users exist. Create one first or pass --user.')
                    return

            data = load_yaml()
            groups = data.get('groups', {})

            created_groups = 0
            created_cats = 0

            for slug, group_info in groups.items():
                grp, created = CategoryGroup.objects.get_or_create(
                    slug=slug, defaults={'name': group_info.get('name', slug.title())}
                )
                if created:
                    created_groups += 1

                for cat_name, cat_info in group_info.get('categories', {}).items():
                    color = cat_info.get('color', '#6c757d')
                    _, created = Category.objects.get_or_create(
                        name=cat_name, group=grp, user=user, defaults={'color': color}
                    )
                    if created:
                        created_cats += 1

            self.stdout.write(f'Groups: {created_groups} created. Categories: {created_cats} created (user: {user.email}).')

            # Import rules from YAML into DB if DB has no rules for this user
            if ClassificationRule.objects.filter(user=user).exists():
                self.stdout.write('Rules already in DB, skipping YAML import.')
            else:
                created_rules = 0
                for slug, group_info in groups.items():
                    for cat_name, cat_info in group_info.get('categories', {}).items():
                        cat = Category.objects.filter(name=cat_name, group__slug=slug, user=user).first()
                        if not cat:
                            continue
                        for rule in cat_info.get('rules', []):
                            metadata = {}
                            kwargs = {'category': cat, 'user': user, 'detail': rule.get('detail', '')}
                            if rule.get('description'):
                                kwargs['description'] = rule['description']
                            if rule.get('account_type'):
                                kwargs['account_type'] = rule['account_type']
                            if rule.get('amount_min') is not None:
                                kwargs['amount_min'] = rule['amount_min']
                            if rule.get('amount_max') is not None:
                                kwargs['amount_max'] = rule['amount_max']
                            for k, v in rule.items():
                                if k.startswith('metadata.'):
                                    metadata[k[9:]] = v
                            kwargs['metadata'] = metadata
                            ClassificationRule.objects.create(**kwargs)
                            created_rules += 1

                self.stdout.write(self.style.SUCCESS(f'Imported {created_rules} rules from YAML.'))

            span.set_attribute("seed.groups_created", created_groups)
            span.set_attribute("seed.categories_created", created_cats)
