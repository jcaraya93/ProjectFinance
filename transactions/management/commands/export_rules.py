from django.core.management.base import BaseCommand
from transactions.models import ClassificationRule, CategoryGroup
from transactions.services.yaml_classifier import load_yaml, save_yaml
from transactions.instrumentation import tracer


class Command(BaseCommand):
    help = 'Export classification rules from DB to classification_rules.yaml'

    def handle(self, *args, **options):
        with tracer.start_as_current_span("export_rules.handle") as span:
            data = load_yaml()
            groups = data.get('groups', {})

            # Ensure all groups/categories exist in YAML structure
            for grp in CategoryGroup.objects.prefetch_related('categories').all():
                if grp.slug not in groups:
                    groups[grp.slug] = {'name': grp.name, 'categories': {}}
                for cat in grp.categories.all():
                    if cat.name not in groups[grp.slug].get('categories', {}):
                        groups[grp.slug].setdefault('categories', {})[cat.name] = {
                            'color': cat.color, 'rules': []
                        }

            # Clear all rules in YAML
            for grp_info in groups.values():
                for cat_info in grp_info.get('categories', {}).values():
                    cat_info['rules'] = []

            # Write DB rules into YAML
            count = 0
            for rule in ClassificationRule.objects.select_related('category__group').all():
                grp_slug = rule.category.group.slug
                cat_name = rule.category.name
                cat_info = groups.get(grp_slug, {}).get('categories', {}).get(cat_name)
                if cat_info is None:
                    continue
                r = {}
                if rule.description:
                    r['description'] = rule.description
                if rule.account_type:
                    r['account_type'] = rule.account_type
                if rule.amount_min is not None:
                    r['amount_min'] = float(rule.amount_min)
                if rule.amount_max is not None:
                    r['amount_max'] = float(rule.amount_max)
                for k, v in rule.metadata.items():
                    r[f'metadata.{k}'] = v
                if rule.detail:
                    r['detail'] = rule.detail
                cat_info['rules'].append(r)
                count += 1

            data['groups'] = groups
            save_yaml(data)
            span.set_attribute("export.rule_count", count)
            self.stdout.write(self.style.SUCCESS(f'Exported {count} rules to YAML.'))
