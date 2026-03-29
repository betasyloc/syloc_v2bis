"""Tests pour les vues critiques et les formulaires."""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import LeaseForm
from .models import (
    Lease,
    MaintenanceRequest,
    Property,
    PropertyTag,
    Tenant,
    ensure_standard_property_tags,
)

User = get_user_model()


class LoginRequiredViewsTest(TestCase):
    """Vérifie que les vues principales exigent une connexion."""

    def setUp(self):
        self.client = Client()

    def test_dashboard_redirects_anonymous_to_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_property_list_redirects_anonymous_to_login(self):
        response = self.client.get(reverse("rental:property_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_lease_list_redirects_anonymous_to_login(self):
        response = self.client.get(reverse("rental:lease_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_rentinvoice_list_redirects_anonymous_to_login(self):
        response = self.client.get(reverse("rental:rentinvoice_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_dashboard_accessible_when_authenticated(self):
        user = User.objects.create_user(username="test@example.com", email="test@example.com", password="testpass123")
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)


class LeaseFormValidationTest(TestCase):
    """Validation des dates du formulaire de bail."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="owner@example.com",
            email="owner@example.com",
            password="testpass123",
        )
        self.property = Property.objects.create(
            owner=self.user,
            name="Studio test",
            address="1 rue Test",
        )
        self.tenant = Tenant.objects.create(
            owner=self.user,
            first_name="Jean",
            last_name="Dupont",
            email="jean@example.com",
        )

    def test_end_date_before_start_date_invalid(self):
        """La date de fin doit être postérieure à la date de début."""
        form = LeaseForm(
            data={
                "property": self.property.pk,
                "tenants": [self.tenant.pk],
                "start_date": date(2025, 6, 1),
                "end_date": date(2025, 5, 1),
                "rent": 600,
                "charges": 50,
                "deposit": 1200,
                "lease_document": "",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("__all__", form.errors)
        self.assertIn("postérieure à la date de début", str(form.errors["__all__"]))

    def test_valid_dates_accepted(self):
        """Des dates cohérentes (début < fin) sont acceptées."""
        form = LeaseForm(
            data={
                "property": self.property.pk,
                "tenants": [self.tenant.pk],
                "start_date": date(2025, 5, 1),
                "end_date": date(2026, 4, 30),
                "rent": 600,
                "charges": 50,
                "deposit": 1200,
                "lease_document": "",
            }
        )
        self.assertTrue(form.is_valid(), msg=form.errors)

    def test_end_date_optional_no_error(self):
        """Sans date de fin, pas d'erreur de cohérence."""
        form = LeaseForm(
            data={
                "property": self.property.pk,
                "tenants": [self.tenant.pk],
                "start_date": date(2025, 5, 1),
                "end_date": "",
                "rent": 600,
                "charges": 50,
                "deposit": 1200,
                "lease_document": "",
            }
        )
        self.assertTrue(form.is_valid(), msg=form.errors)


class FilterByOwnerTest(TestCase):
    """Les listes ne doivent retourner que les données du propriétaire connecté."""

    def setUp(self):
        self.user_a = User.objects.create_user(
            username="a@example.com", email="a@example.com", password="pass123"
        )
        self.user_b = User.objects.create_user(
            username="b@example.com", email="b@example.com", password="pass123"
        )
        self.prop_a = Property.objects.create(owner=self.user_a, name="Bien A")
        self.prop_b = Property.objects.create(owner=self.user_b, name="Bien B")

    def test_property_list_shows_only_own_properties(self):
        self.client = Client()
        self.client.force_login(self.user_a)
        response = self.client.get(reverse("rental:property_list"))
        self.assertEqual(response.status_code, 200)
        self.assertQuerySetEqual(
            response.context["properties"],
            [self.prop_a],
            transform=lambda x: x,
        )

    def test_property_list_filter_by_tag_or_logic(self):
        """Filtre tags : biens ayant au moins un des tags sélectionnés."""
        ensure_standard_property_tags()
        tag_meuble = PropertyTag.objects.get(name="Meublé")
        tag_nu = PropertyTag.objects.get(name="Nu")
        self.prop_a.tags.add(tag_meuble)
        prop_c = Property.objects.create(owner=self.user_a, name="Bien C")
        prop_c.tags.add(tag_nu)
        self.client.force_login(self.user_a)
        response = self.client.get(
            reverse("rental:property_list"),
            {"tag": [str(tag_meuble.pk), str(tag_nu.pk)]},
        )
        self.assertEqual(response.status_code, 200)
        names = sorted(p.name for p in response.context["properties"])
        self.assertEqual(names, ["Bien A", "Bien C"])

        response_one = self.client.get(
            reverse("rental:property_list"), {"tag": str(tag_meuble.pk)}
        )
        self.assertEqual(
            [p.pk for p in response_one.context["properties"]], [self.prop_a.pk]
        )


class MaintenanceRequestStalePropertyTest(TestCase):
    """Alerte 48 h sans réponse bailleur sur les demandes d'intervention."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="own@example.com", email="own@example.com", password="pass123"
        )
        self.prop = Property.objects.create(owner=self.user, name="Bien", address="1 rue X")
        self.tenant = Tenant.objects.create(
            owner=self.user, first_name="Lou", last_name="Loc", email="loc@example.com"
        )
        self.lease = Lease.objects.create(
            property=self.prop,
            start_date=date(2025, 1, 1),
            rent=500,
            charges=0,
        )
        self.lease.tenants.add(self.tenant)

    def test_stale_without_landlord_reply_after_48h(self):
        mr = MaintenanceRequest.objects.create(
            lease=self.lease,
            submitted_by=self.tenant,
            title="Problème",
            description="Détails",
        )
        MaintenanceRequest.objects.filter(pk=mr.pk).update(
            created_at=timezone.now() - timedelta(hours=49)
        )
        mr.refresh_from_db()
        self.assertTrue(mr.stale_without_landlord_reply)

    def test_not_stale_when_landlord_replied(self):
        mr = MaintenanceRequest.objects.create(
            lease=self.lease,
            submitted_by=self.tenant,
            title="Problème",
            description="Détails",
            landlord_reply="Bonjour, nous intervenons lundi.",
        )
        MaintenanceRequest.objects.filter(pk=mr.pk).update(
            created_at=timezone.now() - timedelta(hours=72)
        )
        mr.refresh_from_db()
        self.assertFalse(mr.stale_without_landlord_reply)

    def test_not_stale_when_status_done(self):
        mr = MaintenanceRequest.objects.create(
            lease=self.lease,
            submitted_by=self.tenant,
            title="Problème",
            description="Détails",
            status=MaintenanceRequest.STATUS_DONE,
        )
        MaintenanceRequest.objects.filter(pk=mr.pk).update(
            created_at=timezone.now() - timedelta(hours=72)
        )
        mr.refresh_from_db()
        self.assertFalse(mr.stale_without_landlord_reply)

    def test_not_stale_when_acknowledged_only(self):
        mr = MaintenanceRequest.objects.create(
            lease=self.lease,
            submitted_by=self.tenant,
            title="Problème",
            description="Détails",
        )
        MaintenanceRequest.objects.filter(pk=mr.pk).update(
            created_at=timezone.now() - timedelta(hours=72),
            acknowledged_at=timezone.now() - timedelta(hours=70),
        )
        mr.refresh_from_db()
        self.assertFalse(mr.stale_without_landlord_reply)
