from datetime import date

import pytest
from django.urls import reverse

from core.models import Trip, User

pytestmark = pytest.mark.django_db


class TestTripViews:
    def test_dropdown_links_to_trips(self, auth_client):
        html = auth_client.get(reverse('core:trip_list')).content.decode()
        assert reverse('core:trip_list') in html

    def test_create_trip(self, auth_client, user):
        resp = auth_client.post(reverse('core:trip_save'), {
            'name': 'Mexico', 'start_date': '2025-10-27', 'end_date': '2025-11-03', 'notes': 'CDMX'})
        assert resp.status_code == 302
        trip = Trip.objects.get(user=user)
        assert (trip.name, trip.start_date, trip.end_date) == ('Mexico', date(2025, 10, 27), date(2025, 11, 3))
        assert trip.duration_days == 8
        assert 'Mexico' in auth_client.get(reverse('core:trip_list')).content.decode()

    def test_rejects_invalid_input(self, auth_client):
        url = reverse('core:trip_save')
        auth_client.post(url, {'name': '', 'start_date': '2025-01-01', 'end_date': '2025-01-02'})
        auth_client.post(url, {'name': 'X', 'start_date': '2025-01-05', 'end_date': '2025-01-02'})
        auth_client.post(url, {'name': 'X', 'start_date': 'nope', 'end_date': '2025-01-02'})
        assert not Trip.objects.exists()

    def test_edit_and_delete(self, auth_client, user):
        trip = Trip.objects.create(user=user, name='A', start_date=date(2025, 1, 1), end_date=date(2025, 1, 2))
        assert 'value="A"' in auth_client.get(reverse('core:trip_list'), {'edit': trip.pk}).content.decode()
        auth_client.post(reverse('core:trip_save'), {
            'id': trip.pk, 'name': 'B', 'start_date': '2025-01-01', 'end_date': '2025-01-09'})
        trip.refresh_from_db()
        assert (trip.name, trip.end_date) == ('B', date(2025, 1, 9))
        auth_client.post(reverse('core:trip_delete'), {'id': trip.pk})
        assert not Trip.objects.exists()

    def test_cannot_touch_other_users_trips(self, auth_client):
        other = User.objects.create_user(email='other@example.com', password='x')
        theirs = Trip.objects.create(user=other, name='T', start_date=date(2025, 1, 1), end_date=date(2025, 1, 2))
        assert auth_client.post(reverse('core:trip_save'), {
            'id': theirs.pk, 'name': 'hijack', 'start_date': '2025-01-01', 'end_date': '2025-01-02'}).status_code == 404
        assert auth_client.post(reverse('core:trip_delete'), {'id': theirs.pk}).status_code == 404
        assert 'T' == Trip.objects.get(pk=theirs.pk).name
