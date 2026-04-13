"""
Parcours HTTP (smoke) des vues principales avec un bailleur Premium et des données minimales.

Usage :
  python manage.py syloc_smoke_urls
  python manage.py syloc_smoke_urls --iterations 50   # répétition (charge légère / endurance basique)
  python manage.py syloc_smoke_urls --keep-user       # ne pas supprimer l'utilisateur smoke à la fin

Ce n'est pas un test de charge type Locust : pas de concurrence réaliste, mais détecte erreurs 500
et pages qui redirigent vers la connexion après authentification.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.test import Client
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

# Évite DisallowedHost avec le Client Django (hôte par défaut « testserver »).
_SMOKE_EXTRA = {"HTTP_HOST": "localhost"}

from rental.models import (
    BankAccount,
    BankTransaction,
    InspectionReport,
    InspectionTemplate,
    Lease,
    LeaseTemplate,
    LetterTemplate,
    MaintenanceRequest,
    Plan,
    Property,
    PropertyWork,
    RentInvoice,
    StoredDocument,
    Tenant,
    TenantPortalAccess,
    UserProfile,
)

User = get_user_model()

# Vues à ne pas appeler en GET automatique (effets de bord, Stripe, webhooks, staff-only).
SKIP_ROOT_NAMES = frozenset(
    {
        "stripe_webhook",
        "create_checkout_session",
        "create_billing_portal_session",
        "change_subscription_plan",
        "subscription_sandbox_set",
        "sign_document",
        "admin_dashboard",
        "password_reset",
        "password_reset_done",
        "password_reset_confirm",
        "password_reset_complete",
        "username_reminder",
        "username_reminder_done",
        "suggestion_submit",
        "tenant_portal_exit_mode",  # POST / session
    }
)

SKIP_RENTAL_NAMES = frozenset({})


def _ensure_plans() -> None:
    defaults_triples = [
        (Plan.FREE, "Essai gratuit"),
        (Plan.BASE, "Basic"),
        (Plan.PREMIUM, "Premium"),
    ]
    for slug, name in defaults_triples:
        Plan.objects.get_or_create(slug=slug, defaults={"name": name})


def _get_or_build_smoke_context(user: User):
    """Réutilise les données [smoke] existantes pour ce user ou les crée."""
    try:
        prop = Property.objects.get(owner=user, name="[smoke] Bien test")
        tenant = Tenant.objects.get(owner=user, email="smoke.locataire@example.invalid")
        lease = Lease.objects.filter(property=prop).first()
        invoice = RentInvoice.objects.filter(lease=lease).first()
        access = TenantPortalAccess.objects.filter(tenant=tenant).order_by("-created_at").first()
        bank = BankAccount.objects.get(owner=user, name="[smoke] Compte")
        tx = BankTransaction.objects.filter(account=bank).first()
        insp = InspectionReport.objects.filter(lease=lease).first()
        lt = LeaseTemplate.objects.get(owner=user, name="[smoke] Modèle bail")
        itpl = InspectionTemplate.objects.get(owner=user, name="[smoke] Modèle EDL")
        sd = StoredDocument.objects.get(property=prop, title="[smoke] Document")
        letter = LetterTemplate.objects.get(owner=user, name="[smoke] Courrier")
        if not all([lease, invoice, access, tx, insp]):
            raise Property.DoesNotExist
        pwork = PropertyWork.objects.filter(property=prop).first()
        if not pwork:
            pwork = PropertyWork.objects.create(
                property=prop,
                work_type=PropertyWork.TYPE_RAFRAICHISSEMENT,
                title="[smoke] Peinture salon",
                work_date=date.today(),
            )
        return {
            "property": prop,
            "tenant": tenant,
            "lease": lease,
            "invoice": invoice,
            "portal_token": access.token,
            "bank": bank,
            "bank_tx": tx,
            "inspection": insp,
            "lease_template": lt,
            "inspection_template": itpl,
            "stored_doc": sd,
            "letter": letter,
            "property_work": pwork,
        }
    except Exception:
        return _build_smoke_context(user)


def _build_smoke_context(user: User):
    """Crée biens, bail, facture, EDL, banque, documents, portail — liés à user."""
    prop = Property.objects.create(owner=user, name="[smoke] Bien test")
    tenant = Tenant.objects.create(
        owner=user,
        first_name="Smoke",
        last_name="Locataire",
        email="smoke.locataire@example.invalid",
    )
    lease = Lease.objects.create(
        property=prop,
        start_date=date.today() - timedelta(days=60),
        end_date=None,
        rent=Decimal("600.00"),
        charges=Decimal("40.00"),
    )
    lease.tenants.add(tenant)

    MaintenanceRequest.objects.create(
        lease=lease,
        submitted_by=tenant,
        title="[smoke] Fuite",
        description="Test smoke URLs.",
        category=MaintenanceRequest.CAT_OTHER,
    )

    invoice = RentInvoice.objects.create(
        lease=lease,
        due_date=date.today() + timedelta(days=10),
        amount_rent=Decimal("600.00"),
        amount_charges=Decimal("40.00"),
        status="PAID",
        paid_date=date.today(),
    )

    access = TenantPortalAccess.objects.create(
        tenant=tenant,
        expires_at=timezone.now() + timedelta(days=14),
    )

    bank = BankAccount.objects.create(owner=user, name="[smoke] Compte")
    tx = BankTransaction.objects.create(
        account=bank,
        date=date.today(),
        amount=Decimal("640.00"),
        label="Smoke virement",
    )

    insp = InspectionReport.objects.create(
        lease=lease,
        report_type=InspectionReport.ENTRY,
        report_date=date.today(),
        notes="Smoke EDL",
        shared_with_portal=True,
    )
    insp.attached_file.save(
        "smoke-edl.pdf",
        ContentFile(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"),
        save=True,
    )

    lt = LeaseTemplate.objects.create(owner=user, name="[smoke] Modèle bail", content="…")
    itpl = InspectionTemplate.objects.create(owner=user, name="[smoke] Modèle EDL", content="…")
    doc = StoredDocument.objects.create(
        property=prop,
        document_type=StoredDocument.DOC_OTHER,
        title="[smoke] Document",
        shared_with_portal=True,
    )
    doc.file.save("smoke-doc.txt", ContentFile(b"smoke test"), save=True)
    letter = LetterTemplate.objects.create(
        owner=user,
        name="[smoke] Courrier",
        letter_type=LetterTemplate.MISE_EN_DEMEURE,
        content="Bonjour {{ locataire }}",
    )

    pwork = PropertyWork.objects.create(
        property=prop,
        work_type=PropertyWork.TYPE_RAFRAICHISSEMENT,
        title="[smoke] Peinture salon",
        work_date=date.today(),
    )

    return {
        "property": prop,
        "tenant": tenant,
        "lease": lease,
        "invoice": invoice,
        "portal_token": access.token,
        "bank": bank,
        "bank_tx": tx,
        "inspection": insp,
        "lease_template": lt,
        "inspection_template": itpl,
        "stored_doc": doc,
        "letter": letter,
        "property_work": pwork,
    }


def _url_jobs(ctx: dict) -> list[tuple[str, str]]:
    """Liste (label, path) pour GET."""
    p, t, l, inv = ctx["property"], ctx["tenant"], ctx["lease"], ctx["invoice"]
    tok = ctx["portal_token"]
    bank, tx = ctx["bank"], ctx["bank_tx"]
    insp = ctx["inspection"]
    lt, itpl = ctx["lease_template"], ctx["inspection_template"]
    sd, letter = ctx["stored_doc"], ctx["letter"]
    pw = ctx["property_work"]

    jobs: list[tuple[str, str]] = []

    def add_root(name: str, *args, **kwargs):
        if name in SKIP_ROOT_NAMES:
            return
        try:
            jobs.append((name, reverse(name, args=args, kwargs=kwargs)))
        except NoReverseMatch:
            pass

    def add_rental(name: str, *args, **kwargs):
        if name in SKIP_RENTAL_NAMES:
            return
        try:
            jobs.append((f"rental:{name}", reverse(f"rental:{name}", args=args, kwargs=kwargs)))
        except NoReverseMatch:
            pass

    # --- Racine (sans namespace) ---
    for n in (
        "home",
        "login",
        "register",
        "dashboard",
        "notifications",
        "suggestions",
        "account_profile",
        "premium",
        "subscription",
        "public_offers",
        "checkout_success",
        "checkout_cancel",
    ):
        add_root(n)

    add_root("plan_choice", plan_slug=Plan.BASE)

    # Portail locataire (session anonyme). Les actions POST (demande, relevé, message) sont exclues.
    add_root("tenant_portal", token=tok)
    add_root("tenant_portal_document_download", token=tok, pk=sd.pk)
    add_root("tenant_portal_inspection_download", token=tok, pk=insp.pk)
    add_root("tenant_portal_lease_pdf", token=tok, lease_pk=l.pk)
    add_root("tenant_portal_quittance_pdf", token=tok, invoice_pk=inv.pk)

    # --- rental: ---
    add_rental("property_list")
    add_rental("property_create")
    add_rental("property_edit", pk=p.pk)
    add_rental("property_works_list", property_pk=p.pk)
    add_rental("property_work_create", property_pk=p.pk)
    add_rental("property_work_edit", property_pk=p.pk, pk=pw.pk)
    add_rental("property_work_delete", property_pk=p.pk, pk=pw.pk)
    add_rental("tenant_list")
    add_rental("tenant_create")
    add_rental("tenant_edit", pk=t.pk)
    add_rental("lease_list")
    add_rental("lease_create")
    add_rental("lease_detail", pk=l.pk)
    add_rental("lease_edit", pk=l.pk)
    add_rental("maintenance_requests_list")

    add_rental("inspection_list")
    add_rental("inspection_create")
    add_rental("inspection_detail", pk=insp.pk)
    add_rental("inspection_edit", pk=insp.pk)

    add_rental("team_page")
    add_rental("bank_account_list")
    add_rental("bank_account_create")
    add_rental("bank_transaction_list", account_pk=bank.pk)
    add_rental("bank_import_csv", account_pk=bank.pk)
    add_rental("bank_match_transaction", transaction_pk=tx.pk)
    add_rental("rentability_ai")
    add_rental("api_access")
    add_rental("mobile_app")
    add_rental("analytics_report")
    add_rental("report_pdf")
    add_rental("cashflow_forecast")
    add_rental("reminder_rules")
    add_rental("renewal_reminders")
    add_rental("letter_templates")
    add_rental("letter_template_create")
    add_rental("letter_template_edit", pk=letter.pk)
    add_rental("diagnostics_list")
    add_rental("stored_documents")
    add_rental("stored_document_create")
    add_rental("stored_document_edit", pk=sd.pk)
    add_rental("property_dashboard")
    add_rental("portal_tenant_list")
    add_rental("webhooks_list")
    add_rental("calendar_ics")
    add_rental("activity_log")
    add_rental("data_export")
    add_rental("rentinvoice_list")
    add_rental("export_accounting_csv")
    add_rental("rent_revise", lease_pk=l.pk)
    add_rental("rentinvoice_quittance_pdf", pk=inv.pk)
    add_rental("rentinvoice_quittance_tenant_pdf", pk=inv.pk, tenant_pk=t.pk)
    add_rental("lease_template_list")
    add_rental("lease_template_create")
    add_rental("lease_template_edit", pk=lt.pk)
    add_rental("inspection_template_list")
    add_rental("inspection_template_create")
    add_rental("inspection_template_edit", pk=itpl.pk)

    return jobs


class Command(BaseCommand):
    help = "GET automatisés sur les URLs principales (bailleur Premium + portail token) pour détecter erreurs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--iterations",
            type=int,
            default=1,
            help="Nombre de passages complets sur toutes les URLs (défaut : 1).",
        )
        parser.add_argument(
            "--keep-user",
            action="store_true",
            help="Conserver l'utilisateur et les données créés (email smoke@syloc.local).",
        )

    def handle(self, *args, **options):
        iterations = max(1, options["iterations"])
        keep_user = options["keep_user"]

        _ensure_plans()
        premium_plan = Plan.objects.get(slug=Plan.PREMIUM)

        email = "smoke@syloc.local"
        if not keep_user:
            User.objects.filter(email__iexact=email).delete()

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            user = User.objects.create_user(
                username=email,
                email=email,
                password="SmokeTestSmoke2026!",
            )
        UserProfile.objects.update_or_create(
            user=user,
            defaults={"plan": premium_plan},
        )

        ctx = _get_or_build_smoke_context(user)
        jobs = _url_jobs(ctx)

        landlord_client = Client()
        landlord_client.force_login(user)

        portal_client = Client()

        failures: list[str] = []
        total = 0

        for it in range(iterations):
            if iterations > 1:
                self.stdout.write(f"--- Iteration {it + 1}/{iterations} ---")

            for name, path in jobs:
                total += 1
                is_portal = path.startswith("/portal/") and "/quitter-mode/" not in path
                client = portal_client if is_portal else landlord_client

                try:
                    response = client.get(path, follow=True, **_SMOKE_EXTRA)
                except Exception as e:
                    failures.append(f"{name} {path} -> exception: {e}")
                    self.stdout.write(self.style.ERROR(f"FAIL {name}: {e}"))
                    continue

                if response.status_code >= 400:
                    failures.append(f"{name} {path} -> HTTP {response.status_code}")
                    self.stdout.write(
                        self.style.ERROR(f"FAIL {name}: HTTP {response.status_code}")
                    )
                elif response.redirect_chain and any(
                    "/login" in (u or "") for u, _ in response.redirect_chain
                ):
                    failures.append(f"{name} {path} -> redirect login")
                    self.stdout.write(self.style.ERROR(f"FAIL {name}: redirect login"))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Requetes executees : {total}"))
        if failures:
            self.stdout.write(self.style.ERROR(f"Echecs : {len(failures)}"))
            for line in failures[:30]:
                self.stdout.write(self.style.WARNING(line))
            if len(failures) > 30:
                self.stdout.write(self.style.WARNING(f"... et {len(failures) - 30} autres"))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "OK : aucun echec (pas de HTTP >= 400 ni renvoi vers /login)."
                )
            )

        if not keep_user:
            uid = user.pk
            user.delete()
            self.stdout.write(
                self.style.WARNING(
                    f"Utilisateur smoke et donnees associees supprimes (id={uid})."
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"Donnees conservees : compte {email} (mot de passe defini a la creation)."
                )
            )

        if failures:
            raise CommandError(f"{len(failures)} URL(s) en echec (details ci-dessus).")
