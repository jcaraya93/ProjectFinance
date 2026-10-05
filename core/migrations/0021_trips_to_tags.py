from django.db import migrations


def trips_to_tags(apps, schema_editor):
    Trip = apps.get_model('core', 'Trip')
    Tag = apps.get_model('core', 'Tag')
    TagGroup = apps.get_model('core', 'TagGroup')
    for trip in Trip.objects.all().order_by('start_date', 'pk'):
        group, _ = TagGroup.objects.get_or_create(user_id=trip.user_id, name='Trips')
        name = trip.name[:50]
        if Tag.objects.filter(user_id=trip.user_id, name__iexact=name).exists():
            name = f'{name[:43]} (trip)'
        Tag.objects.create(
            user_id=trip.user_id, group=group, name=name, notes=trip.notes,
            start_date=trip.start_date, end_date=trip.end_date,
        )


def tags_to_trips(apps, schema_editor):
    Trip = apps.get_model('core', 'Trip')
    Tag = apps.get_model('core', 'Tag')
    for tag in Tag.objects.filter(start_date__isnull=False):
        Trip.objects.create(user_id=tag.user_id, name=tag.name, notes=tag.notes,
                            start_date=tag.start_date, end_date=tag.end_date)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0020_tag_groups_and_dates'),
    ]

    operations = [
        migrations.RunPython(trips_to_tags, tags_to_trips),
    ]
