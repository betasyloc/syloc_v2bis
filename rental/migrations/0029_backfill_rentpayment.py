# Generated manually for historique portail locataire

from decimal import Decimal

from django.db import migrations


def forwards(apps, schema_editor):
    RentPayment = apps.get_model("rental", "RentPayment")
    RentInvoice = apps.get_model("rental", "RentInvoice")
    for inv in RentInvoice.objects.filter(status="PAID").iterator():
        if RentPayment.objects.filter(rent_invoice_id=inv.pk).exists():
            continue
        total = Decimal(inv.amount_rent or 0) + Decimal(inv.amount_charges or 0)
        paid_at = inv.paid_date or inv.due_date
        if paid_at is None:
            continue
        RentPayment.objects.create(
            rent_invoice_id=inv.pk,
            amount=total,
            paid_at=paid_at,
            method="TRANSFER",
        )


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("rental", "0028_tenant_portal_extensions"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
