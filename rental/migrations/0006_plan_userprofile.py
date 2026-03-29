# Generated manually for Premium / Base plans

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_plans_and_profiles(apps, schema_editor):
    Plan = apps.get_model("rental", "Plan")
    UserProfile = apps.get_model("rental", "UserProfile")
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Plan.objects.get_or_create(slug="base", defaults={"name": "Base", "price_display": "Sur devis"})
    Plan.objects.get_or_create(slug="premium", defaults={"name": "Premium", "price_display": "Sur devis"})
    base_plan = Plan.objects.get(slug="base")
    for user in User.objects.all():
        UserProfile.objects.get_or_create(user=user, defaults={"plan_id": base_plan.pk})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("rental", "0005_add_lease_and_inspection_templates"),
    ]

    operations = [
        migrations.CreateModel(
            name="Plan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=50)),
                ("slug", models.SlugField(unique=True, max_length=20)),
                ("description", models.TextField(blank=True)),
                ("price_display", models.CharField(blank=True, max_length=50, help_text="Ex. 9 €/mois")),
            ],
            options={
                "verbose_name": "Offre",
                "verbose_name_plural": "Offres",
                "ordering": ["id"],
            },
        ),
        migrations.CreateModel(
            name="UserProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("plan", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="user_profiles", to="rental.plan")),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="profile", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Profil utilisateur",
                "verbose_name_plural": "Profils utilisateur",
            },
        ),
        migrations.RunPython(create_plans_and_profiles, noop),
    ]
