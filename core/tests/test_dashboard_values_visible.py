from pathlib import Path

import pytest
from django.urls import reverse


def test_dashboard_templates_have_no_value_hiding():
    templates = Path(__file__).resolve().parents[1] / 'templates' / 'core'
    for template in [templates / 'base.html', *templates.glob('dashboard*.html')]:
        content = template.read_text(encoding='utf-8')
        for removed in ('dashboardPrivacy', 'privacyToggle', 'privacy.js', 'data-sensitive', 'isPrivacy', '_dashCharts'):
            assert removed not in content, f'{removed} still present in {template.name}'


@pytest.mark.parametrize('route', [
    'dashboard', 'income_salary_dashboard', 'income_bonus_dashboard',
    'reimbursement_overview_dashboard', 'bank_income_overview_dashboard',
    'expense_range_dashboard', 'transfer_flow_dashboard', 'internal_transfers_dashboard',
    'credit_transfers_dashboard', 'external_transfers_dashboard', 'manual_classification_dashboard',
    'transaction_pairing_dashboard',
])
def test_dashboard_renders_without_privacy_controls(auth_client, route):
    response = auth_client.get(reverse('core:' + route))
    assert response.status_code == 200
    content = response.content.decode()
    assert 'privacyToggle' not in content
    assert 'privacy.js' not in content
