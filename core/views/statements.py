import logging
import os
import time

from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST

from ..models import (
    Transaction, LogicalTransaction, RawTransaction, Category,
    CurrencyLedger, Account, CreditAccount, DebitAccount,
)
from ..ratelimit import ratelimit
from ._helpers import _safe_next_url

logger = logging.getLogger(__name__)

ALLOWED_UPLOAD_EXTENSIONS = {'.csv'}

__all__ = [
    'statement_list',
    'purge_all_data',
    'upload',
    'upload_file_api',
]


@login_required
def statement_list(request):
    from decimal import Decimal

    # Build wallet list (account + currency combos)
    wallets_qs = (
        CurrencyLedger.objects.filter(user=request.user)
        .select_related('statement_import__account')
        .values('statement_import__account__id', 'statement_import__account__nickname',
                'statement_import__account__account_type', 'currency')
        .distinct()
        .order_by('-statement_import__account__account_type', 'statement_import__account__nickname', 'currency')
    )
    wallet_list = []
    for w in wallets_qs:
        acct_id = w['statement_import__account__id']
        currency = w['currency']
        nickname = w['statement_import__account__nickname']
        key = f"{acct_id}:{currency}"
        wallet_list.append({
            'key': key,
            'label': f"{nickname} — {currency}",
            'account_id': acct_id,
            'currency': currency,
            'account_type': w['statement_import__account__account_type'],
        })

    # Handle selection — default to first wallet
    selected_wallet = request.GET.get('wallet', '')
    if not selected_wallet and wallet_list:
        selected_wallet = wallet_list[0]['key']

    selected_account = None
    typed_account = None
    selected_currency = 'CRC'
    ledgers = []

    if selected_wallet:
        parts = selected_wallet.split(':')
        if len(parts) == 2:
            account_id, selected_currency = parts[0], parts[1]
            try:
                selected_account = Account.objects.filter(user=request.user).get(pk=account_id)
            except Account.DoesNotExist:
                selected_account = None

    if selected_account:
        if selected_account.account_type == 'credit_account':
            try:
                typed_account = selected_account.creditaccount
            except CreditAccount.DoesNotExist:
                typed_account = selected_account
        elif selected_account.account_type == 'debit_account':
            try:
                typed_account = selected_account.debitaccount
            except DebitAccount.DoesNotExist:
                typed_account = selected_account
        else:
            typed_account = selected_account

        currency_symbol = '₡' if selected_currency == 'CRC' else '$'
        decimals = 0 if selected_currency == 'CRC' else 2

        qs = CurrencyLedger.objects.filter(user=request.user).filter(
            currency=selected_currency,
            statement_import__account=selected_account,
        ).select_related('statement_import').prefetch_related(
            'raw_transactions__logical_transactions__category__group'
        ).order_by(
            '-statement_import__statement_date'
        )

        for lg in qs:
            raw_count = RawTransaction.objects.filter(user=request.user).filter(ledger=lg).count()
            txns = LogicalTransaction.objects.filter(user=request.user).filter(raw_transaction__ledger=lg).select_related('category__group')
            lg.raw_count = raw_count
            lg.txn_count = txns.count()
            lg.total_spent = sum(t.amount for t in txns if t.category and t.category.group.slug == 'expense')
            lg.total_payments = abs(sum(t.amount for t in txns if t.category and t.category.group.slug == 'transaction'))
            lg.total_income = sum(t.amount for t in txns if t.category and t.category.group.slug == 'income')
            lg.total_expenses = abs(sum(t.amount for t in txns if t.category and t.category.group.slug == 'expense'))
            lg.points = lg.statement_import.points_assigned
            lg.currency_symbol = currency_symbol
            lg.decimals = decimals
            ledgers.append(lg)

    context = {
        'wallets': wallet_list,
        'selected_wallet': selected_wallet,
        'selected_account': selected_account,
        'typed_account': typed_account,
        'selected_currency': selected_currency,
        'ledgers': ledgers,
        'statement_count': selected_account.statements.count() if selected_account else 0,
        'transaction_count': sum(lg.txn_count for lg in ledgers) if ledgers else 0,
    }
    return render(request, 'core/statement_list.html', context)


@login_required
@require_POST
@ratelimit(key='purge', rate='3/h', method='POST')
def purge_all_data(request):
    """Delete all transactions, statements, and accounts for the current user."""
    confirm = request.POST.get('confirm', '')
    if confirm != 'DELETE ALL':
        messages.error(request, 'Purge cancelled — confirmation text did not match.')
        return redirect('core:statement_list')

    deleted_accounts = Account.objects.filter(user=request.user).delete()
    deleted_exchange = LogicalTransaction.objects.filter(user=request.user).delete()
    deleted_raw = RawTransaction.objects.filter(user=request.user).delete()

    messages.success(request, 'All transactions, statements, and accounts have been deleted.')
    return redirect('core:statement_list')


@login_required
@ratelimit(key='upload', rate='20/h', method='POST')
def upload(request):
    """Render the upload page. File processing is handled by upload_file_api."""
    return render(request, 'core/upload.html')


@login_required
@require_POST
def upload_file_api(request):
    """JSON API: import a single CSV file. Called by the upload page JS."""
    MAX_FILE_SIZE = 10 * 1024 * 1024

    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return JsonResponse({'error': 'No file provided.'}, status=400)

    ext = os.path.splitext(uploaded_file.name)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        return JsonResponse({'error': f'Unsupported file type: {ext}'}, status=400)
    if uploaded_file.size > MAX_FILE_SIZE:
        return JsonResponse({'error': f'File exceeds {MAX_FILE_SIZE // (1024*1024)} MB limit.'}, status=400)

    import hashlib
    from ..services.import_service import import_statement

    raw = uploaded_file.read()
    file_hash = hashlib.sha256(raw).hexdigest()

    try:
        content = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        content = raw.decode('latin-1')

    try:
        t0 = time.monotonic()
        result = import_statement(content, uploaded_file.name, file_hash, request.user)
        elapsed_ms = round((time.monotonic() - t0) * 1000)
    except Exception as e:
        logger.exception('Error importing "%s"', uploaded_file.name)
        return JsonResponse({'error': 'Import failed. File may be corrupted or unsupported.'}, status=500)

    if result.skipped:
        return JsonResponse({
            'status': 'skipped',
            'reason': result.skip_reason,
            'filename': result.filename,
        })

    return JsonResponse({
        'status': 'ok',
        'filename': result.filename,
        'card_type': result.card_type,
        'transaction_count': result.transaction_count,
        'classified_count': result.classified_count,
        'converted_count': result.converted_count,
        'warnings': result.warnings,
        'elapsed_ms': elapsed_ms,
    })
