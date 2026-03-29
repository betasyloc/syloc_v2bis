# Generated manually for Stripe

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("rental", "0006_plan_userprofile"),
    ]

    operations = [
        migrations.AddField(
            model_name="plan",
            name="stripe_price_id",
            field=models.CharField(blank=True, max_length=100, help_text="ID du prix Stripe (price_xxx)"),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="stripe_customer_id",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="stripe_subscription_id",
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
