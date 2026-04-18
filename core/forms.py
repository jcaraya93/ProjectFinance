from django import forms
from .models import Category, CategoryGroup


class UploadForm(forms.Form):
    pass  # File input handled in template (HTML multiple), card type auto-detected


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ['name', 'color', 'group']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'color': forms.TextInput(attrs={'class': 'form-control form-control-color', 'type': 'color'}),
            'group': forms.Select(attrs={'class': 'form-select'}),
        }


ACCOUNT_TYPE_CHOICES = [
    ('', '— Any —'),
    ('credit_account', 'Credit Account'),
    ('debit_account', 'Debit Account'),
]

GROUP_CHOICES = [
    ('expense', 'Expense'),
    ('income', 'Income'),
    ('transaction', 'Transfer'),
]


class YamlRuleForm(forms.Form):
    """Form for adding/editing a YAML classification rule."""
    description = forms.CharField(
        required=False, max_length=200,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. STARBUCKS'}),
    )
    metadata_key = forms.CharField(
        required=False, max_length=50, label='Metadata key',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. transaction_code'}),
    )
    metadata_value = forms.CharField(
        required=False, max_length=100, label='Metadata value',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. PT'}),
    )
    account_type = forms.ChoiceField(
        required=False, choices=ACCOUNT_TYPE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    amount_min = forms.DecimalField(
        required=False, max_digits=14, decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Min', 'step': '0.01'}),
    )
    amount_max = forms.DecimalField(
        required=False, max_digits=14, decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Max', 'step': '0.01'}),
    )
    group = forms.ChoiceField(
        choices=GROUP_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    category = forms.ChoiceField(
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    detail = forms.CharField(
        required=False, max_length=500, label='Detail (documentation)',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional note about this rule'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import Category
        # Build category choices keyed by "group_slug:name" to handle duplicates
        choices = []
        self._group_map = {}  # "group_slug:name" -> group_slug
        for cat in Category.objects.select_related('group').order_by('group__name', 'name'):
            key = f'{cat.group.slug}:{cat.name}'
            choices.append((key, cat.name))
            self._group_map[key] = cat.group.slug
        self.fields['category'].choices = choices

    def get_group_categories_json(self):
        """Return JSON mapping group_slug -> [category keys] for JS."""
        import json
        mapping = {}
        for key, group_slug in self._group_map.items():
            mapping.setdefault(group_slug, []).append(key)
        for k in mapping:
            mapping[k].sort()
        return json.dumps(mapping)
        for k in mapping:
            mapping[k].sort()
        return json.dumps(mapping)

    def clean(self):
        cleaned = super().clean()
        desc = cleaned.get('description', '').strip()
        meta_key = cleaned.get('metadata_key', '').strip()
        meta_val = cleaned.get('metadata_value', '').strip()
        amt_min = cleaned.get('amount_min')
        amt_max = cleaned.get('amount_max')
        acct = cleaned.get('account_type', '').strip()
        if not desc and not meta_key and amt_min is None and amt_max is None and not acct:
            raise forms.ValidationError('At least one condition is required (description, metadata, amount range, or account type).')
        if bool(meta_key) != bool(meta_val):
            raise forms.ValidationError('Both metadata key and value are required together.')
        return cleaned

    def to_rule_dict(self):
        """Convert form data to a YAML rule dict."""
        data = self.cleaned_data
        rule = {}
        if data.get('description', '').strip():
            rule['description'] = data['description'].strip()
        meta_key = data.get('metadata_key', '').strip()
        meta_val = data.get('metadata_value', '').strip()
        if meta_key and meta_val:
            rule[f'metadata.{meta_key}'] = meta_val
        if data.get('account_type', '').strip():
            rule['account_type'] = data['account_type'].strip()
        if data.get('amount_min') is not None:
            rule['amount_min'] = float(data['amount_min'])
        if data.get('amount_max') is not None:
            rule['amount_max'] = float(data['amount_max'])
        rule['group'] = data['group']
        # category value is "group_slug:name" — extract just the name
        cat_val = data['category']
        if ':' in cat_val:
            rule['category'] = cat_val.split(':', 1)[1]
        else:
            rule['category'] = cat_val.strip()
        if data.get('detail', '').strip():
            rule['detail'] = data['detail'].strip()
        return rule
