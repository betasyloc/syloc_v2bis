# Generated manually: RECEIVED → PENDING (Envoyée), suppression du libellé « Reçue ».

from django.db import migrations, models


def received_to_pending(apps, schema_editor):
    MaintenanceRequest = apps.get_model("rental", "MaintenanceRequest")
    MaintenanceRequest.objects.filter(status="RECEIVED").update(status="PENDING")


class Migration(migrations.Migration):

    dependencies = [
        ("rental", "0031_maintenance_acknowledged_at"),
    ]

    operations = [
        migrations.RunPython(received_to_pending, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="maintenancerequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Envoyée"),
                    ("IN_PROGRESS", "En cours"),
                    ("DONE", "Terminée"),
                    ("CANCELLED", "Annulée"),
                ],
                default="PENDING",
                max_length=20,
            ),
        ),
    ]
