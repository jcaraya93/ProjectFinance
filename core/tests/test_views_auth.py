"""Integration tests for authentication views."""
import pytest
from django.urls import reverse

from core.models import User


class TestLoginSmoke:
    """Login and register pages render."""

    def test_login_page(self, client):
        resp = client.get('/auth/login/')
        assert resp.status_code == 200

    def test_register_page(self, client):
        resp = client.get('/auth/register/')
        assert resp.status_code == 200


class TestLoginAction:
    """POST /auth/login/ authenticates users."""

    def test_login_success(self, client, user):
        resp = client.post('/auth/login/', {
            'email': 'test@example.com',
            'password': 'testpass123!',
        })
        assert resp.status_code == 302
        assert resp.url == '/'

    def test_login_failure(self, client, user):
        resp = client.post('/auth/login/', {
            'email': 'test@example.com',
            'password': 'wrongpassword',
        })
        assert resp.status_code == 200  # Re-renders form

    def test_login_redirect_next(self, client, user):
        resp = client.post('/auth/login/?next=/categories-v2/', {
            'email': 'test@example.com',
            'password': 'testpass123!',
        })
        assert resp.status_code == 302
        assert resp.url == '/categories-v2/'


class TestRegisterAction:
    """POST /auth/register/ creates accounts."""

    def test_register_success(self, client, db):
        resp = client.post('/auth/register/', {
            'email': 'new@example.com',
            'password': 'Str0ngP@ssword!',
            'password_confirm': 'Str0ngP@ssword!',
        })
        assert resp.status_code == 302
        assert User.objects.filter(email='new@example.com').exists()

    def test_register_loads_default_categories(self, client, db):
        client.post('/auth/register/', {
            'email': 'seeded@example.com',
            'password': 'Str0ngP@ssword!',
            'password_confirm': 'Str0ngP@ssword!',
        })
        from core.models import CategoryNode
        nodes = CategoryNode.objects.filter(user__email='seeded@example.com')
        assert nodes.count() > 4
        assert nodes.filter(parent__isnull=False).exists()
        assert nodes.filter(name='Unclassified', parent__isnull=True).count() == 4

    def test_register_duplicate_email(self, client, user):
        resp = client.post('/auth/register/', {
            'email': 'test@example.com',
            'password': 'Str0ngP@ssword!',
            'password_confirm': 'Str0ngP@ssword!',
        })
        assert resp.status_code == 200  # Re-renders with error

    def test_register_password_mismatch(self, client, db):
        resp = client.post('/auth/register/', {
            'email': 'new@example.com',
            'password': 'Str0ngP@ssword!',
            'password_confirm': 'different',
        })
        assert resp.status_code == 200
        assert not User.objects.filter(email='new@example.com').exists()


class TestLogout:
    """POST /auth/logout/ logs out."""

    def test_logout(self, auth_client):
        resp = auth_client.post('/auth/logout/')
        assert resp.status_code == 302


class TestUnauthenticatedRedirect:
    """Protected pages redirect to login."""

    @pytest.mark.parametrize('url', [
        '/categories-v2/',
        '/transactions/',
        '/statements/',
        '/rules-v2/',
        '/upload/',
        '/',
    ])
    def test_redirect_to_login(self, client, db, url):
        resp = client.get(url)
        assert resp.status_code == 302
        assert '/auth/login/' in resp.url
