import json
import os
import time
from dotenv import load_dotenv
import google.generativeai as genai
from core.models import Transaction, Category, CategoryGroup
from core.instrumentation import tracer, ai_classifier_calls, classification_result

load_dotenv()


def classify_with_ai(descriptions, user, dry_run=False):
    """
    Use Gemini to classify transaction descriptions.
    Returns a dict of {description: category_name} or 'Unclassified' if uncertain.
    """
    with tracer.start_as_current_span("ai_classifier.classify") as span:
        unique_descs = list(set(descriptions))
        span.set_attribute("ai.description_count", len(descriptions))
        span.set_attribute("ai.unique_count", len(unique_descs))
        span.set_attribute("ai.dry_run", dry_run)

        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            ai_classifier_calls.add(1, {"outcome": "error", "error_type": "no_api_key"})
            raise ValueError('GEMINI_API_KEY not set in .env')

        genai.configure(api_key=api_key)

        category_list = []
        for cat in Category.objects.filter(user=user).select_related('group'):
            if cat.name == 'Unclassified':
                continue
            category_list.append(f'{cat.group.name} > {cat.name}')

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
        t0 = time.monotonic()
        try:
            response = model.generate_content(prompt)
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            span.set_attribute("ai.api_duration_ms", elapsed_ms)
            span.set_attribute("ai.error", str(e))
            ai_classifier_calls.add(1, {"outcome": "error", "error_type": type(e).__name__})
            raise

        elapsed_ms = (time.monotonic() - t0) * 1000
        span.set_attribute("ai.api_duration_ms", elapsed_ms)
        span.set_attribute("ai.model", "gemini-2.5-flash")

        text = response.text.strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1]
            if text.endswith('```'):
                text = text[:-3]

        try:
            results = json.loads(text)
        except json.JSONDecodeError:
            ai_classifier_calls.add(1, {"outcome": "error", "error_type": "json_parse"})
            raise ValueError(f'Failed to parse AI response as JSON: {text[:200]}')

        ai_classifier_calls.add(1, {"outcome": "success"})
        span.set_attribute("ai.results_count", len(results))
        return results


def apply_ai_classifications(user, dry_run=False):
    """
    Classify all unclassified transactions using AI.
    Returns (classified_count, skipped_count, results_detail).
    """
    with tracer.start_as_current_span("ai_classifier.apply") as span:
        span.set_attribute("user.id", user.id)
        span.set_attribute("ai.dry_run", dry_run)

        unclassified_txns = Transaction.objects.filter(
            user=user, category__group__slug='unclassified'
        ).select_related('category')

        if not unclassified_txns.exists():
            span.set_attribute("ai.unclassified_count", 0)
            return 0, 0, []

        descriptions = list(unclassified_txns.values_list('description', flat=True))
        span.set_attribute("ai.unclassified_count", len(descriptions))
        ai_results = classify_with_ai(descriptions, user=user)

        categories = {c.name: c for c in Category.objects.filter(user=user)}

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
                classification_result.add(1, {"method": "ai"})

            classified += 1
            details.append((txn.description, suggested, True))

        span.set_attribute("ai.classified_count", classified)
        span.set_attribute("ai.skipped_count", skipped)
        return classified, skipped, details
