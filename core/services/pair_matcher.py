"""Auto-matching service for transfer transaction pairs.

Scans RawTransactions whose LogicalTransaction is in the transfer group
(Internal, Credit categories) and creates TransactionPair records linking
the outgoing and incoming sides.
"""
import logging
from collections import defaultdict
from dataclasses import dataclass

from core.models import (
    RawTransaction, LogicalTransaction, TransactionPair,
)

logger = logging.getLogger(__name__)

PAIRABLE_CATEGORIES = ['Internal', 'Credit']


@dataclass
class MatchResult:
    """Summary of an auto-match run."""
    paired: int = 0
    unmatched: int = 0
    skipped: int = 0


def auto_match_transfers(user, dry_run=False):
    """Find and persist transfer pairs for the given user.

    Returns a MatchResult with counts.
    """
    result = MatchResult()

    # Get all RawTransactions in pairable categories that are NOT already paired
    already_paired_out = set(
        TransactionPair.objects.filter(user=user, outgoing__isnull=False)
        .values_list('outgoing_id', flat=True)
    )
    already_paired_in = set(
        TransactionPair.objects.filter(user=user, incoming__isnull=False)
        .values_list('incoming_id', flat=True)
    )
    already_paired = already_paired_out | already_paired_in

    # Get candidate LogicalTransactions in transfer group with pairable categories
    logical_txns = (
        LogicalTransaction.objects.filter(
            user=user,
            category__group__slug='transaction',
            category__name__in=PAIRABLE_CATEGORIES,
        )
        .select_related(
            'raw_transaction__ledger__statement_import__account',
            'category',
        )
    )

    # Build candidate list with raw transaction info
    candidates = []
    for lt in logical_txns:
        raw = lt.raw_transaction
        if raw.id in already_paired:
            result.skipped += 1
            continue

        ledger = raw.ledger
        stmt = ledger.statement_import
        acct = stmt.account

        candidates.append({
            'raw_id': raw.id,
            'raw': raw,
            'date': lt.date,
            'amount_crc': float(lt.amount_crc or 0),
            'amount_usd': float(lt.amount_usd or 0),
            'description': lt.description,
            'account_id': acct.id if acct else None,
            'account_name': str(acct) if acct else 'Unknown',
            'is_credit_account': hasattr(acct, 'creditaccount') if acct else False,
            'category': lt.category.name,
        })

    # Match pairs: different accounts, opposite signs, close amounts, close dates
    matched = set()
    pairs_to_create = []
    unmatched_to_create = []

    for i, ci in enumerate(candidates):
        if ci['raw_id'] in matched:
            continue

        best_match = None
        best_diff = float('inf')

        for j, cj in enumerate(candidates):
            if j <= i or cj['raw_id'] in matched:
                continue
            # Must be different accounts
            if ci['account_id'] == cj['account_id']:
                continue
            # Must be opposite signs in CRC — except Credit category
            # where both sides are negative (debit payment + credit card receipt)
            same_sign = ci['amount_crc'] * cj['amount_crc'] > 0
            opposite_sign = ci['amount_crc'] * cj['amount_crc'] < 0
            is_credit_pair = (ci['category'] == 'Credit' or cj['category'] == 'Credit')

            if is_credit_pair:
                # Credit pairs: same sign (both negative), different account types
                if not same_sign:
                    continue
                if ci['is_credit_account'] == cj['is_credit_account']:
                    continue
            else:
                # Internal pairs: opposite signs
                if not opposite_sign:
                    continue

            # Date tolerance: Internal ±1 day, Credit ±2 days
            day_diff = abs((ci['date'] - cj['date']).days)
            max_days = 1 if ci['category'] == 'Internal' else 2
            if day_diff > max_days:
                continue

            # Amount matching: CRC or USD, within tolerance
            crc_diff = abs(abs(ci['amount_crc']) - abs(cj['amount_crc'])) if ci['amount_crc'] and cj['amount_crc'] else float('inf')
            usd_diff = abs(abs(ci['amount_usd']) - abs(cj['amount_usd'])) if ci['amount_usd'] and cj['amount_usd'] else float('inf')
            amt_diff = min(crc_diff, usd_diff)
            threshold = min(abs(ci['amount_crc']), abs(cj['amount_crc'])) * 0.02 + 5000

            if amt_diff < threshold and amt_diff < best_diff:
                best_diff = amt_diff
                best_match = j

        if best_match is not None:
            cj = candidates[best_match]
            matched.add(ci['raw_id'])
            matched.add(cj['raw_id'])

            # Determine outgoing and incoming
            if is_credit_pair:
                # For credit pairs: debit account is outgoing, credit account is incoming
                if ci['is_credit_account']:
                    out_raw, in_raw = cj['raw'], ci['raw']
                else:
                    out_raw, in_raw = ci['raw'], cj['raw']
            else:
                # For internal: negative CRC is outgoing
                if ci['amount_crc'] < 0:
                    out_raw, in_raw = ci['raw'], cj['raw']
                else:
                    out_raw, in_raw = cj['raw'], ci['raw']

            pairs_to_create.append(TransactionPair(
                user=user,
                outgoing=out_raw,
                incoming=in_raw,
                match_method='auto',
                status='paired',
            ))
            result.paired += 1
        else:
            # No match found — create unmatched record
            if ci['raw_id'] not in matched:
                if ci['amount_crc'] < 0:
                    unmatched_to_create.append(TransactionPair(
                        user=user,
                        outgoing=ci['raw'],
                        incoming=None,
                        match_method='auto',
                        status='unmatched',
                    ))
                else:
                    unmatched_to_create.append(TransactionPair(
                        user=user,
                        outgoing=None,
                        incoming=ci['raw'],
                        match_method='auto',
                        status='unmatched',
                    ))
                matched.add(ci['raw_id'])
                result.unmatched += 1

    if not dry_run:
        TransactionPair.objects.bulk_create(pairs_to_create + unmatched_to_create)
        logger.info(
            "Auto-match complete: %d paired, %d unmatched, %d skipped",
            result.paired, result.unmatched, result.skipped,
        )

    return result
