from core.models import CategoryNode


def income_category_roles(user):
    """Resolve saved roles by category ID; descendants inherit the nearest assigned role."""
    nodes = {node.pk: node for node in CategoryNode.objects.filter(user=user, group__slug='income')}
    roles = {role: set() for role, _ in CategoryNode.INCOME_DASHBOARD_ROLES}
    for node in nodes.values():
        ancestor = node
        while ancestor:
            if ancestor.income_dashboard_role:
                roles[ancestor.income_dashboard_role].add(node.pk)
                break
            ancestor = nodes.get(ancestor.parent_id)
    return roles


def dashboard_income_ids(request, *roles):
    from django.contrib import messages

    assignments = income_category_roles(request.user)
    ids = set().union(*(assignments[role] for role in roles))
    if not ids:
        messages.warning(request, 'No income categories are assigned to this dashboard. Edit an income category on the Categories page and choose its Income dashboard role.')
    return ids
