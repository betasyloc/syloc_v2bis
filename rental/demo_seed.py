"""Données de démonstration pour les comptes en essai gratuit (SyLoc)."""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from django.db import transaction

from .models import (
    InspectionReport,
    Lease,
    Plan,
    Property,
    RentInvoice,
    Tenant,
    UserProfile,
    get_or_create_default_organisation,
)


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def seed_demo_data_if_needed(user, profile: UserProfile) -> None:
    """Crée biens, locataires, baux, loyers et un EDL fictifs (une seule fois par profil)."""
    if profile.demo_seeded or profile.plan.slug != Plan.FREE:
        return
    with transaction.atomic():
        locked = UserProfile.objects.select_for_update().get(pk=profile.pk)
        if locked.demo_seeded:
            return
        org = get_or_create_default_organisation(user)
        org.name = "Exemples SyLoc (démonstration)"
        org.save(update_fields=["name"])

        today = date.today()
        lease_start = _add_months(today.replace(day=1), -10)

        p1 = Property.objects.create(
            owner=user,
            organisation=org,
            name="Studio démo – Lyon 3ᵉ",
            address="12 cours Lafayette",
            city="Lyon",
            zip_code="69003",
            owner_type="LMNP",
            purchase_price=Decimal("95000.00"),
            monthly_mortgage=Decimal("280.00"),
            monthly_charges=Decimal("85.00"),
            dpe_class="C",
        )
        p2 = Property.objects.create(
            owner=user,
            organisation=org,
            name="T3 démo – Marseille",
            address="8 rue de la République",
            city="Marseille",
            zip_code="13001",
            owner_type="PERSONNE_PHYSIQUE",
            purchase_price=Decimal("198000.00"),
            monthly_mortgage=Decimal("620.00"),
            monthly_charges=Decimal("120.00"),
            dpe_class="D",
        )

        t1 = Tenant.objects.create(
            owner=user,
            organisation=org,
            first_name="Marie",
            last_name="Dupont (exemple)",
            email="marie.dupont.exemple@syloc.local",
            phone="06 12 34 56 78",
        )
        t2 = Tenant.objects.create(
            owner=user,
            organisation=org,
            first_name="Jean",
            last_name="Martin (exemple)",
            email="jean.martin.exemple@syloc.local",
            phone="06 98 76 54 32",
        )

        lease1 = Lease.objects.create(
            property=p1,
            start_date=lease_start,
            end_date=None,
            rent=Decimal("650.00"),
            charges=Decimal("45.00"),
            deposit=Decimal("1300.00"),
            lease_document=(
                "Bail de location vide (exemple SyLoc).\n\n"
                "Les présentes données sont fictives pour vous permettre de parcourir l'application."
            ),
            is_active=True,
        )
        lease1.tenants.add(t1)

        lease2 = Lease.objects.create(
            property=p2,
            start_date=_add_months(lease_start, 2),
            end_date=None,
            rent=Decimal("920.00"),
            charges=Decimal("70.00"),
            deposit=Decimal("1840.00"),
            lease_document="Bail meublé — texte d'exemple pour la démonstration SyLoc.",
            is_active=True,
        )
        lease2.tenants.add(t2)

        # Loyers sur plusieurs mois (échéances le 5 de chaque mois)
        def seed_invoices(lease: Lease, rent: Decimal, charges: Decimal, months_back: int):
            for i in range(months_back, -1, -1):
                due = _add_months(today.replace(day=5), -i)
                if i == 0:
                    status, paid = "DUE", None
                elif i == 1:
                    status, paid = "LATE", None
                else:
                    status, paid = "PAID", _add_months(due, 0)
                RentInvoice.objects.get_or_create(
                    lease=lease,
                    due_date=due,
                    defaults={
                        "amount_rent": rent,
                        "amount_charges": charges,
                        "status": status,
                        "paid_date": paid,
                    },
                )

        seed_invoices(lease1, Decimal("650.00"), Decimal("45.00"), 5)
        seed_invoices(lease2, Decimal("920.00"), Decimal("70.00"), 3)

        InspectionReport.objects.create(
            lease=lease1,
            report_type=InspectionReport.ENTRY,
            report_date=lease_start,
            notes=(
                "État des lieux d'entrée (exemple) : murs et sols en bon état général, "
                "équipements de cuisine fonctionnels. Inventaire joint au dossier fictif."
            ),
        )

        locked.demo_seeded = True
        locked.save(update_fields=["demo_seeded"])
