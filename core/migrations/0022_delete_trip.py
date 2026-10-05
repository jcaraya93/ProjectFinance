from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0021_trips_to_tags'),
    ]

    operations = [
        migrations.RemoveConstraint(model_name='trip', name='trip_end_not_before_start'),
        migrations.DeleteModel(name='Trip'),
    ]
