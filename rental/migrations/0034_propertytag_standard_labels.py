# Libellés alignés sur rental.models.STANDARD_PROPERTY_TAG_NAMES

from django.db import migrations

_STANDARD_TAG_NAMES = (
    "Meublé",
    "Nu",
    "Coloc",
    "Copro",
    "Parking",
    "Travaux",
    "Vacant",
    "Saison",
    "Mandat",
    "Etudiants",
)


def create_standard_property_tags(apps, schema_editor):
    PropertyTag = apps.get_model("rental", "PropertyTag")
    for name in _STANDARD_TAG_NAMES:
        PropertyTag.objects.get_or_create(name=name)


class Migration(migrations.Migration):
    dependencies = [
        ("rental", "0033_maintenance_thread_message"),
    ]

    operations = [
        migrations.RunPython(create_standard_property_tags, migrations.RunPython.noop),
    ]
