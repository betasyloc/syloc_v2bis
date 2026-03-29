# Basic annuel : aligner le modèle Plan sur le Price Stripe actuel (SyLoc 2026-03-28)

from django.db import migrations

STRIPE_PRICE_ID_BASE_ANNUAL = "price_1TFsMIIXpfEmKuWCtecfQMtH"


def forwards(apps, schema_editor):
    Plan = apps.get_model("rental", "Plan")
    Plan.objects.filter(slug="base").update(stripe_price_id_annual=STRIPE_PRICE_ID_BASE_ANNUAL)


def backwards(apps, schema_editor):
    Plan = apps.get_model("rental", "Plan")
    Plan.objects.filter(slug="base").update(stripe_price_id_annual="")


class Migration(migrations.Migration):
    dependencies = [
        ("rental", "0024_plan_free_demo_seeded"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
