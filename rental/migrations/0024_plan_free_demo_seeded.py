# Generated manually – plan Essai gratuit + champ demo_seeded

from django.db import migrations, models


def create_free_plan(apps, schema_editor):
    Plan = apps.get_model("rental", "Plan")
    Plan.objects.get_or_create(
        slug="free",
        defaults={
            "name": "Essai gratuit",
            "description": "Compte gratuit avec exemples pour découvrir SyLoc. Souscrivez à basic ou Premium pour vos données réelles.",
            "price_display": "Gratuit",
            "stripe_price_id": "",
            "stripe_price_id_annual": "",
        },
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("rental", "0023_usersuggestion_theme_autres"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="demo_seeded",
            field=models.BooleanField(
                default=False,
                help_text="Données de démonstration créées pour l'essai gratuit",
            ),
        ),
        migrations.RunPython(create_free_plan, noop_reverse),
    ]
