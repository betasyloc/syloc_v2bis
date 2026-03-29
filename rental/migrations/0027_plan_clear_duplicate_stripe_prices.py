# Si mensuel et annuel pointent vers le même price_…, vider l’annuel (à ressaisir depuis Stripe).

from django.db import migrations


def forwards(apps, schema_editor):
    Plan = apps.get_model("rental", "Plan")
    for p in Plan.objects.filter(slug__in=("base", "premium")):
        m = (p.stripe_price_id or "").strip()
        a = (p.stripe_price_id_annual or "").strip()
        if m and a and m == a:
            Plan.objects.filter(pk=p.pk).update(stripe_price_id_annual="")


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("rental", "0026_plan_premium_annual_stripe_price"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
