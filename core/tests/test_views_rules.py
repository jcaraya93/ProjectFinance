"""Integration tests for rule management views."""
import pytest
from django.urls import reverse

from core.models import ClassificationRule, Transaction, Category


class TestReclassify:
    """POST endpoints for reclassification."""

    def test_reclassify_all(self, auth_client, sample_data):
        resp = auth_client.post(reverse('core:reclassify_all'))
        assert resp.status_code == 302

    def test_classify_unclassified(self, auth_client, sample_data):
        resp = auth_client.post(reverse('core:classify_unclassified'))
        assert resp.status_code == 302
