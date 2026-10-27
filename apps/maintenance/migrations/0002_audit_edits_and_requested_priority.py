from django.db import migrations, models
from django.db.models import F

PRIORITY_CHOICES = [("emergency", "Emergency"), ("urgent", "Urgent"), ("routine", "Routine")]


def copy_priority(apps, schema_editor):
    MaintenanceRequest = apps.get_model("maintenance", "MaintenanceRequest")
    MaintenanceRequest.objects.update(requested_priority=F("priority"))


class Migration(migrations.Migration):
    dependencies = [
        ("maintenance", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="requestevent",
            name="event_type",
            field=models.CharField(
                choices=[("status_change", "Status change"), ("edit", "Edit")],
                default="status_change",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="requestevent",
            name="changes",
            field=models.JSONField(blank=True, default=dict),
        ),
        # Nullable first so existing rows can be backfilled; 0003 makes it NOT NULL.
        migrations.AddField(
            model_name="maintenancerequest",
            name="requested_priority",
            field=models.CharField(choices=PRIORITY_CHOICES, max_length=20, null=True),
        ),
        migrations.RunPython(copy_priority, migrations.RunPython.noop),
    ]
