# Generated migration - initial models

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Property",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(help_text="Nom court du bien (ex : Studio Lyon 3)", max_length=255)),
                ("address", models.CharField(blank=True, max_length=255)),
                ("city", models.CharField(blank=True, max_length=100)),
                ("zip_code", models.CharField(blank=True, max_length=10)),
                ("owner_type", models.CharField(choices=[("PERSONNE_PHYSIQUE", "Personne physique"), ("LMNP", "LMNP"), ("SCI", "SCI")], default="PERSONNE_PHYSIQUE", max_length=32)),
                ("purchase_price", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("notary_fees", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("monthly_mortgage", models.DecimalField(blank=True, decimal_places=2, help_text="Mensualité de crédit", max_digits=10, null=True)),
                ("monthly_charges", models.DecimalField(blank=True, decimal_places=2, help_text="Charges mensuelles (copro, assurances, etc.)", max_digits=10, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="properties", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="Tenant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("first_name", models.CharField(max_length=100)),
                ("last_name", models.CharField(max_length=100)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("phone", models.CharField(blank=True, max_length=30)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tenants", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["last_name", "first_name"]},
        ),
        migrations.CreateModel(
            name="Lease",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("start_date", models.DateField()),
                ("end_date", models.DateField(blank=True, null=True)),
                ("rent", models.DecimalField(decimal_places=2, max_digits=10)),
                ("charges", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("deposit", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("property", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="leases", to="rental.property")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="leases", to="rental.tenant")),
            ],
            options={"ordering": ["-start_date"]},
        ),
        migrations.CreateModel(
            name="RentInvoice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("due_date", models.DateField()),
                ("amount_rent", models.DecimalField(decimal_places=2, max_digits=10)),
                ("amount_charges", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("status", models.CharField(choices=[("DUE", "À payer"), ("PAID", "Payé"), ("LATE", "En retard")], default="DUE", max_length=10)),
                ("paid_date", models.DateField(blank=True, null=True)),
                ("reminder_sent_at", models.DateTimeField(blank=True, help_text="Date d'envoi du rappel avant échéance", null=True)),
                ("last_late_reminder_sent_at", models.DateTimeField(blank=True, help_text="Dernière relance impayé envoyée", null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("lease", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="rent_invoices", to="rental.lease")),
            ],
            options={"ordering": ["-due_date"], "unique_together": {("lease", "due_date")}},
        ),
    ]
