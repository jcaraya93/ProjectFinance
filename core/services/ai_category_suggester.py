"""AI-powered category suggestion service using Gemini."""
import json
import logging
import os
import time

import google.generativeai as genai

from core.models import LogicalTransaction, Category, CategoryGroup

logger = logging.getLogger(__name__)


def suggest_categories(user, max_patterns=50):
    """Use Gemini AI to suggest new categories based on unclassified transactions.

    Returns a list of dicts:
    [
        {
            "name": "Coffee & Cafes",
            "group": "expense",
            "color": "#795548",
            "reason": "Multiple coffee shop transactions",
            "source": "new" | "default",
            "matching_descriptions": ["STARBUCKS", "CAFE BRITT"],
        },
        ...
    ]
    """
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        raise ValueError('GEMINI_API_KEY not set in .env')

    genai.configure(api_key=api_key)

    # Gather user's existing categories
    existing_cats = []
    existing_names = set()
    for cat in Category.objects.filter(user=user).select_related('group').order_by('group__slug', 'name'):
        if cat.name == 'Unclassified Unclassified':
            continue
        existing_cats.append(f'{cat.group.slug} > {cat.name}')
        existing_names.add(cat.name.lower())

    # Gather transactions in Default categories (any group) — these need categorization
    unclassified_qs = LogicalTransaction.objects.filter(
        user=user,
        category__name='Unclassified Unclassified',
    ).values_list('description', flat=True)

    # Deduplicate and group similar descriptions into patterns
    from collections import Counter
    import re

    raw_descs = list(unclassified_qs)
    total_unclassified = len(raw_descs)

    def _normalize(desc):
        """Strip numbers, locations, trailing codes to find patterns."""
        s = desc.upper().strip()
        s = re.sub(r'\\[A-Z0-9 ]+$', '', s)  # trailing backslash location codes
        s = re.sub(r'[#*]\S+', '', s)  # ticket/reference numbers
        s = re.sub(r'\d{3,}', '', s)  # numbers with 3+ digits
        s = re.sub(r'\s+', ' ', s).strip()
        return s[:30].strip()

    pattern_counts = Counter(_normalize(d) for d in raw_descs)
    top_patterns = pattern_counts.most_common(max_patterns)

    desc_lines = []
    for pattern, count in top_patterns:
        if count > 1:
            desc_lines.append(f'- "{pattern}" ({count} transactions)')
        else:
            desc_lines.append(f'- "{pattern}"')

    if not desc_lines:
        return []

    prompt = f"""You are a personal finance categorization expert for a Costa Rican bank account.

The application has these FIXED groups (cannot be changed):
- expense: money going out (purchases, bills, fees, subscriptions)
- income: money coming in (salary, reimbursements, interest, refunds)
- transaction: money moving between accounts (internal transfers, credit card payments)

The user currently has these categories loaded:
{chr(10).join(f'- {c}' for c in existing_cats) if existing_cats else '(none)'}

Here are {len(desc_lines)} uncategorized transaction patterns ({total_unclassified} total transactions):
{chr(10).join(desc_lines)}

Based on the transaction descriptions, suggest NEW categories that would help organize them.

Rules:
- Do NOT suggest categories the user already has loaded
- Do NOT suggest generic names like "Other", "Unclassified", "Miscellaneous", or "General"
- Each category MUST belong to one of: expense, income, transaction
- Suggest a color hex code for each category
- Group similar transactions under one category
- Costa Rican context: SINPE=mobile payments, TEF=bank transfers, common merchants
- Keep category names concise and consistent with existing naming patterns
- Return ONLY valid JSON, no markdown

Return a JSON array of objects with these fields:
- name: category name (string)
- group: one of "expense", "income", "transfer" (string)
- color: hex color code (string)
- reason: brief explanation (string)
- matching_descriptions: list of 2-5 example transaction descriptions that match (array of strings)
"""

    model = genai.GenerativeModel('gemini-2.5-flash')
    t0 = time.monotonic()

    try:
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type='application/json',
                temperature=0.3,
            ),
        )
        elapsed = time.monotonic() - t0
        logger.info('AI category suggestion took %.1fs', elapsed)

        raw_text = response.text.strip()
        suggestions = json.loads(raw_text)
        logger.info('AI raw response: %d items', len(suggestions) if isinstance(suggestions, list) else 0)

        if not isinstance(suggestions, list):
            logger.warning('AI returned non-list: %s', type(suggestions))
            return []

        # Validate and clean suggestions
        valid = []
        seen_names = set()
        for s in suggestions:
            name = s.get('name', '').strip()
            group = s.get('group', '').strip()
            if not name or not group:
                logger.debug('Skipping empty name/group: %s', s)
                continue
            if group not in ('expense', 'income', 'transfer'):
                logger.debug('Skipping invalid group %s for %s', group, name)
                continue
            if name.lower() in existing_names:
                logger.debug('Skipping existing category: %s', name)
                continue
            if name.lower() in ('other', 'default', 'miscellaneous', 'general', 'uncategorized', 'unclassified'):
                logger.debug('Skipping generic category: %s', name)
                continue
            if name.lower() in seen_names:
                continue
            seen_names.add(name.lower())

            valid.append({
                'name': name,
                'group': group,
                'color': s.get('color', '#6c757d'),
                'reason': s.get('reason', ''),
                'matching_descriptions': s.get('matching_descriptions', [])[:5],
            })

        return valid

    except json.JSONDecodeError as e:
        logger.error('AI returned invalid JSON: %s', e)
        raise ValueError(f'AI returned invalid response: {e}')
    except Exception as e:
        logger.error('AI category suggestion failed: %s', e)
        raise
