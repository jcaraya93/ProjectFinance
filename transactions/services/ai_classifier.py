import json
import os
from dotenv import load_dotenv
import google.generativeai as genai
from transactions.models import Transaction, Category, CategoryGroup

load_dotenv()


def classify_with_ai(descriptions, dry_run=False):
    """
    Use Gemini to classify transaction descriptions.
    Returns a dict of {description: category_name} or 'Unclassified' if uncertain.
    """
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        raise ValueError('GEMINI_API_KEY not set in .env')

    genai.configure(api_key=api_key)

    # Build category list grouped by type
    groups = CategoryGroup.objects.prefetch_related('categories').all()
    category_list = []
    for group in groups:
        for cat in group.categories.all():
            if cat.name == 'Unclassified':
                continue
            category_list.append(f'{group.name} > {cat.name}')

    # Build unique descriptions
    unique_descs = list(set(descriptions))

    prompt = f"""You are a personal finance transaction classifier for a Costa Rican bank account.

Given the following categories (format: Group > Category):
{chr(10).join(f'- {c}' for c in category_list)}

Classify each transaction description below into the best matching category.
If you are NOT confident about the classification, use "Unclassified".

Rules:
- Costa Rican merchants: restaurants, cafes, supermarkets, gas stations, pharmacies, etc.
- SINPE MOVIL / SINPE are mobile payment transfers in Costa Rica
- Descriptions may be truncated or have unusual formatting
- Only return the category name (not the group), or "Unclassified"

Return a JSON object mapping each description to its category name.
Only valid JSON, no markdown formatting.

Descriptions:
{chr(10).join(f'- "{d}"' for d in unique_descs)}
"""

    model = genai.GenerativeModel('gemini-2.5-flash')
    response = model.generate_content(prompt)

    # Parse response
    text = response.text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1]
        if text.endswith('```'):
            text = text[:-3]

    try:
        results = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError(f'Failed to parse AI response as JSON: {text[:200]}')

    return results


def apply_ai_classifications(dry_run=False):
    """
    Classify all unclassified transactions using AI.
    Returns (classified_count, skipped_count, results_detail).
    """
    unclassified_txns = Transaction.objects.filter(
        category__group__slug='unclassified'
    ).select_related('category')

    if not unclassified_txns.exists():
        return 0, 0, []

    descriptions = list(unclassified_txns.values_list('description', flat=True))
    ai_results = classify_with_ai(descriptions)

    # Build category lookup
    categories = {c.name: c for c in Category.objects.all()}

    classified = 0
    skipped = 0
    details = []

    for txn in unclassified_txns:
        suggested = ai_results.get(txn.description, 'Unclassified')

        if suggested == 'Unclassified' or suggested not in categories:
            skipped += 1
            details.append((txn.description, suggested, False))
            continue

        if not dry_run:
            txn.category = categories[suggested]
            txn.save(update_fields=['category'])

        classified += 1
        details.append((txn.description, suggested, True))

    return classified, skipped, details
