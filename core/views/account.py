import json
import logging
import time

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST, require_GET

from ..services.user_data_io import export_user_data, import_user_data
from ..services.user_data_io import ImportError as DataImportError

logger = logging.getLogger(__name__)

__all__ = [
    'account_page',
    'export_data_api',
    'import_data_api',
]

MAX_IMPORT_SIZE = 50 * 1024 * 1024  # 50 MB


@login_required
def account_page(request):
    """Render the account management page with export/import controls."""
    from ..models import (
        Account, ClassificationRule, Category, StatementImport,
        LogicalTransaction,
    )

    acct_count = Account.objects.filter(user=request.user).count()
    stmt_count = StatementImport.objects.filter(user=request.user).count()
    txn_count = LogicalTransaction.objects.filter(user=request.user).count()
    cat_count = Category.objects.filter(user=request.user).exclude(name=Category.UNCLASSIFIED_NAME).count()
    rule_count = ClassificationRule.objects.filter(user=request.user).count()

    has_data = (acct_count + stmt_count + cat_count + rule_count) > 0

    return render(request, 'core/account.html', {
        'has_data': has_data,
        'user': request.user,
        'acct_count': acct_count,
        'stmt_count': stmt_count,
        'txn_count': txn_count,
        'cat_count': cat_count,
        'rule_count': rule_count,
    })


@login_required
@require_GET
def export_data_api(request):
    """Stream the user's full data as a JSON file download."""
    try:
        t0 = time.monotonic()
        data = export_user_data(request.user)
        elapsed_ms = round((time.monotonic() - t0) * 1000)
        logger.info('Exported data for %s in %dms', request.user.email, elapsed_ms)
    except Exception:
        logger.exception('Export failed for %s', request.user.email)
        return JsonResponse({'error': 'Export failed. Please try again.'}, status=500)

    content = json.dumps(data, indent=2, ensure_ascii=False)
    response = HttpResponse(content, content_type='application/json')
    response['Content-Disposition'] = f'attachment; filename="project-finance-backup.json"'
    return response


@login_required
@require_POST
def import_data_api(request):
    """Accept a JSON backup file and restore into the user's account."""
    uploaded = request.FILES.get('file')
    if not uploaded:
        return JsonResponse({'error': 'No file provided.'}, status=400)

    if uploaded.size > MAX_IMPORT_SIZE:
        return JsonResponse(
            {'error': f'File exceeds {MAX_IMPORT_SIZE // (1024 * 1024)} MB limit.'},
            status=400,
        )

    try:
        raw = uploaded.read()
        data = json.loads(raw.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON file.'}, status=400)

    try:
        t0 = time.monotonic()
        counts = import_user_data(request.user, data)
        elapsed_ms = round((time.monotonic() - t0) * 1000)
        logger.info('Imported data for %s in %dms: %s', request.user.email, elapsed_ms, counts)
    except DataImportError as e:
        return JsonResponse({'error': str(e)}, status=400)
    except Exception:
        logger.exception('Import failed for %s', request.user.email)
        return JsonResponse({'error': 'Import failed. The file may be corrupted.'}, status=500)

    return JsonResponse({
        'status': 'ok',
        'counts': counts,
        'elapsed_ms': elapsed_ms,
    })
