# Generated manually for PropertyWork

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("rental", "0035_userprofile_volume_segment_key"),
    ]

    operations = [
        migrations.CreateModel(
            name="PropertyWork",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("work_type", models.CharField(choices=[
                    ("RAFRAICHISSEMENT", "Travaux de rafraîchissement"),
                    ("AMELIORATION", "Travaux d'amélioration"),
                    ("RENOVATION_LOURDE", "Travaux de rénovation lourde"),
                    ("ENERGIE", "Travaux d'optimisation énergétique"),
                    ("AGRANDISSEMENT", "Travaux d'agrandissement"),
                    ("STRATEGIQUE", "Travaux stratégiques (investissement)"),
                ], max_length=30)),
                ("title", models.CharField(help_text="Résumé court (ex. peinture salon, réfection salle de bain)", max_length=255)),
                ("description", models.TextField(blank=True, help_text="Détails : prestataires, matériaux, périmètre, etc.")),
                ("work_date", models.DateField(help_text="Date de fin des travaux ou de la réalisation")),
                ("amount", models.DecimalField(blank=True, decimal_places=2, help_text="Coût total TTC (optionnel)", max_digits=12, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("property", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="works", to="rental.property")),
            ],
            options={
                "verbose_name": "Travail réalisé",
                "verbose_name_plural": "Travaux réalisés",
                "ordering": ["-work_date", "-pk"],
            },
        ),
    ]
