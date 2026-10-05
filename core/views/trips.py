from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from ..models import Trip, Transaction

__all__ = ['trip_list', 'trip_save', 'trip_delete']


def _parse_date(value):
    try:
        return date.fromisoformat((value or '').strip())
    except ValueError:
        return None


@login_required
def trip_list(request):
    trips = list(Trip.objects.filter(user=request.user))
    txns = Transaction.objects.filter(user=request.user)
    for trip in trips:
        trip.txn_count = txns.filter(date__range=(trip.start_date, trip.end_date)).count()
        trip.txn_url = reverse('core:transaction_list') + '?' + urlencode([
            ('start_date', trip.start_date.isoformat()), ('end_date', trip.end_date.isoformat()),
            ('return_to', request.get_full_path()),
        ])
    editing = None
    edit_id = request.GET.get('edit', '')
    if edit_id.isdigit():
        editing = next((t for t in trips if t.pk == int(edit_id)), None)
    return render(request, 'core/trip_list.html', {'trips': trips, 'editing': editing})


@login_required
@require_POST
def trip_save(request):
    """Create a trip, or update one when `id` is supplied."""
    trip_id = request.POST.get('id')
    name = request.POST.get('name', '').strip()
    start = _parse_date(request.POST.get('start_date'))
    end = _parse_date(request.POST.get('end_date'))
    notes = request.POST.get('notes', '').strip()

    error = None
    if not name:
        error = 'Trip name is required.'
    elif not start or not end:
        error = 'Valid start and end dates are required.'
    elif end < start:
        error = 'The end date cannot be before the start date.'
    elif len(name) > 100 or len(notes) > 500:
        error = 'Name or notes are too long.'
    if error:
        messages.error(request, error)
        return redirect(f"{reverse('core:trip_list')}?edit={trip_id}" if trip_id else 'core:trip_list')

    if trip_id:
        trip = get_object_or_404(Trip, pk=trip_id, user=request.user)
    else:
        trip = Trip(user=request.user)
    trip.name, trip.start_date, trip.end_date, trip.notes = name, start, end, notes
    trip.save()
    messages.success(request, f'Trip "{trip.name}" saved.')
    return redirect('core:trip_list')


@login_required
@require_POST
def trip_delete(request):
    trip = get_object_or_404(Trip, pk=request.POST.get('id') or 0, user=request.user)
    trip.delete()
    messages.success(request, 'Trip deleted.')
    return redirect('core:trip_list')
