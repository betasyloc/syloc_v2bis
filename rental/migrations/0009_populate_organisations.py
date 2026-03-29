# Data migration: create one Organisation per user and assign their objects to it

from django.db import migrations


def create_organisations(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Organisation = apps.get_model("rental", "Organisation")
    OrganisationMember = apps.get_model("rental", "OrganisationMember")
    Property = apps.get_model("rental", "Property")
    Tenant = apps.get_model("rental", "Tenant")
    LeaseTemplate = apps.get_model("rental", "LeaseTemplate")
    InspectionTemplate = apps.get_model("rental", "InspectionTemplate")

    for user in User.objects.all():
        has_data = (
            Property.objects.filter(owner=user).exists()
            or Tenant.objects.filter(owner=user).exists()
            or LeaseTemplate.objects.filter(owner=user).exists()
            or InspectionTemplate.objects.filter(owner=user).exists()
        )
        if not has_data:
            continue
        name = (getattr(user, "username", "") or getattr(user, "email", "") or "Mon organisation")[:150]
        org = Organisation.objects.create(name=name[:150])
        OrganisationMember.objects.create(user=user, organisation=org, role="owner")
        Property.objects.filter(owner=user).update(organisation=org)
        Tenant.objects.filter(owner=user).update(organisation=org)
        LeaseTemplate.objects.filter(owner=user).update(organisation=org)
        InspectionTemplate.objects.filter(owner=user).update(organisation=org)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("rental", "0008_organisation_roles"),
    ]

    operations = [
        migrations.RunPython(create_organisations, noop),
    ]
