from django.db import migrations, models

PRIORITY_CHOICES = [("emergency", "Emergency"), ("urgent", "Urgent"), ("routine", "Routine")]


class Migration(migrations.Migration):
    # Separate from 0002 so the NOT NULL change runs in its own transaction,
    # after the backfill has committed.
    dependencies = [
        ("maintenance", "0002_audit_edits_and_requested_priority"),
    ]

    operations = [
        migrations.AlterField(
            model_name="maintenancerequest",
            name="requested_priority",
            field=models.CharField(choices=PRIORITY_CHOICES, max_length=20),
        ),
    ]
