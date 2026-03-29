import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("rental", "0017_property_dpe"),
    ]

    operations = [
        migrations.AddField(
            model_name="lease",
            name="attached_file",
            field=models.FileField(
                blank=True,
                help_text="Optionnel : joindre votre bail au format PDF (au lieu ou en complément du texte ci-dessus).",
                upload_to="leases/attachments/",
                validators=[django.core.validators.FileExtensionValidator(["pdf"])],
            ),
        ),
        migrations.AddField(
            model_name="inspectionreport",
            name="attached_file",
            field=models.FileField(
                blank=True,
                help_text="Optionnel : joindre un état des lieux au format PDF (au lieu ou en complément des observations).",
                upload_to="inspections/attachments/",
                validators=[django.core.validators.FileExtensionValidator(["pdf"])],
            ),
        ),
    ]
