# Premium annuel : aligner le modèle Plan sur le Price Stripe actuel (SyLoc 2026-03-28)

from django.db import migrations

STRIPE_PRICE_ID_PREMIUM_ANNUAL = "price_1TFsLnIXpfEmKuWC8u98nskG"


def forwards(apps, schema_editor):
    Plan = apps.get_model("rental", "Plan")
    Plan.objects.filter(slug="premium").update(stripe_price_id_annual=STRIPE_PRICE_ID_PREMIUM_ANNUAL)


def backwards(apps, schema_editor):
    Plan = apps.get_model("rental", "Plan")
    Plan.objects.filter(slug="premium").update(stripe_price_id_annual="")


class Migration(migrations.Migration):
    dependencies = [
        ("rental", "0025_plan_base_annual_stripe_price"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
