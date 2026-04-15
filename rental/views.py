from __future__ import annotations

import csv
import logging
import os
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib.sessions.models import Session
from django.utils import timezone as tz_module
from django.core.mail import send_mail
from django.core.mail import get_connection
from django.db import transaction
from django.db.models import Count, Exists, Max, Min, OuterRef, Prefetch, Sum, Q, F
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils.http import content_disposition_header
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone as tz
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import (
    PropertyForm,
    PropertyWorkForm,
    TenantForm,
    LeaseForm,
    InspectionReportForm,
    RentRevisionForm,
    UsernameReminderForm,
    AccountProfileForm,
    AccountDeleteForm,
    UserRegistrationForm,
    UserSuggestionForm,
    LeaseTemplateForm,
    InspectionTemplateForm,
    InviteMemberForm,
    OrganisationNameForm,
    TeamMemberEmailForm,
    DocumentSignatureForm,
    BankAccountForm,
    BankStatementUploadForm,
)
from .models import (
    Property,
    PropertyTag,
    PropertyWork,
    Tenant,
    Lease,
    RentInvoice,
    RentPayment,
    InspectionReport,
    LeaseTemplate,
    InspectionTemplate,
    Plan,
    UserProfile,
    Organisation,
    OrganisationMember,
    ApiKey,
    Announcement,
    SigningInvitation,
    DocumentSignature,
    UserSuggestion,
    UserSuggestionAttachment,
    UserActivityLog,
    BankAccount,
    BankTransaction,
    compute_global_cashflow_for_user,
    get_or_create_default_organisation,
    properties_visible_to,
    tenants_visible_to,
    leases_visible_to,
    lease_templates_visible_to,
    inspection_templates_visible_to,
    bank_accounts_visible_to,
    LeasePracticalInfo,
    LeaseAnnouncement,
    MaintenanceRequest,
    MaintenanceThreadMessage,
    PropertyDiagnostic,
    maintenance_requests_pending_landlord_action_qs,
    STANDARD_PROPERTY_TAG_NAMES,
)
from .plan_segments import (
    SUBSCRIPTION_VOLUME_TIER_ROWS,
    active_property_count_for_user,
    max_active_properties_for_volume_segment_key,
    message_active_property_quota_reached,
    property_volume_segment_for_count,
    volume_segment_label_for_key,
)
from django.contrib.contenttypes.models import ContentType

from .emails import send_user_suggestion_notifications
from .intervention_feed import intervention_exchange_timeline_for_landlord
from .pdf import (
    build_quittance_pdf,
    build_lease_pdf,
    build_inspection_pdf,
    quittance_attachment_filename,
    lease_attachment_filename,
    inspection_attachment_filename,
)
from .emails import send_quittance_email, send_quittance_per_tenant, send_signing_invitation_email
from .template_defaults import get_default_lease_content, get_default_inspection_content
from .ai_rentability import get_property_metrics, get_rentability_analysis
from .user_activity import log_user_activity

User = get_user_model()
logger = logging.getLogger(__name__)


def _portal_lease_content_redirect(request: HttpRequest, lease_pk: int) -> HttpResponseRedirect:
    """Après ajout / suppression d’info pratique ou d’annonce portail (POST)."""
    if request.POST.get("return_to") == "maintenance_list":
        return redirect("rental:maintenance_requests_list")
    return redirect("rental:lease_detail", pk=lease_pk)


def ratelimit_blocked(request: HttpRequest, exception=None) -> HttpResponse:
    """Vue appelée par le middleware quand la limite de tentatives (ex. connexion) est dépassée."""
    return render(request, "auth/ratelimit_blocked.html", status=429)


def username_reminder(request: HttpRequest) -> HttpResponse:
    """Identifiant oublié : envoie l'identifiant par email."""
    if request.method == "POST":
        form = UsernameReminderForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"].strip().lower()
            users = User.objects.filter(email__iexact=email)
            if users.exists():
                usernames = [u.username for u in users]
                body = (
                    "Vous avez demandé à recevoir votre identifiant SyLoc.\n\n"
                    "Identifiant(s) associé(s) à cette adresse email :\n"
                    + "\n".join(f"  - {u}" for u in usernames)
                    + "\n\n"
                    "Vous pouvez vous connecter avec cet identifiant ou avec votre email.\n\n"
                    "L'équipe SyLoc"
                )
                send_mail(
                    subject="SyLoc – Votre identifiant",
                    message=body,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[email],
                    fail_silently=True,
                )
            return redirect("username_reminder_done")
    else:
        form = UsernameReminderForm()
    return render(request, "auth/username_reminder.html", {"form": form})


def username_reminder_done(request: HttpRequest) -> HttpResponse:
    """Page de confirmation après envoi de l'identifiant par email."""
    return render(request, "auth/username_reminder_done.html")


@staff_member_required
def admin_dashboard(request: HttpRequest) -> HttpResponse:
    """Tableau de bord administrateur : comptes, statistiques et problèmes de fonctionnement."""
    today = date.today()

    # Comptes utilisateurs
    total_users = User.objects.count()
    thirty_days_ago = tz_module.now() - tz_module.timedelta(days=30)
    new_users_30d = User.objects.filter(date_joined__gte=thirty_days_ago).count()

    # Statistiques globales
    total_properties = Property.objects.count()
    total_leases = Lease.objects.count()
    total_invoices = RentInvoice.objects.count()
    late_invoices_count = RentInvoice.objects.filter(
        status__in=["DUE", "LATE"],
        due_date__lt=today,
    ).count()

    # Problèmes / alertes
    problems = []

    # Utilisateurs sans aucun bien (compte inactif ou nouveau)
    users_without_properties = User.objects.annotate(
        nprops=Count("properties"),
    ).filter(nprops=0).exclude(is_superuser=True).count()
    if users_without_properties > 0:
        problems.append({
            "type": "warning",
            "title": "Comptes sans bien",
            "description": f"{users_without_properties} utilisateur(s) n'ont aucun bien enregistré.",
        })

    # Loyers en retard
    if late_invoices_count > 0:
        problems.append({
            "type": "danger",
            "title": "Loyers en retard",
            "description": f"{late_invoices_count} loyer(s) en retard sur l'ensemble de la plateforme.",
        })

    # Baux sans locataire (tenants vide)
    leases_no_tenant = Lease.objects.filter(tenants__isnull=True).count()
    if leases_no_tenant > 0:
        problems.append({
            "type": "warning",
            "title": "Baux sans locataire",
            "description": f"{leases_no_tenant} bail(aux) sans locataire associé.",
        })

    # Baux se terminant dans les 30 prochains jours
    end_soon = today + timedelta(days=30)
    leases_ending_soon = Lease.objects.filter(
        end_date__isnull=False,
        end_date__gte=today,
        end_date__lte=end_soon,
        is_active=True,
    ).count()
    if leases_ending_soon > 0:
        problems.append({
            "type": "info",
            "title": "Baux bientôt terminés",
            "description": f"{leases_ending_soon} bail(aux) se terminent dans les 30 prochains jours.",
        })

    # Biens sans bail actif
    active_lease = Lease.objects.filter(property=OuterRef("pk"), is_active=True)
    properties_no_lease = properties_visible_to(request.user).filter(
        archived_at__isnull=True,
    ).exclude(Exists(active_lease)).count()
    if properties_no_lease > 0:
        problems.append({
            "type": "info",
            "title": "Biens sans bail actif",
            "description": f"{properties_no_lease} bien(s) sans bail actif (vacants ou à louer).",
        })

    recent_suggestions = (
        UserSuggestion.objects.select_related("user")
        .prefetch_related("attachments")
        .order_by("-created_at")[:12]
    )
    total_suggestions = UserSuggestion.objects.count()

    context = {
        "total_users": total_users,
        "new_users_30d": new_users_30d,
        "total_properties": total_properties,
        "total_leases": total_leases,
        "total_invoices": total_invoices,
        "late_invoices_count": late_invoices_count,
        "problems": problems,
        "users_without_properties": users_without_properties,
        "total_suggestions": total_suggestions,
        "recent_suggestions": recent_suggestions,
    }
    return render(request, "admin_dashboard/dashboard.html", context)


def register(request: HttpRequest) -> HttpResponse:
    """Création de compte gratuit : essai avec données de démonstration ; abonnement ultérieur au besoin."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"].strip().lower()
            password = form.cleaned_data["password1"]
            first_name = form.cleaned_data["first_name"].strip()
            last_name = form.cleaned_data["last_name"].strip()
            try:
                plan = Plan.objects.get(slug=Plan.FREE)
            except Plan.DoesNotExist:
                plan = Plan.get_base()
            username = email[:150] if len(email) > 150 else email
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
            )
            UserProfile.objects.get_or_create(
                user=user,
                defaults={"plan_id": plan.pk},
            )
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            messages.success(
                request,
                "Votre compte gratuit est prêt. Explorez SyLoc avec les exemples ; "
                "votre premier abonnement Premium peut inclure 14 jours d’essai (voir page Abonnement).",
            )
            return redirect("dashboard")
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = UserRegistrationForm()
    return render(request, "auth/register.html", {"form": form})


@login_required
def account_profile(request: HttpRequest) -> HttpResponse:
    """Prénom et nom affichés dans le menu ; modification sans passer par l’admin."""
    if request.method == "POST":
        form = AccountProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Votre profil a été mis à jour.")
            return redirect("account_profile")
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = AccountProfileForm(instance=request.user)
    return render(request, "auth/account_profile.html", {"form": form})


def _logout_user_other_sessions(current_session_key: str | None, user_id: int) -> int:
    """Supprime toutes les sessions actives de l'utilisateur sauf la session courante."""
    removed = 0
    uid = str(user_id)
    for sess in Session.objects.all().iterator():
        if current_session_key and sess.session_key == current_session_key:
            continue
        try:
            data = sess.get_decoded()
        except Exception:
            continue
        if str(data.get("_auth_user_id") or "") != uid:
            continue
        sess.delete()
        removed += 1
    return removed


@login_required
@require_POST
def account_logout_other_sessions(request: HttpRequest) -> HttpResponse:
    removed = _logout_user_other_sessions(request.session.session_key, request.user.pk)
    if removed:
        messages.success(
            request,
            f"Vous avez été déconnecté sur {removed} autre(s) appareil(s).",
        )
    else:
        messages.info(request, "Aucune autre session active à déconnecter.")
    return redirect("account_profile")


def _legal_page_context() -> dict:
    return {
        "legal_publisher": getattr(settings, "SYLOC_LEGAL_PUBLISHER", "") or "",
        "legal_siret": getattr(settings, "SYLOC_LEGAL_SIRET", "") or "",
        "legal_address": getattr(settings, "SYLOC_LEGAL_ADDRESS", "") or "",
        "legal_contact_email": getattr(settings, "SYLOC_LEGAL_CONTACT_EMAIL", "") or "",
        "legal_hosting": getattr(settings, "SYLOC_LEGAL_HOSTING_PROVIDER", "") or "Render",
        "has_openai": bool(getattr(settings, "OPENAI_API_KEY", None)),
        "has_email_smtp": bool((getattr(settings, "EMAIL_HOST", None) or "").strip()),
        "has_stripe": bool(getattr(settings, "STRIPE_SECRET_KEY", None)),
    }


def legal_mentions(request: HttpRequest) -> HttpResponse:
    return render(request, "rental/legal/mentions_legales.html", _legal_page_context())


def legal_privacy(request: HttpRequest) -> HttpResponse:
    return render(request, "rental/legal/confidentialite.html", _legal_page_context())


def legal_cookies(request: HttpRequest) -> HttpResponse:
    return render(request, "rental/legal/cookies.html", _legal_page_context())


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def account_delete_confirm(request: HttpRequest) -> HttpResponse:
    """Suppression définitive du compte bailleur et des données associées (RGPD, droit à l'effacement)."""
    if request.user.is_superuser:
        messages.error(
            request,
            "La suppression automatisée n’est pas disponible pour les comptes super-utilisateur.",
        )
        return redirect("account_profile")

    profile = _get_profile(request.user)
    stripe_subscription = bool(profile.stripe_subscription_id)

    if request.method == "POST":
        form = AccountDeleteForm(request.POST)
        if form.is_valid():
            if not request.user.check_password(form.cleaned_data["password"]):
                form.add_error("password", "Mot de passe incorrect.")
            else:
                user_pk = request.user.pk
                with transaction.atomic():
                    logout(request)
                    User.objects.filter(pk=user_pk).delete()
                    Organisation.objects.annotate(n=Count("members")).filter(n=0).delete()
                return HttpResponseRedirect(f"{reverse('home')}?compte_supprime=1")
    else:
        form = AccountDeleteForm()

    return render(
        request,
        "auth/account_delete_confirm.html",
        {
            "form": form,
            "stripe_subscription": stripe_subscription,
        },
    )


def _get_profile(user):
    """Retourne le profil utilisateur (créé avec essai gratuit si absent)."""
    try:
        return user.profile
    except UserProfile.DoesNotExist:
        try:
            default_plan = Plan.objects.get(slug=Plan.FREE)
        except Plan.DoesNotExist:
            default_plan = Plan.get_base()
        return UserProfile.objects.create(user=user, plan=default_plan)


def _require_premium(request):
    """Retourne None si l'utilisateur est premium, sinon redirige vers Abonnement avec message."""
    if not request.user.is_authenticated:
        return redirect(settings.LOGIN_URL)
    if not _get_profile(request.user).is_premium:
        messages.info(request, "Cette fonctionnalité est réservée aux abonnés Premium.")
        return redirect("subscription")
    return None


def _profile_can_manage_stripe_subscription(profile: UserProfile) -> bool:
    """True si l'utilisateur peut modifier son abonnement via Stripe (API ou portail)."""
    return bool(
        profile.stripe_customer_id
        and profile.stripe_subscription_id
        and getattr(settings, "STRIPE_SECRET_KEY", None)
    )


def _subscription_sandbox_enabled() -> bool:
    """True si les flux Stripe sont court-circuités et la formule est pilotée en mode test."""
    return bool(getattr(settings, "SYLOC_SUBSCRIPTION_SANDBOX", False))


def _stripe_any_price_configured() -> bool:
    """True si au moins un prix Stripe (basic ou Premium) est utilisable."""
    # En mode sandbox, on aligne l'UI locale/prod : aucun parcours Stripe affiché.
    if _subscription_sandbox_enabled():
        return False
    if not getattr(settings, "STRIPE_SECRET_KEY", None):
        return False
    try:
        base_plan = Plan.objects.get(slug=Plan.BASE)
        prem_plan = Plan.objects.get(slug=Plan.PREMIUM)
    except Plan.DoesNotExist:
        return False
    return bool(
        base_plan.stripe_price_id
        or base_plan.stripe_price_id_annual
        or prem_plan.stripe_price_id
        or prem_plan.stripe_price_id_annual
        or getattr(settings, "STRIPE_PRICE_ID_BASE", None)
        or getattr(settings, "STRIPE_PRICE_ID_BASE_ANNUAL", None)
        or getattr(settings, "STRIPE_PRICE_ID_PREMIUM", None)
        or getattr(settings, "STRIPE_PRICE_ID_PREMIUM_ANNUAL", None)
    )


@login_required
def premium_page(request: HttpRequest) -> HttpResponse:
    """Tableau de bord des fonctionnalités Premium (abonnés) ou aperçu de l'offre."""
    profile = _get_profile(request.user)
    stripe_configured = _stripe_any_price_configured()
    can_manage_subscription = _profile_can_manage_stripe_subscription(profile)
    subscription_sandbox = _subscription_sandbox_enabled()
    return render(
        request,
        "rental/premium.html",
        {
            "user_plan": profile.plan,
            "is_premium": profile.is_premium,
            "stripe_configured": stripe_configured,
            "can_manage_subscription": can_manage_subscription,
            "subscription_sandbox": subscription_sandbox,
        },
    )


# Libellés et montants affichés pour le choix mensuel/annuel (page abonnement)
PLAN_PRICES = {
    Plan.BASE: {"monthly": "7 €", "annual": "67 €"},
    Plan.PREMIUM: {"monthly": "15 €", "annual": "144 €"},
}


def _stripe_nested_price_id(price) -> str:
    """Extrait l'ID price_... (réponse Stripe : chaîne ou objet développé)."""
    if price is None:
        return ""
    if isinstance(price, str):
        return price.strip()
    if isinstance(price, dict):
        return (price.get("id") or "").strip()
    return (getattr(price, "id", None) or "").strip()


def _syloc_stripe_recurring_interval_for_price_id(
    price_id: str,
    base_plan: Plan,
    prem_plan: Plan,
) -> str | None:
    """« month » / « year » si l'ID est classé sans ambiguïté dans l'admin / .env ; sinon None."""
    pid = (price_id or "").strip()
    if not pid:
        return None
    monthly_ids, annual_ids = _configured_stripe_price_id_sets(base_plan, prem_plan)
    in_m = pid in monthly_ids
    in_a = pid in annual_ids
    if in_m and not in_a:
        return "month"
    if in_a and not in_m:
        return "year"
    return None


def _stripe_price_recurring_interval_by_id(price_id: str) -> str | None:
    """Intervalle de facturation du Price Stripe : 'month', 'year', ou None."""
    if not price_id:
        return None
    import stripe

    stripe.api_key = getattr(settings, "STRIPE_SECRET_KEY", None)
    if not stripe.api_key:
        return None
    try:
        p = stripe.Price.retrieve(price_id)
    except stripe.error.StripeError:
        return None
    rec = None
    if isinstance(p, dict):
        rec = p.get("recurring")
    else:
        rec = getattr(p, "recurring", None)
        if rec is None and callable(getattr(p, "get", None)):
            try:
                rec = p.get("recurring")
            except Exception:
                rec = None
        if rec is None:
            to_dict = getattr(p, "to_dict", None)
            if callable(to_dict):
                try:
                    rec = to_dict().get("recurring")
                except Exception:
                    rec = None
    if not rec:
        return None
    if isinstance(rec, dict):
        iv = rec.get("interval")
    else:
        iv = getattr(rec, "interval", None)
    return str(iv) if iv else None


def _stripe_unit_price_display(price_id: str) -> str | None:
    """Libellé affichable du montant TTC unitaire (ex. « 149 € ») depuis Price Stripe.

    Pas de cache : chaque affichage page Abonnement reflète tout de suite les montants Stripe.
    """
    if not price_id:
        return None
    import stripe

    stripe.api_key = getattr(settings, "STRIPE_SECRET_KEY", None)
    if not stripe.api_key:
        return None
    try:
        p = stripe.Price.retrieve(price_id)
    except stripe.error.StripeError:
        return None
    unit = getattr(p, "unit_amount", None)
    if unit is None:
        return None
    cur = (getattr(p, "currency", None) or "eur").lower()
    amount = unit / 100.0
    if cur == "eur":
        if float(amount) == int(amount):
            return f"{int(amount)} €"
        text = f"{amount:.2f}".replace(".", ",").rstrip("0").rstrip(",")
        return f"{text} €"
    return f"{amount} {cur.upper()}"


def _resolve_stripe_price_id(plan: Plan, plan_slug: str, interval: str) -> str:
    """Retourne l'ID de prix Stripe pour une formule et une période (mensuel / annuel).

    Les variables STRIPE_PRICE_ID_* (.env / settings) priment sur les champs du modèle
    Plan lorsqu'elles sont renseignées. Sinon un ancien ID « mensuel » en admin pouvait
    rester utilisé alors que l'annuel était correct, ce qui bloquait tout changement une
    fois passé en facturation mensuelle.
    """
    if interval not in ("monthly", "annual"):
        interval = "monthly"

    env_base_m = (getattr(settings, "STRIPE_PRICE_ID_BASE", None) or "").strip()
    env_base_a = (getattr(settings, "STRIPE_PRICE_ID_BASE_ANNUAL", None) or "").strip()
    env_prem_m = (getattr(settings, "STRIPE_PRICE_ID_PREMIUM", None) or "").strip()
    env_prem_a = (getattr(settings, "STRIPE_PRICE_ID_PREMIUM_ANNUAL", None) or "").strip()

    db_m = (plan.stripe_price_id or "").strip()
    db_a = (plan.stripe_price_id_annual or "").strip()

    if plan_slug == Plan.PREMIUM:
        m = (env_prem_m or db_m or "").strip()
        a = (env_prem_a or db_a or "").strip()
        if interval == "monthly":
            return m
        # Annuel identique au mensuel : erreur fréquente sur l’offre Basic en admin (copier-coller).
        if a and m and a == m:
            return ""
        return a

    if plan_slug == Plan.BASE:
        m = (env_base_m or db_m or "").strip()
        a = (env_base_a or db_a or "").strip()
        if interval == "monthly":
            return m
        if a and m and a == m:
            return ""
        return a

    if interval == "monthly":
        m = (db_m or "").strip()
        a = (db_a or "").strip()
        return m
    a = (db_a or "").strip()
    m = (db_m or "").strip()
    if a and m and a == m:
        return ""
    return a


def _stripe_subscription_to_dict(sub) -> dict:
    """Normalise une Subscription Stripe (objet API ou dict) pour _webhook_set_plan_from_subscription."""
    if isinstance(sub, dict):
        return sub
    to_dict = getattr(sub, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    try:
        return dict(sub)
    except Exception:
        return {}


def _plan_interval_availability(plan: Plan, plan_slug: str) -> dict[str, bool]:
    """Indique si les boutons Stripe mensuel / annuel sont affichables pour ce Plan."""
    stripe_ok = bool(getattr(settings, "STRIPE_SECRET_KEY", None))
    fallback_monthly = None
    fallback_annual = None
    if plan_slug == Plan.PREMIUM:
        fallback_monthly = getattr(settings, "STRIPE_PRICE_ID_PREMIUM", None)
        fallback_annual = getattr(settings, "STRIPE_PRICE_ID_PREMIUM_ANNUAL", None)
    elif plan_slug == Plan.BASE:
        fallback_monthly = getattr(settings, "STRIPE_PRICE_ID_BASE", None)
        fallback_annual = getattr(settings, "STRIPE_PRICE_ID_BASE_ANNUAL", None)
    return {
        "has_monthly": stripe_ok and bool(plan.stripe_price_id or fallback_monthly),
        "has_annual": stripe_ok and bool(plan.stripe_price_id_annual or fallback_annual),
    }


def _configured_stripe_price_id_sets(base_plan: Plan, prem_plan: Plan) -> tuple[set[str], set[str]]:
    """IDs price_... mensuels et annuels connus (admin + .env) pour basic et premium."""
    monthly: set[str] = set()
    annual: set[str] = set()
    for p in (base_plan, prem_plan):
        if p.stripe_price_id:
            monthly.add(p.stripe_price_id.strip())
        if p.stripe_price_id_annual:
            annual.add(p.stripe_price_id_annual.strip())
    for v in (
        getattr(settings, "STRIPE_PRICE_ID_BASE", None),
        getattr(settings, "STRIPE_PRICE_ID_PREMIUM", None),
    ):
        s = (v or "").strip()
        if s:
            monthly.add(s)
    for v in (
        getattr(settings, "STRIPE_PRICE_ID_BASE_ANNUAL", None),
        getattr(settings, "STRIPE_PRICE_ID_PREMIUM_ANNUAL", None),
    ):
        s = (v or "").strip()
        if s:
            annual.add(s)
    return monthly, annual


def _billing_adjective_from_stripe_price_object(price) -> str | None:
    """À partir d'un Price Stripe (développé), retourne mensuel / annuel."""
    if price is None or isinstance(price, str):
        return None
    rec = price.get("recurring") if isinstance(price, dict) else getattr(price, "recurring", None)
    if not rec:
        return None
    interval = rec.get("interval") if isinstance(rec, dict) else getattr(rec, "interval", None)
    if interval == "year":
        return "annuel"
    if interval == "month":
        return "mensuel"
    return None


def _billing_interval_code_from_stripe_price_object(price) -> str | None:
    """'monthly' | 'annual' | None selon le Price Stripe développé."""
    if price is None or isinstance(price, str):
        return None
    rec = price.get("recurring") if isinstance(price, dict) else getattr(price, "recurring", None)
    if not rec:
        return None
    interval = rec.get("interval") if isinstance(rec, dict) else getattr(rec, "interval", None)
    if interval == "year":
        return "annual"
    if interval == "month":
        return "monthly"
    return None


def _billing_interval_code_for_price(
    price,
    base_plan: Plan,
    prem_plan: Plan,
) -> str | None:
    """Code SyLoc « monthly » / « annual » depuis un Price Stripe (objet développé ou id seul)."""
    if price is None:
        return None
    code = _billing_interval_code_from_stripe_price_object(price)
    if code in ("annual", "monthly"):
        return code
    adj = _billing_adjective_from_stripe_price_object(price)
    if adj == "annuel":
        return "annual"
    if adj == "mensuel":
        return "monthly"
    pid = _stripe_nested_price_id(price)
    if not pid:
        return None
    monthly_ids, annual_ids = _configured_stripe_price_id_sets(base_plan, prem_plan)
    if pid in annual_ids:
        return "annual"
    if pid in monthly_ids:
        return "monthly"
    return None


def _subscription_billing_period_info(
    profile: UserProfile,
    base_plan: Plan,
    prem_plan: Plan,
) -> tuple[str, str | None]:
    """(adjectif affichage, code période 'monthly'|'annual'|None) — une requête Stripe."""
    if not profile.stripe_subscription_id:
        return "", None
    import stripe

    stripe.api_key = getattr(settings, "STRIPE_SECRET_KEY", None)
    if not stripe.api_key:
        return "", None
    try:
        sub = stripe.Subscription.retrieve(
            profile.stripe_subscription_id,
            expand=["items.data.price"],
        )
        items = list(sub["items"].data)
        if not items:
            return "", None
        price = items[0].price
        code = _billing_interval_code_for_price(price, base_plan, prem_plan)
        if code == "annual":
            return "annuel", "annual"
        if code == "monthly":
            return "mensuel", "monthly"
        return "", None
    except stripe.error.StripeError:
        return "", None


def public_offers_page(request: HttpRequest) -> HttpResponse:
    """Page publique : comparatif Basic / Premium pour les visiteurs (sans connexion)."""
    if request.user.is_authenticated:
        return redirect("subscription")
    base_plan = get_object_or_404(Plan, slug=Plan.BASE)
    prem_plan = get_object_or_404(Plan, slug=Plan.PREMIUM)
    stripe_configured = _stripe_any_price_configured()
    return render(
        request,
        "rental/public_offers.html",
        {
            "base_plan": base_plan,
            "prem_plan": prem_plan,
            "stripe_configured": stripe_configured,
        },
    )


@login_required
def subscription_page(request: HttpRequest) -> HttpResponse:
    """Page Abonnement : choix basic / Premium puis passage par Stripe (mensuel ou annuel)."""
    profile = _get_profile(request.user)
    base_plan = get_object_or_404(Plan, slug=Plan.BASE)
    prem_plan = get_object_or_404(Plan, slug=Plan.PREMIUM)
    subscription_sandbox = _subscription_sandbox_enabled()
    stripe_configured = _stripe_any_price_configured()
    can_manage_subscription = (
        not subscription_sandbox and _profile_can_manage_stripe_subscription(profile)
    )
    base_iv = _plan_interval_availability(base_plan, Plan.BASE)
    prem_iv = _plan_interval_availability(prem_plan, Plan.PREMIUM)
    subscription_billing_period = ""
    subscription_interval = None
    if subscription_sandbox:
        subscription_interval = request.session.get("subscription_sandbox_interval", "monthly")
        if subscription_interval not in ("monthly", "annual"):
            subscription_interval = "monthly"
        subscription_billing_period = "mensuel" if subscription_interval == "monthly" else "annuel"
    elif profile.stripe_subscription_id:
        subscription_billing_period, subscription_interval = _subscription_billing_period_info(
            profile, base_plan, prem_plan
        )

    stripe_plan_slug = (
        profile.plan.slug
        if profile.plan.slug in (Plan.BASE, Plan.PREMIUM)
        else None
    )
    if subscription_sandbox:
        switch_to_annual_available = bool(
            stripe_plan_slug and subscription_interval == "monthly"
        )
        switch_to_monthly_available = bool(
            stripe_plan_slug and subscription_interval == "annual"
        )
    else:
        switch_to_annual_available = bool(
            can_manage_subscription
            and stripe_plan_slug
            and subscription_interval == "monthly"
            and (
                (stripe_plan_slug == Plan.BASE and base_iv["has_annual"])
                or (stripe_plan_slug == Plan.PREMIUM and prem_iv["has_annual"])
            )
        )
        switch_to_monthly_available = bool(
            can_manage_subscription
            and stripe_plan_slug
            and subscription_interval == "annual"
            and (
                (stripe_plan_slug == Plan.BASE and base_iv["has_monthly"])
                or (stripe_plan_slug == Plan.PREMIUM and prem_iv["has_monthly"])
            )
        )

    show_subscription_dashboard = bool(
        profile.stripe_subscription_id
        or (
            subscription_sandbox
            and profile.plan.slug in (Plan.BASE, Plan.PREMIUM)
        )
    )
    subscription_tariff_highlight_subscription = bool(
        profile.stripe_subscription_id
        or (
            subscription_sandbox
            and profile.plan.slug in (Plan.BASE, Plan.PREMIUM)
        )
    )

    price_label_base_m = PLAN_PRICES[Plan.BASE]["monthly"]
    price_label_base_a = PLAN_PRICES[Plan.BASE]["annual"]
    price_label_prem_m = PLAN_PRICES[Plan.PREMIUM]["monthly"]
    price_label_prem_a = PLAN_PRICES[Plan.PREMIUM]["annual"]
    if stripe_configured:
        _pid = _resolve_stripe_price_id(base_plan, Plan.BASE, "monthly")
        _d = _stripe_unit_price_display(_pid)
        if _d:
            price_label_base_m = _d
        _pid = _resolve_stripe_price_id(base_plan, Plan.BASE, "annual")
        _d = _stripe_unit_price_display(_pid)
        if _d:
            price_label_base_a = _d
        _pid = _resolve_stripe_price_id(prem_plan, Plan.PREMIUM, "monthly")
        _d = _stripe_unit_price_display(_pid)
        if _d:
            price_label_prem_m = _d
        _pid = _resolve_stripe_price_id(prem_plan, Plan.PREMIUM, "annual")
        _d = _stripe_unit_price_display(_pid)
        if _d:
            price_label_prem_a = _d

    user_active_property_count = active_property_count_for_user(request.user)
    user_volume_segment = property_volume_segment_for_count(user_active_property_count)
    subscribed_segment_keys = frozenset({"perso", "actif", "pro", "volume"})
    subscribed_volume_key = (getattr(profile, "volume_segment_key", None) or "perso").strip()
    if subscribed_volume_key not in subscribed_segment_keys:
        subscribed_volume_key = "perso"
    subscription_volume_tiers = tuple(
        {**dict(tier), "is_user_palier": subscribed_volume_key == tier["key"]}
        for tier in SUBSCRIPTION_VOLUME_TIER_ROWS
    )
    subscribed_volume_label = volume_segment_label_for_key(subscribed_volume_key)

    return render(
        request,
        "rental/subscription.html",
        {
            "profile": profile,
            "user_plan": profile.plan,
            "is_premium": profile.is_premium,
            "base_plan": base_plan,
            "prem_plan": prem_plan,
            "prices_base": PLAN_PRICES[Plan.BASE],
            "prices_prem": PLAN_PRICES[Plan.PREMIUM],
            "price_label_base_monthly": price_label_base_m,
            "price_label_base_annual": price_label_base_a,
            "price_label_prem_monthly": price_label_prem_m,
            "price_label_prem_annual": price_label_prem_a,
            "base_has_monthly": base_iv["has_monthly"],
            "base_has_annual": base_iv["has_annual"],
            "prem_has_monthly": prem_iv["has_monthly"],
            "prem_has_annual": prem_iv["has_annual"],
            "stripe_configured": stripe_configured,
            "can_manage_subscription": can_manage_subscription,
            "subscription_sandbox": subscription_sandbox,
            "show_subscription_dashboard": show_subscription_dashboard,
            "subscription_tariff_highlight_subscription": subscription_tariff_highlight_subscription,
            "subscription_billing_period": subscription_billing_period,
            "subscription_interval": subscription_interval,
            "stripe_plan_slug": stripe_plan_slug,
            "switch_to_annual_available": switch_to_annual_available,
            "switch_to_monthly_available": switch_to_monthly_available,
            "user_volume_segment": user_volume_segment,
            "user_active_property_count": user_active_property_count,
            "subscription_volume_tiers": subscription_volume_tiers,
            "subscribed_volume_key": subscribed_volume_key,
            "subscribed_volume_label": subscribed_volume_label,
        },
    )


@login_required
@require_POST
def subscription_sandbox_set(request: HttpRequest) -> HttpResponse:
    """Mode test : met à jour l'offre et le palier volume sans appeler Stripe."""
    if not _subscription_sandbox_enabled():
        raise Http404()
    plan_slug = (request.POST.get("plan") or "").strip().lower()
    vol = (request.POST.get("volume_segment_key") or "").strip().lower()
    interval = (request.POST.get("interval") or "monthly").strip().lower()
    if plan_slug not in (Plan.FREE, Plan.BASE, Plan.PREMIUM):
        messages.error(request, "Formule inconnue.")
        return redirect("subscription")
    segment_keys = frozenset(k for k, _ in UserProfile.VOLUME_SEGMENT_KEY_CHOICES)
    if vol not in segment_keys:
        messages.error(request, "Palier volume invalide.")
        return redirect("subscription")
    if interval not in ("monthly", "annual"):
        interval = "monthly"
    request.session["subscription_sandbox_interval"] = interval
    request.session.modified = True
    profile = _get_profile(request.user)
    plan = get_object_or_404(Plan, slug=plan_slug)
    profile.plan = plan
    profile.volume_segment_key = vol
    profile.save(update_fields=["plan", "volume_segment_key"])
    log_user_activity(
        request,
        UserActivityLog.ACTION_CREATE,
        object_repr="Abonnement — mode test (sans Stripe)",
        details=(
            f"plan={plan_slug}\nvolume_segment_key={vol}\nbilling_interval={interval}\n"
            "(aucun appel Stripe)"
        ),
    )
    messages.success(
        request,
        "Réglages enregistrés en mode test : votre formule et votre palier biens ont été mis à jour (sans paiement).",
    )
    return redirect("subscription")


@login_required
def plan_choice(request: HttpRequest, plan_slug: str) -> HttpResponse:
    """Page de choix de l'abonnement : mensuel ou annuel pour le plan donné (base ou premium)."""
    if _subscription_sandbox_enabled():
        messages.info(
            request,
            "Le paiement en ligne est désactivé en mode test. Utilisez le formulaire en haut de la page Abonnement pour changer de formule et de palier.",
        )
        return redirect("subscription")
    if plan_slug not in (Plan.BASE, Plan.PREMIUM):
        return redirect("home")
    plan = get_object_or_404(Plan, slug=plan_slug)
    profile = _get_profile(request.user)
    # Si déjà abonné à ce plan avec un abonnement Stripe actif, proposer de gérer
    if (profile.plan_id == plan.pk and profile.stripe_subscription_id) or (
        profile.is_premium and plan_slug == Plan.PREMIUM and profile.stripe_subscription_id
    ):
        messages.info(request, f"Vous avez déjà l'abonnement {plan.name}. Vous pouvez gérer votre abonnement ci-dessous.")
        return redirect("subscription")
    prices = PLAN_PRICES.get(plan_slug, {"monthly": "", "annual": ""})
    # Autoriser une configuration hybride : admin (Plan) ou .env.
    fallback_monthly = None
    fallback_annual = None
    if plan_slug == Plan.PREMIUM:
        fallback_monthly = getattr(settings, "STRIPE_PRICE_ID_PREMIUM", None)
        fallback_annual = getattr(settings, "STRIPE_PRICE_ID_PREMIUM_ANNUAL", None)
    elif plan_slug == Plan.BASE:
        fallback_monthly = getattr(settings, "STRIPE_PRICE_ID_BASE", None)
        fallback_annual = getattr(settings, "STRIPE_PRICE_ID_BASE_ANNUAL", None)
    has_monthly = bool(plan.stripe_price_id or fallback_monthly)
    has_annual = bool(plan.stripe_price_id_annual or fallback_annual)
    stripe_ok = bool(getattr(settings, "STRIPE_SECRET_KEY", None))
    return render(
        request,
        "rental/plan_choice.html",
        {
            "plan": plan,
            "prices": prices,
            "has_monthly": has_monthly and stripe_ok,
            "has_annual": has_annual and stripe_ok,
            "stripe_configured": stripe_ok,
            "can_manage_subscription": _profile_can_manage_stripe_subscription(profile),
        },
    )


@login_required
def notifications_page(request: HttpRequest) -> HttpResponse:
    """Page Notifications : annonces SyLoc et fil des échanges interventions (portail locataire)."""
    announcements = Announcement.objects.filter(is_published=True).order_by("-created_at")[:50]
    intervention_timeline = intervention_exchange_timeline_for_landlord(
        request.user,
        limit=60,
    )
    return render(
        request,
        "rental/notifications.html",
        {
            "announcements": announcements,
            "intervention_timeline": intervention_timeline,
        },
    )


@login_required
@require_GET
def ui_maintenance_badge_count(request: HttpRequest) -> JsonResponse:
    """Compteur pour la pastille « Demandes locataires » (rafraîchissement sans recharger la page)."""
    profile = _get_profile(request.user)
    if not profile.is_premium:
        return JsonResponse({"count": 0})
    n = maintenance_requests_pending_landlord_action_qs(request.user).count()
    return JsonResponse({"count": n})


@login_required
def create_checkout_session(request: HttpRequest) -> HttpResponse:
    """Crée une session Stripe Checkout pour l'abonnement (POST : plan + interval)."""
    if request.method != "POST":
        return redirect("subscription")
    if _subscription_sandbox_enabled():
        messages.info(
            request,
            "Paiement Stripe désactivé en mode test. Utilisez la page Abonnement pour simuler votre formule.",
        )
        return redirect("subscription")
    plan_slug = request.POST.get("plan") or request.GET.get("plan")
    interval = request.POST.get("interval") or request.GET.get("interval")  # monthly | annual
    if not plan_slug or plan_slug not in (Plan.BASE, Plan.PREMIUM):
        plan_slug = Plan.PREMIUM
    if interval not in ("monthly", "annual"):
        interval = "monthly"
    plan = get_object_or_404(Plan, slug=plan_slug)
    profile = _get_profile(request.user)
    # Déjà abonné : ne pas créer un second abonnement via Checkout
    if profile.stripe_subscription_id:
        messages.info(
            request,
            "Vous avez déjà un abonnement actif. Utilisez « Choisir » pour changer de formule "
            "(mise à jour sans nouveau paiement initial) ou « Gérer mon abonnement » pour le portail Stripe.",
        )
        return redirect("subscription")
    price_id = _resolve_stripe_price_id(plan, plan_slug, interval)
    import stripe
    stripe.api_key = getattr(settings, "STRIPE_SECRET_KEY", None)
    if not stripe.api_key or not price_id:
        messages.error(request, "Paiement non configuré pour cette formule. Contactez-nous.")
        return redirect("plan_choice", plan_slug=plan_slug)
    base_url = request.build_absolute_uri("/").rstrip("/")
    success_url = base_url + reverse("checkout_success") + "?session_id={CHECKOUT_SESSION_ID}"
    cancel_url = base_url + reverse("plan_choice", args=[plan_slug])
    try:
        customer_id = profile.stripe_customer_id or None
        if not customer_id:
            customer = stripe.Customer.create(
                email=request.user.email or "",
                metadata={"user_id": str(request.user.pk)},
            )
            customer_id = customer.id
            profile.stripe_customer_id = customer_id
            profile.save(update_fields=["stripe_customer_id"])
        subscription_data = {
            "metadata": {"user_id": str(request.user.pk), "plan": plan_slug},
        }
        if plan_slug == Plan.PREMIUM and profile.plan.slug == Plan.FREE:
            subscription_data["trial_period_days"] = 14
        session = stripe.checkout.Session.create(
            customer=customer_id,
            client_reference_id=str(request.user.pk),
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"user_id": str(request.user.pk), "plan": plan_slug},
            subscription_data=subscription_data,
        )
        log_user_activity(
            request,
            UserActivityLog.ACTION_CREATE,
            object_repr="Abonnement — session Stripe Checkout",
            details=(
                f"Ouverture paiement abonnement\nplan={plan_slug}\ninterval={interval}\n"
                f"checkout_session_id={session.id}\nprice_id={price_id}"
            ),
        )
        return redirect(session.url)
    except Exception as e:
        messages.error(request, f"Erreur lors de la création du paiement : {e}")
        return redirect("plan_choice", plan_slug=plan_slug)


@login_required
@require_POST
def change_subscription_plan(request: HttpRequest) -> HttpResponse:
    """Met à jour l'abonnement Stripe existant (changement basic ↔ premium, mensuel ↔ annuel).

    Évite un second abonnement : remplace le price sur la subscription courante (prorata Stripe).
    """
    if _subscription_sandbox_enabled():
        messages.info(
            request,
            "Modification d’abonnement Stripe désactivée en mode test. Utilisez le formulaire sur la page Abonnement.",
        )
        return redirect("subscription")
    import stripe

    plan_slug = (request.POST.get("plan") or "").strip()
    interval = (request.POST.get("interval") or "monthly").strip()
    if plan_slug not in (Plan.BASE, Plan.PREMIUM):
        messages.error(request, "Formule invalide.")
        return redirect("subscription")
    if interval not in ("monthly", "annual"):
        interval = "monthly"

    profile = _get_profile(request.user)
    if not profile.stripe_subscription_id or not profile.stripe_customer_id:
        messages.info(request, "Pour souscrire, choisissez une formule (premier paiement via Stripe).")
        return redirect("subscription")

    plan = get_object_or_404(Plan, slug=plan_slug)
    base_plan = get_object_or_404(Plan, slug=Plan.BASE)
    prem_plan = get_object_or_404(Plan, slug=Plan.PREMIUM)
    stripe.api_key = getattr(settings, "STRIPE_SECRET_KEY", None)
    if not stripe.api_key:
        messages.error(request, "Paiement non configuré.")
        return redirect("subscription")

    new_price_id = _resolve_stripe_price_id(plan, plan_slug, interval)
    if not new_price_id:
        messages.error(request, "Ce tarif n'est pas disponible pour cette formule.")
        return redirect("plan_choice", plan_slug=plan_slug)

    want_stripe_interval = "year" if interval == "annual" else "month"
    interval_from_syloc = _syloc_stripe_recurring_interval_for_price_id(
        new_price_id, base_plan, prem_plan
    )
    if interval_from_syloc is not None:
        if interval_from_syloc != want_stripe_interval:
            messages.error(
                request,
                "Le price ID utilisé ne correspond pas à la période choisie. "
                f"ID Stripe : « {new_price_id} » — dans SyLoc (admin « Offres » ou .env), cet ID est enregistré "
                f"comme tarif {'annuel' if interval_from_syloc == 'year' else 'mensuel'}, "
                f"alors que vous avez demandé une facturation {'annuelle' if want_stripe_interval == 'year' else 'mensuelle'}. "
                "Vérifiez que STRIPE_PRICE_ID_BASE / STRIPE_PRICE_ID_PREMIUM = prix mensuels Stripe, "
                "et STRIPE_PRICE_ID_BASE_ANNUAL / STRIPE_PRICE_ID_PREMIUM_ANNUAL = prix annuels (récurrents « par an » dans Stripe).",
            )
            return redirect("subscription")
    else:
        new_stripe_interval = _stripe_price_recurring_interval_by_id(new_price_id)
        if new_stripe_interval and new_stripe_interval != want_stripe_interval:
            messages.error(
                request,
                "Le prix Stripe ne correspond pas à la période choisie. "
                f"ID : « {new_price_id} » — côté Stripe, ce prix est en facturation "
                f"{'annuelle' if new_stripe_interval == 'year' else 'mensuelle'}, "
                f"alors qu’ici une facturation {'annuelle' if want_stripe_interval == 'year' else 'mensuelle'} était attendue. "
                "Dans le dashboard Stripe, ouvrez le produit / prix : un abonnement annuel doit avoir une récurrence « year », "
                "le mensuel « month ». Puis mettez à jour les bons price_… dans l’admin SyLoc ou le fichier .env.",
            )
            return redirect("subscription")

    try:
        sub = stripe.Subscription.retrieve(
            profile.stripe_subscription_id,
            expand=["items.data.price"],
        )
    except stripe.error.StripeError as e:
        messages.error(
            request,
            f"Impossible de lire votre abonnement : {getattr(e, 'user_message', None) or str(e)}",
        )
        return redirect("subscription")

    cust = sub.customer
    cust_id = cust if isinstance(cust, str) else getattr(cust, "id", None) or str(cust)
    if str(cust_id) != str(profile.stripe_customer_id):
        messages.error(request, "L'abonnement ne correspond pas à votre compte.")
        return redirect("subscription")

    if sub.status not in ("active", "trialing", "past_due"):
        messages.warning(
            request,
            "Cet abonnement ne peut pas être modifié ici. Utilisez « Gérer mon abonnement ».",
        )
        return redirect("subscription")

    # Stripe Python 9+ : `sub.items` est la méthode dict-like, pas les lignes d'abonnement.
    items = list(sub["items"].data)
    if not items:
        messages.error(request, "Abonnement Stripe incomplet.")
        return redirect("subscription")

    first = items[0]
    item_id = first.id
    current_price_id = _stripe_nested_price_id(first.price)
    if not current_price_id:
        messages.error(request, "Abonnement Stripe : prix de la ligne introuvable.")
        return redirect("subscription")

    # Formule actuelle : métadonnées Stripe d’abord, sinon profil SyLoc.
    sub_meta_raw = dict(sub.metadata or {}).get("plan")
    sm = (
        (sub_meta_raw or "").strip().lower()
        if isinstance(sub_meta_raw, str)
        else ""
    )
    current_plan_slug = sm if sm in (Plan.BASE, Plan.PREMIUM) else ""
    if not current_plan_slug and profile.plan.slug in (Plan.BASE, Plan.PREMIUM):
        current_plan_slug = profile.plan.slug

    switching_product = bool(current_plan_slug and current_plan_slug != plan_slug)

    # Doublon uniquement si c’est exactement le même Price Stripe. Ne pas se fier à la « période »
    # déduite par SyLoc (admin / .env) : une erreur de classement déclenchait à tort « déjà mensuel »
    # alors que l’abonnement était annuel (ou l’inverse) lors du passage mensuel ↔ annuel.
    if not switching_product and current_price_id.strip() == new_price_id.strip():
        periode = "annuelle" if interval == "annual" else "mensuelle"
        messages.info(
            request,
            f"Vous êtes déjà sur cette option de facturation ({periode}).",
        )
        return redirect("subscription")

    md = dict(sub.metadata or {})
    md["user_id"] = str(request.user.pk)
    md["plan"] = plan_slug

    try:
        updated = stripe.Subscription.modify(
            sub.id,
            items=[{"id": item_id, "price": new_price_id}],
            metadata=md,
            proration_behavior="create_prorations",
        )
        _webhook_set_plan_from_subscription(_stripe_subscription_to_dict(updated))
        log_user_activity(
            request,
            UserActivityLog.ACTION_UPDATE,
            object_repr="Abonnement Stripe (changement de formule)",
            details=(
                f"Changement abonnement\nancien_plan={current_plan_slug or '?'}\n"
                f"nouveau_plan={plan_slug}\ninterval={interval}\n"
                f"ancien_price_id={current_price_id}\nnouveau_price_id={new_price_id}\n"
                f"subscription_id={profile.stripe_subscription_id}"
            ),
        )
    except stripe.error.StripeError as e:
        messages.error(
            request,
            f"Impossible de changer de formule : {getattr(e, 'user_message', None) or str(e)}",
        )
        return redirect("subscription")

    periode_ok = "annuelle" if interval == "annual" else "mensuelle"
    messages.success(
        request,
        f"Votre abonnement a été mis à jour (facturation {periode_ok}). "
        "Stripe applique une facturation au prorata ; le détail figure sur votre facture.",
    )
    return redirect("subscription")


@login_required
def create_billing_portal_session(request: HttpRequest) -> HttpResponse:
    """Redirige vers le portail client Stripe (gérer abonnement, moyen de paiement, annuler)."""
    if request.method != "POST":
        return redirect("subscription")
    if _subscription_sandbox_enabled():
        messages.info(
            request,
            "Le portail de facturation Stripe n’est pas disponible en mode test.",
        )
        return redirect("subscription")
    profile = _get_profile(request.user)
    if not profile.stripe_customer_id:
        messages.info(request, "Aucun abonnement en ligne à gérer.")
        return redirect("subscription")
    import stripe
    stripe.api_key = getattr(settings, "STRIPE_SECRET_KEY", None)
    if not stripe.api_key:
        messages.error(request, "Paiement non configuré.")
        return redirect("subscription")
    base_url = request.build_absolute_uri("/").rstrip("/")
    return_url = base_url + reverse("subscription")
    try:
        session = stripe.billing_portal.Session.create(
            customer=profile.stripe_customer_id,
            return_url=return_url,
        )
        log_user_activity(
            request,
            UserActivityLog.ACTION_VIEW,
            object_repr="Portail client Stripe",
            details=f"Ouverture portail facturation\nbilling_portal_session={session.id}",
        )
        return redirect(session.url)
    except Exception as e:
        messages.error(request, f"Impossible d'ouvrir le portail : {e}")
        return redirect("subscription")


@login_required
def checkout_success(request: HttpRequest) -> HttpResponse:
    """Page affichée après paiement Stripe réussi."""
    session_id = request.GET.get("session_id")
    if session_id:
        messages.success(
            request,
            "Merci ! Votre paiement est pris en compte. Votre abonnement sera activé sous peu.",
        )
    return redirect("subscription")


def checkout_cancel(request: HttpRequest) -> HttpResponse:
    """Redirection si l'utilisateur annule le paiement Stripe."""
    messages.info(request, "Paiement annulé.")
    return redirect("subscription")


@csrf_exempt
@require_http_methods(["POST"])
def stripe_webhook(request: HttpRequest) -> HttpResponse:
    """Webhook Stripe : met à jour le plan selon les événements d'abonnement."""
    import stripe

    stripe.api_key = getattr(settings, "STRIPE_SECRET_KEY", None)
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", None)
    if not secret:
        return HttpResponse("Webhook secret not configured", status=500)
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except ValueError:
        return HttpResponse("Invalid payload", status=400)
    except stripe.SignatureVerificationError:
        return HttpResponse("Invalid signature", status=400)
    event_type = event.get("type") or ""
    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        # Permet d'activer l'accès Premium immédiatement après Checkout (avant/indépendamment des events subscription.*)
        if session.get("mode") == "subscription":
            _webhook_set_from_checkout_session(session)
    elif event_type == "invoice.payment_succeeded":
        invoice = event["data"]["object"]
        sub_id = invoice.get("subscription")
        if sub_id:
            try:
                sub = stripe.Subscription.retrieve(sub_id)
                if sub and sub.get("status") in ("active", "trialing"):
                    _webhook_set_plan_from_subscription(sub)
            except Exception:
                pass
    elif event_type == "invoice.payment_failed":
        # En cas d'impayé, on ne force pas un downgrade immédiat (Stripe peut retenter) :
        # on se base sur l'état réel de la subscription quand il change (customer.subscription.updated/deleted).
        pass
    elif event_type == "customer.subscription.created":
        sub = event["data"]["object"]
        if sub.get("status") in ("active", "trialing"):
            _webhook_set_plan_from_subscription(sub)
    elif event_type == "customer.subscription.updated":
        sub = event["data"]["object"]
        if sub.get("status") in ("active", "trialing"):
            _webhook_set_plan_from_subscription(sub)
        else:
            _webhook_set_base(sub)
    elif event_type == "customer.subscription.deleted":
        sub = event["data"]["object"]
        _webhook_set_base(sub)
    return HttpResponse(status=200)


def _webhook_set_from_checkout_session(session) -> None:
    """Active le plan et lie customer/subscription à partir d'une Checkout Session."""
    user_id = (session.get("client_reference_id") or "").strip()
    if not user_id:
        user_id = (session.get("metadata", {}) or {}).get("user_id") or ""
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return

    customer_id = session.get("customer") or ""
    subscription_id = session.get("subscription") or ""
    plan_slug = ((session.get("metadata", {}) or {}).get("plan") or "").strip()

    plan = None
    if plan_slug in (Plan.BASE, Plan.PREMIUM, Plan.FREE):
        plan = Plan.objects.filter(slug=plan_slug).first()

    try:
        profile = UserProfile.objects.get(user_id=user_id_int)
    except UserProfile.DoesNotExist:
        return

    update_fields: list[str] = []
    if customer_id and profile.stripe_customer_id != customer_id:
        profile.stripe_customer_id = customer_id
        update_fields.append("stripe_customer_id")
    if subscription_id and profile.stripe_subscription_id != subscription_id:
        profile.stripe_subscription_id = subscription_id
        update_fields.append("stripe_subscription_id")
    if plan and profile.plan_id != plan.pk:
        profile.plan = plan
        update_fields.append("plan_id")

    if update_fields:
        profile.save(update_fields=update_fields)


def _webhook_set_plan_from_subscription(sub) -> None:
    """Associe le profil au plan correspondant au price_id de l'abonnement Stripe."""
    user_id = sub.get("metadata", {}).get("user_id")
    if not user_id:
        return
    items = sub.get("items", {}).get("data", [])
    if not items:
        return
    price_id = items[0].get("price", {}).get("id")
    if not price_id:
        return
    plan = Plan.objects.filter(
        Q(stripe_price_id=price_id) | Q(stripe_price_id_annual=price_id)
    ).first()
    if not plan:
        base_pid = getattr(settings, "STRIPE_PRICE_ID_BASE", None) or ""
        base_annual = getattr(settings, "STRIPE_PRICE_ID_BASE_ANNUAL", None) or ""
        prem_pid = getattr(settings, "STRIPE_PRICE_ID_PREMIUM", None) or ""
        prem_annual = getattr(settings, "STRIPE_PRICE_ID_PREMIUM_ANNUAL", None) or ""
        if price_id and price_id in (base_pid, base_annual):
            plan = Plan.objects.filter(slug=Plan.BASE).first()
        elif price_id and price_id in (prem_pid, prem_annual):
            plan = Plan.objects.filter(slug=Plan.PREMIUM).first()
    if not plan:
        return
    try:
        profile = UserProfile.objects.get(user_id=int(user_id))
        profile.stripe_subscription_id = sub["id"]
        profile.plan = plan
        profile.save(update_fields=["stripe_subscription_id", "plan_id"])
    except (UserProfile.DoesNotExist, ValueError):
        pass


def _webhook_set_base(sub) -> None:
    """Repasse en essai gratuit les profils liés à cette subscription (fin d’abonnement)."""
    try:
        free_plan_id = Plan.get_free().pk
    except Plan.DoesNotExist:
        free_plan_id = Plan.get_base().pk
    UserProfile.objects.filter(stripe_subscription_id=sub["id"]).update(
        plan_id=free_plan_id,
        stripe_subscription_id="",
    )


def _sync_user_email_for_login(user, new_email: str) -> None:
    """Aligne email et identifiant de connexion (comme à l'inscription)."""
    new_email = new_email.strip().lower()
    user.email = new_email
    user.username = new_email[:150]
    user.save(update_fields=["email", "username"])


@login_required
def team_page(request: HttpRequest) -> HttpResponse:
    """Page Équipe : organisation et membres (Premium). Permet aux propriétaires d'ajouter des membres."""
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    from .models import get_organisations_for_user

    user = request.user
    profile = _get_profile(user)
    organisations = get_organisations_for_user(user).prefetch_related("members__user")
    # Organisation « par défaut » : celle dont l'utilisateur est propriétaire, ou la première
    default_org = None
    for org in organisations:
        membership = org.members.filter(user=user).first()
        if membership and membership.role == OrganisationMember.ROLE_OWNER:
            default_org = org
            break
    if default_org is None and organisations:
        default_org = organisations.first()
    if not default_org:
        default_org = get_or_create_default_organisation(user)
        organisations = get_organisations_for_user(user).prefetch_related("members__user")

    is_owner = default_org.members.filter(user=user, role=OrganisationMember.ROLE_OWNER).exists()
    if request.method == "POST" and not is_owner:
        messages.error(request, "Action réservée au propriétaire de l'organisation.")
        return redirect("rental:team_page")
    members = list(default_org.members.select_related("user").order_by("-role", "user__email"))
    form = None
    org_name_form = None
    if is_owner:
        org_name_form = OrganisationNameForm(instance=default_org)
        form = InviteMemberForm(organisation=default_org)
        if request.method == "POST":
            action = (request.POST.get("action") or "").strip()
            if action == "update_organisation_name":
                org_name_form = OrganisationNameForm(request.POST, instance=default_org)
                if org_name_form.is_valid():
                    org_name_form.save()
                    messages.success(request, "Nom de l'équipe mis à jour.")
                    return redirect("rental:team_page")
                messages.error(request, "Veuillez corriger le nom de l'équipe.")
                form = InviteMemberForm(organisation=default_org)
            elif action == "remove_member":
                try:
                    member_pk = int(request.POST.get("member_id", "0"))
                except ValueError:
                    messages.error(request, "Requête invalide.")
                    return redirect("rental:team_page")
                member = OrganisationMember.objects.filter(
                    pk=member_pk,
                    organisation=default_org,
                ).first()
                if not member:
                    messages.error(request, "Membre introuvable.")
                elif member.role == OrganisationMember.ROLE_OWNER:
                    messages.error(
                        request,
                        "Impossible de retirer un propriétaire de l'organisation.",
                    )
                else:
                    label = member.user.email or member.user.username
                    member.delete()
                    messages.success(request, f"{label} a été retiré(e) de l'équipe.")
                return redirect("rental:team_page")
            elif action == "update_member_email":
                email_form = TeamMemberEmailForm(default_org, request.POST)
                if email_form.is_valid():
                    m = email_form.member
                    _sync_user_email_for_login(m.user, email_form.cleaned_data["email"])
                    messages.success(request, "Adresse email mise à jour.")
                    return redirect("rental:team_page")
                for errs in email_form.errors.values():
                    for err in errs:
                        messages.error(request, err)
                return redirect("rental:team_page")
            else:
                # action == invite (formulaire d'ajout)
                form = InviteMemberForm(default_org, request.POST)
                if form.is_valid():
                    email = form.cleaned_data["email"].strip().lower()
                    new_user = User.objects.filter(email__iexact=email).first()
                    if new_user:
                        OrganisationMember.objects.get_or_create(
                            user=new_user,
                            organisation=default_org,
                            defaults={"role": OrganisationMember.ROLE_MEMBER},
                        )
                        messages.success(request, f"{new_user.email} a été ajouté à l'équipe.")
                    else:
                        messages.error(request, "Utilisateur introuvable.")
                    return redirect("rental:team_page")
                messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
                org_name_form = OrganisationNameForm(instance=default_org)

    return render(
        request,
        "rental/team.html",
        {
            "organisation": default_org,
            "members": members,
            "is_owner": is_owner,
            "form": form,
            "org_name_form": org_name_form,
        },
    )


@login_required
def api_access_page(request: HttpRequest) -> HttpResponse:
    """Page Accès API (Premium) : afficher / générer la clé API et la documentation."""
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    api_key_obj = None
    try:
        api_key_obj = ApiKey.objects.get(user=request.user)
    except ApiKey.DoesNotExist:
        pass
    new_key_display = None
    if request.method == "POST" and request.POST.get("action") == "generate":
        _, raw_key = ApiKey.create_key_for_user(request.user)
        new_key_display = raw_key
        messages.success(request, "Une nouvelle clé API a été générée. Copiez-la maintenant, elle ne sera plus affichée.")
    base_url = request.build_absolute_uri("/").rstrip("/")
    return render(
        request,
        "rental/api_access.html",
        {
            "api_key": api_key_obj,
            "new_key_display": new_key_display,
            "api_base_url": base_url,
        },
    )


@login_required
def mobile_app_page(request: HttpRequest) -> HttpResponse:
    """Page Application mobile (Premium) : accès SyLoc sur smartphone et installation PWA."""
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    login_url = request.build_absolute_uri(reverse("login"))
    dashboard_url = request.build_absolute_uri(reverse("dashboard"))
    return render(
        request,
        "rental/mobile_app.html",
        {"login_url": login_url, "dashboard_url": dashboard_url},
    )


def _accounting_csv_cell_text(value) -> str:
    """Texte CSV : une ligne, pas de saut ni guillemet parasite."""
    if value is None:
        return ""
    s = str(value).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return s.strip()


def _accounting_csv_cell_date(d: date | None) -> str:
    if d is None:
        return ""
    return d.isoformat()


def _accounting_csv_cell_datetime(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if tz.is_aware(dt):
        dt = tz.localtime(dt)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _accounting_csv_cell_eur(amount) -> str:
    """Montant en euros, virgule décimale, 2 décimales (usage comptable / Excel France)."""
    if amount is None:
        return ""
    q = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(q, "f").replace(".", ",")


def _accounting_csv_cell_id(pk: int | None) -> str:
    if pk is None:
        return ""
    return str(pk)


@login_required
def export_accounting_csv(request: HttpRequest) -> HttpResponse:
    """Export CSV des loyers (comptabilité) — réservé Premium.

    Format : UTF-8 avec BOM, séparateur « ; », fins de ligne Windows (CRLF),
    dates ISO AAAA-MM-JJ, montants en euros avec virgule décimale (ex. 1234,56),
    horodatage « créé le » en heure locale du serveur.
    """
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    from io import StringIO

    invoices = (
        RentInvoice.objects.filter(lease__property__in=properties_visible_to(request.user))
        .select_related("lease", "lease__property")
        .prefetch_related("lease__tenants")
        .order_by("due_date", "lease_id", "pk")
    )
    buffer = StringIO()
    buffer.write("\ufeff")  # BOM UTF-8 pour qu'Excel ouvre le fichier en UTF-8
    writer = csv.writer(
        buffer,
        delimiter=";",
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\r\n",
    )
    writer.writerow(
        [
            "id_loyer",
            "bien",
            "adresse",
            "code_postal",
            "ville",
            "locataires",
            "id_bail",
            "date_debut_bail",
            "date_fin_bail",
            "date_echeance",
            "periode_aaaa_mm",
            "montant_loyer_eur",
            "montant_charges_eur",
            "montant_total_eur",
            "statut_code",
            "statut_libelle",
            "date_paiement",
            "cree_le",
        ]
    )
    for inv in invoices:
        prop = inv.lease.property
        lease = inv.lease
        periode = ""
        if inv.due_date:
            periode = f"{inv.due_date.year:04d}-{inv.due_date.month:02d}"
        total = inv.total_amount
        writer.writerow(
            [
                _accounting_csv_cell_id(inv.pk),
                _accounting_csv_cell_text(prop.name),
                _accounting_csv_cell_text(prop.address),
                _accounting_csv_cell_text(prop.zip_code),
                _accounting_csv_cell_text(prop.city),
                _accounting_csv_cell_text(lease.tenants_display),
                _accounting_csv_cell_id(lease.pk),
                _accounting_csv_cell_date(lease.start_date),
                _accounting_csv_cell_date(lease.end_date),
                _accounting_csv_cell_date(inv.due_date),
                periode,
                _accounting_csv_cell_eur(inv.amount_rent),
                _accounting_csv_cell_eur(inv.amount_charges),
                _accounting_csv_cell_eur(total),
                inv.status,
                _accounting_csv_cell_text(inv.get_status_display()),
                _accounting_csv_cell_date(inv.paid_date),
                _accounting_csv_cell_datetime(inv.created_at),
            ]
        )
    export_ts = tz.localtime(tz.now()).strftime("%Y%m%d_%H%M")
    filename = f"syloc_encaissements_{export_ts}.csv"
    response = HttpResponse(buffer.getvalue().encode("utf-8"), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _active_properties(user):
    return properties_visible_to(user).filter(archived_at__isnull=True).select_related(
        "organisation"
    )


def _active_tenants(user):
    return tenants_visible_to(user).filter(archived_at__isnull=True)


def _active_leases(user):
    return leases_visible_to(user).filter(archived_at__isnull=True)


def _invoice_total_aggregate():
    """Agrégation pour somme loyer + charges."""
    return Sum(F("amount_rent") + F("amount_charges"))


def _meter_width_pct_css(pct: float | int | None) -> str:
    """Largeur de jauge en % pour CSS (point décimal, borné 0–100)."""
    try:
        x = float(pct)
    except (TypeError, ValueError):
        return "0"
    x = max(0.0, min(100.0, x))
    return f"{x:.1f}"


SUGGESTION_MAX_FILES = 8
SUGGESTION_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 Mo


def _dashboard_onboarding_state(user):
    """Progression 0 → premier encaissement (hors mode découverte en template)."""
    has_property = properties_visible_to(user).filter(archived_at__isnull=True).exists()
    has_tenant = tenants_visible_to(user).filter(archived_at__isnull=True).exists()
    has_lease = leases_visible_to(user).filter(
        archived_at__isnull=True,
        is_active=True,
    ).exists()
    has_paid_rent = RentInvoice.objects.filter(
        lease__property__in=properties_visible_to(user),
        status="PAID",
    ).exists()
    show_wizard = not (has_property and has_tenant and has_lease and has_paid_rent)
    if not has_property:
        current_step = 1
    elif not has_tenant:
        current_step = 2
    elif not has_lease:
        current_step = 3
    elif not has_paid_rent:
        current_step = 4
    else:
        current_step = 0
    return {
        "has_property": has_property,
        "has_tenant": has_tenant,
        "has_lease": has_lease,
        "has_paid_rent": has_paid_rent,
        "show_wizard": show_wizard,
        "current_step": current_step,
    }


def _build_dashboard_context(user):
    """Contexte commun du tableau de bord (KPI, graphiques, loyers en retard)."""
    profile = _get_profile(user)
    is_premium = profile.is_premium
    props_visible = properties_visible_to(user)
    properties = list(_active_properties(user))
    today = date.today()
    current_year = today.year
    current_month = today.month

    base_invoices = RentInvoice.objects.filter(
        lease__property__in=properties_visible_to(user),
        due_date__month=current_month,
        due_date__year=current_year,
    )
    total_rent_expected = (
        base_invoices.aggregate(s=_invoice_total_aggregate())["s"] or 0
    )
    total_rent_paid = (
        base_invoices.filter(status="PAID").aggregate(s=_invoice_total_aggregate())["s"]
        or 0
    )

    if total_rent_expected and total_rent_expected > 0:
        rent_percentage = min(
            100,
            round(float(total_rent_paid) / float(total_rent_expected) * 100, 1),
        )
    else:
        rent_percentage = 100.0 if total_rent_paid else 0.0

    cashflow = compute_global_cashflow_for_user(user)

    try:
        paid_float = float(total_rent_paid or 0)
        cashflow_float = float(cashflow if cashflow is not None else 0)
        if paid_float > 0:
            rentability_pct = round(cashflow_float / paid_float * 100, 1)
        else:
            rentability_pct = None
    except (TypeError, ValueError, ZeroDivisionError):
        rentability_pct = None

    late_rents = RentInvoice.objects.filter(
        lease__property__in=properties_visible_to(user),
        status__in=["DUE", "LATE"],
        due_date__lt=today,
    ).select_related(
        "lease", "lease__property", "lease__property__organisation"
    ).prefetch_related("lease__tenants")

    week_end = today + timedelta(days=7)
    upcoming_dues = list(
        RentInvoice.objects.filter(
            lease__property__in=properties_visible_to(user),
            status="DUE",
            due_date__gte=today,
            due_date__lte=week_end,
        )
        .select_related(
            "lease", "lease__property", "lease__property__organisation",
        )
        .prefetch_related("lease__tenants")
        .order_by("due_date")[:8]
    )

    property_annual_totals = []
    for prop in properties:
        year_total = (
            RentInvoice.objects.filter(
                lease__property=prop,
                status="PAID",
                due_date__year=current_year,
            ).aggregate(s=_invoice_total_aggregate())["s"]
            or 0
        )
        monthly = []
        for month in range(1, 13):
            total = (
                RentInvoice.objects.filter(
                    lease__property=prop,
                    status="PAID",
                    due_date__year=current_year,
                    due_date__month=month,
                ).aggregate(s=_invoice_total_aggregate())["s"]
                or 0
            )
            monthly.append(float(total))
        cumulative = []
        s = 0
        for v in monthly:
            s += v
            cumulative.append(round(s, 2))
        monthly_str = ",".join(str(round(x, 2)) for x in cumulative)
        property_annual_totals.append({
            "property": prop,
            "year_total": year_total,
            "monthly": cumulative,
            "monthly_str": monthly_str,
        })

    overdue_qs = RentInvoice.objects.filter(
        lease__property__in=props_visible,
        status__in=["DUE", "LATE"],
        due_date__lt=today,
    )
    late_total_amount = overdue_qs.aggregate(s=_invoice_total_aggregate())["s"] or 0
    oldest_due = overdue_qs.aggregate(m=Min("due_date"))["m"]
    oldest_overdue_days = (today - oldest_due).days if oldest_due else None

    tomorrow = today + timedelta(days=1)
    dues_tomorrow = list(
        RentInvoice.objects.filter(
            lease__property__in=props_visible,
            status="DUE",
            due_date=tomorrow,
        )
        .select_related("lease", "lease__property")
        .prefetch_related("lease__tenants")[:6]
    )

    in_90 = today + timedelta(days=90)
    leases_ending_soon = list(
        leases_visible_to(user)
        .filter(
            archived_at__isnull=True,
            is_active=True,
            end_date__isnull=False,
            end_date__gte=today,
            end_date__lte=in_90,
        )
        .select_related("property", "property__organisation")
        .order_by("end_date")[:8]
    )

    bank_unmatched_count = 0
    bank_last_activity = None
    bank_accounts_count = 0
    if is_premium:
        b_accounts = bank_accounts_visible_to(user)
        bank_accounts_count = b_accounts.count()
        if bank_accounts_count:
            b_tx = BankTransaction.objects.filter(account__in=b_accounts)
            bank_unmatched_count = b_tx.filter(rent_invoice__isnull=True).count()
            bank_last_activity = b_tx.aggregate(m=Max("created_at"))["m"]

    diagnostics_expiring = []
    if is_premium:
        for d in (
            PropertyDiagnostic.objects.filter(
                property__in=props_visible.filter(archived_at__isnull=True),
                expiry_date__isnull=False,
                expiry_date__gte=today,
                expiry_date__lte=in_90,
            )
            .select_related("property")
            .order_by("expiry_date")[:6]
        ):
            diagnostics_expiring.append(
                {
                    "label": d.get_diagnostic_type_display(),
                    "property_name": d.property.name,
                    "expiry_date": d.expiry_date,
                    "property_pk": d.property_id,
                }
            )
        for p in (
            props_visible.filter(archived_at__isnull=True)
            .exclude(dpe_valid_until__isnull=True)
            .filter(dpe_valid_until__gte=today, dpe_valid_until__lte=in_90)
            .order_by("dpe_valid_until")[:4]
        ):
            diagnostics_expiring.append(
                {
                    "label": f"DPE ({p.dpe_class or '?'})",
                    "property_name": p.name,
                    "expiry_date": p.dpe_valid_until,
                    "property_pk": p.pk,
                }
            )
        diagnostics_expiring.sort(key=lambda x: x["expiry_date"])

    task_items: list[dict] = []
    for inv in overdue_qs.order_by("due_date")[:5]:
        days_late = (today - inv.due_date).days
        task_items.append(
            {
                "kind": "danger",
                "title": f"Loyer en retard ({days_late} j.) — {inv.lease.property.name}",
                "subtitle": f"{inv.total_amount} € · échéance {inv.due_date:%d/%m/%Y}",
                "href": reverse("rental:rentinvoice_list") + "?status=LATE",
            }
        )
    if is_premium:
        maint_pending_new = (
            maintenance_requests_pending_landlord_action_qs(user)
            .select_related("submitted_by", "lease", "lease__property")
            .order_by("-created_at")[:6]
        )
        for mr in maint_pending_new:
            title_short = (mr.title[:58] + "…") if len(mr.title) > 60 else mr.title
            task_items.append(
                {
                    "kind": "warning",
                    "title": f"Demande locataire — {title_short}",
                    "subtitle": (
                        f"{mr.submitted_by} · {mr.lease.property.name} · "
                        f"reçue le {mr.created_at:%d/%m/%Y %H:%M}"
                    ),
                    "href": reverse("rental:maintenance_requests_list") + "?status=PENDING",
                }
            )
    for inv in dues_tomorrow:
        task_items.append(
            {
                "kind": "warning",
                "title": f"Échéance demain — {inv.lease.property.name}",
                "subtitle": f"{inv.total_amount} €",
                "href": reverse("rental:rentinvoice_list") + "?status=DUE",
            }
        )
    for lease in leases_ending_soon[:4]:
        task_items.append(
            {
                "kind": "info",
                "title": f"Fin de bail proche — {lease.property.name}",
                "subtitle": f"Fin le {lease.end_date:%d/%m/%Y}",
                "href": reverse("rental:lease_detail", args=[lease.pk]),
            }
        )
    if is_premium:
        for row in diagnostics_expiring[:3]:
            task_items.append(
                {
                    "kind": "warning",
                    "title": f"Diagnostic à échéance — {row['property_name']}",
                    "subtitle": f"{row['label']} · expire le {row['expiry_date']:%d/%m/%Y}",
                    "href": reverse("rental:diagnostics_list"),
                }
            )
        if bank_unmatched_count > 0:
            first_acc = bank_accounts_visible_to(user).first()
            task_items.append(
                {
                    "kind": "info",
                    "title": f"{bank_unmatched_count} opération(s) bancaire(s) non rapprochée(s)",
                    "subtitle": "Rapprochez-les avec vos loyers dans Banque.",
                    "href": (
                        reverse("rental:bank_transaction_list", args=[first_acc.pk])
                        if first_acc
                        else reverse("rental:bank_account_list")
                    ),
                }
            )

    onboarding = _dashboard_onboarding_state(user)

    props_active_qs = properties_visible_to(user).filter(archived_at__isnull=True)
    dashboard_active_properties_count = props_active_qs.count()
    qualifying_leases_qs = leases_visible_to(user).filter(
        archived_at__isnull=True,
        is_active=True,
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
    dashboard_active_leases_count = qualifying_leases_qs.count()

    # % encaissé / attendu (montants du mois) — base unique pour la jauge « Encaissés »
    if total_rent_expected and float(total_rent_expected) > 0:
        dashboard_encaissement_pct = min(
            100.0,
            round(
                float(total_rent_paid) / float(total_rent_expected) * 100.0,
                1,
            ),
        )
    else:
        dashboard_encaissement_pct = 0.0

    # Taux d'occupation « financier » : somme des loyers+charges des baux actifs
    # / potentiel parc (biens sans bail estimés au loyer moyen des biens loués).
    per_property_rent: dict[int, Decimal] = {}
    for lease in qualifying_leases_qs.only("property_id", "rent", "charges").iterator():
        pid = lease.property_id
        amt = Decimal(lease.rent or 0) + Decimal(lease.charges or 0)
        per_property_rent[pid] = per_property_rent.get(pid, Decimal(0)) + amt

    occupied_rent_sum = sum(per_property_rent.values(), Decimal(0))
    leased_prop_count = len(per_property_rent)
    active_prop_ids = set(props_active_qs.values_list("pk", flat=True))
    dashboard_occupancy_vacant_count = len(
        active_prop_ids - set(per_property_rent.keys())
    )

    if leased_prop_count > 0 and dashboard_occupancy_vacant_count > 0:
        avg_rent_leased = occupied_rent_sum / leased_prop_count
        denom_financial = (
            occupied_rent_sum + avg_rent_leased * dashboard_occupancy_vacant_count
        )
    elif leased_prop_count > 0:
        denom_financial = occupied_rent_sum
    else:
        denom_financial = Decimal(0)

    if denom_financial and denom_financial > 0:
        dashboard_occupancy_pct = min(
            100.0,
            round(float(occupied_rent_sum / denom_financial * 100), 1),
        )
    else:
        dashboard_occupancy_pct = 0.0

    return {
        "total_properties": len(properties),
        "total_rent_expected": total_rent_expected,
        "total_rent_paid": total_rent_paid,
        "rent_percentage": rent_percentage,
        "rentability_pct": rentability_pct,
        "cashflow": cashflow,
        "late_rents": late_rents,
        "upcoming_dues": upcoming_dues,
        "late_rents_count": late_rents.count(),
        "property_annual_totals": property_annual_totals,
        "current_year": current_year,
        "current_month": current_month,
        "onboarding": onboarding,
        "is_premium": is_premium,
        "late_total_amount": late_total_amount,
        "oldest_overdue_days": oldest_overdue_days,
        "dues_tomorrow": dues_tomorrow,
        "bank_unmatched_count": bank_unmatched_count,
        "bank_last_activity": bank_last_activity,
        "bank_accounts_count": bank_accounts_count,
        "diagnostics_expiring": diagnostics_expiring[:8],
        "task_items": task_items[:14],
        "current_month_label": f"{MONTH_NAMES_FR[current_month - 1]} {current_year}",
        "dashboard_active_properties_count": dashboard_active_properties_count,
        "dashboard_active_leases_count": dashboard_active_leases_count,
        "dashboard_occupancy_pct": dashboard_occupancy_pct,
        "dashboard_occupancy_vacant_count": dashboard_occupancy_vacant_count,
        "dashboard_encaissement_pct": dashboard_encaissement_pct,
        "dashboard_encaissement_pct_css": _meter_width_pct_css(dashboard_encaissement_pct),
        "dashboard_occupancy_pct_css": _meter_width_pct_css(dashboard_occupancy_pct),
    }


def landing_page(request: HttpRequest) -> HttpResponse:
    """Page d'accueil publique : présentation de SyLoc et offres basic / Premium."""
    return render(
        request,
        "rental/landing.html",
        {"account_deleted_notice": request.GET.get("compte_supprime") == "1"},
    )


def home(request: HttpRequest) -> HttpResponse:
    """Redirige les utilisateurs connectés vers le tableau de bord, sinon affiche la page d'accueil."""
    if request.user.is_authenticated:
        return redirect("dashboard")
    return landing_page(request)


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    user = request.user
    profile = _get_profile(user)
    if profile.plan.slug == Plan.FREE and not profile.demo_seeded:
        from .demo_seed import seed_demo_data_if_needed

        seed_demo_data_if_needed(user, profile)
    context = _build_dashboard_context(user)
    return render(request, "rental/dashboard.html", context)


@login_required
def suggestions_page(request: HttpRequest) -> HttpResponse:
    """Page dédiée : envoi de suggestions d’amélioration (UX, perf, contenu, SEO, etc.)."""
    user = request.user
    form = UserSuggestionForm(
        initial={"contact_email": (user.email or "").strip()},
    )
    return render(request, "rental/suggestions.html", {"suggestion_form": form})


@login_required
@require_POST
def suggestion_submit(request: HttpRequest) -> HttpResponse:
    """Enregistre une suggestion utilisateur et notifie le staff par email."""
    user = request.user
    form = UserSuggestionForm(request.POST)
    files = request.FILES.getlist("attachments")

    def _render_with_form(f):
        return render(request, "rental/suggestions.html", {"suggestion_form": f})

    if not form.is_valid():
        return _render_with_form(form)

    if len(files) > SUGGESTION_MAX_FILES:
        messages.error(request, f"Maximum {SUGGESTION_MAX_FILES} fichiers autorisés.")
        return _render_with_form(form)

    for f in files:
        if f.size > SUGGESTION_MAX_FILE_BYTES:
            messages.error(
                request,
                f"Le fichier « {f.name} » dépasse la taille maximale (5 Mo).",
            )
            return _render_with_form(form)

    suggestion = UserSuggestion.objects.create(
        user=user,
        contact_email=form.cleaned_data["contact_email"],
        theme=form.cleaned_data["theme"],
        message=form.cleaned_data["message"],
    )
    for f in files:
        UserSuggestionAttachment.objects.create(
            suggestion=suggestion,
            file=f,
            original_name=(getattr(f, "name", "") or "")[:255],
        )

    ok, err = send_user_suggestion_notifications(suggestion)
    if ok:
        messages.success(
            request,
            "Merci ! Votre suggestion a été transmise. Un accusé de réception vous a été envoyé par email.",
        )
    else:
        messages.warning(
            request,
            "Votre suggestion a été enregistrée mais la notification par email a échoué. "
            "Notre équipe pourra toutefois la consulter. Si le problème persiste, contactez le support.",
        )
    return redirect("suggestions")


def _property_list_querystring(
    *, show_archived: bool, search_q: str, tag_ids: list[int]
) -> str:
    pairs: list[tuple[str, str]] = []
    if show_archived:
        pairs.append(("archived", "1"))
    if search_q:
        pairs.append(("q", search_q))
    for tid in tag_ids:
        pairs.append(("tag", str(tid)))
    return urlencode(pairs)


@login_required
def property_list(request: HttpRequest) -> HttpResponse:
    show_archived = request.GET.get("archived") == "1"
    qs = properties_visible_to(request.user).select_related("organisation")
    if not show_archived:
        qs = qs.filter(archived_at__isnull=True)
    search_q = (request.GET.get("q") or "").strip()
    if search_q:
        qs = qs.filter(
            Q(name__icontains=search_q)
            | Q(city__icontains=search_q)
            | Q(address__icontains=search_q)
            | Q(zip_code__icontains=search_q)
        )

    base_qs = qs
    raw_tag = request.GET.getlist("tag")
    selected_tag_ids: list[int] = []
    for x in raw_tag:
        try:
            selected_tag_ids.append(int(x))
        except (TypeError, ValueError):
            continue
    if selected_tag_ids:
        valid = set(
            PropertyTag.objects.filter(pk__in=selected_tag_ids).values_list(
                "pk", flat=True
            )
        )
        selected_tag_ids = [tid for tid in selected_tag_ids if tid in valid]
        if selected_tag_ids:
            qs = qs.filter(tags__in=selected_tag_ids).distinct()

    available_tags = list(
        PropertyTag.objects.filter(properties__in=base_qs).distinct()
    )
    order_map = {n: i for i, n in enumerate(STANDARD_PROPERTY_TAG_NAMES)}
    available_tags.sort(key=lambda t: (order_map.get(t.name, 99), t.name))

    qs = qs.order_by("name").prefetch_related("tags").annotate(works_count=Count("works", distinct=True))

    return render(
        request,
        "rental/property_list.html",
        {
            "properties": qs,
            "show_archived": show_archived,
            "search_q": search_q,
            "available_tags": available_tags,
            "selected_tag_ids": selected_tag_ids,
            "qs_params_current": _property_list_querystring(
                show_archived=show_archived,
                search_q=search_q,
                tag_ids=selected_tag_ids,
            ),
            "qs_params_archived_on": _property_list_querystring(
                show_archived=True,
                search_q=search_q,
                tag_ids=selected_tag_ids,
            ),
            "qs_params_archived_off": _property_list_querystring(
                show_archived=False,
                search_q=search_q,
                tag_ids=selected_tag_ids,
            ),
            "qs_params_clear_search": _property_list_querystring(
                show_archived=show_archived,
                search_q="",
                tag_ids=selected_tag_ids,
            ),
            "qs_params_clear_tags": _property_list_querystring(
                show_archived=show_archived,
                search_q=search_q,
                tag_ids=[],
            ),
        },
    )


@login_required
def property_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = PropertyForm(request.POST, user=request.user)
        if form.is_valid():
            prop = form.save(commit=False)
            prop.owner = request.user
            if form.cleaned_data.get("sharing_scope") == PropertyForm.SCOPE_TEAM:
                prop.organisation = get_or_create_default_organisation(request.user)
            else:
                prop.organisation = None
            prop.save()
            form.save_m2m()
            log_user_activity(
                request,
                UserActivityLog.ACTION_CREATE,
                obj=prop,
                details="Création d’un bien",
            )
            messages.success(request, "Bien créé avec succès.")
            return redirect("rental:property_list")
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = PropertyForm(user=request.user)
    return render(request, "rental/property_form.html", {"form": form})


@login_required
def property_edit(request: HttpRequest, pk: int) -> HttpResponse:
    prop = get_object_or_404(properties_visible_to(request.user), pk=pk)
    if request.method == "POST":
        form = PropertyForm(request.POST, instance=prop, user=request.user)
        if form.is_valid():
            updated = form.save(commit=False)
            if form.cleaned_data.get("sharing_scope") == PropertyForm.SCOPE_TEAM:
                updated.organisation = get_or_create_default_organisation(request.user)
            else:
                updated.organisation = None
            updated.save()
            form.save_m2m()
            messages.success(request, "Bien mis à jour.")
            return redirect("rental:property_list")
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = PropertyForm(instance=prop, user=request.user)
    return render(request, "rental/property_form.html", {"form": form, "property": prop})


@login_required
def property_delete(request: HttpRequest, pk: int) -> HttpResponse:
    prop = get_object_or_404(properties_visible_to(request.user), pk=pk)
    if request.method == "POST":
        prop.delete()
        messages.success(request, "Bien supprimé.")
        return redirect("rental:property_list")
    return render(request, "rental/property_confirm_delete.html", {"property": prop})


@login_required
def property_works_list(request: HttpRequest, property_pk: int) -> HttpResponse:
    prop = get_object_or_404(properties_visible_to(request.user), pk=property_pk)
    works = prop.works.all()
    return render(
        request,
        "rental/property_works.html",
        {"property": prop, "works": works},
    )


@login_required
def property_work_create(request: HttpRequest, property_pk: int) -> HttpResponse:
    prop = get_object_or_404(properties_visible_to(request.user), pk=property_pk)
    if request.method == "POST":
        form = PropertyWorkForm(request.POST)
        if form.is_valid():
            work = form.save(commit=False)
            work.property = prop
            work.save()
            log_user_activity(
                request,
                UserActivityLog.ACTION_CREATE,
                obj=work,
                details=f"Travaux sur le bien « {prop.name} »",
            )
            messages.success(request, "Travaux enregistrés.")
            return redirect("rental:property_works_list", property_pk=prop.pk)
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = PropertyWorkForm()
    return render(
        request,
        "rental/property_work_form.html",
        {"property": prop, "form": form, "work": None},
    )


@login_required
def property_work_edit(request: HttpRequest, property_pk: int, pk: int) -> HttpResponse:
    prop = get_object_or_404(properties_visible_to(request.user), pk=property_pk)
    work = get_object_or_404(PropertyWork, pk=pk, property=prop)
    if request.method == "POST":
        form = PropertyWorkForm(request.POST, instance=work)
        if form.is_valid():
            form.save()
            log_user_activity(
                request,
                UserActivityLog.ACTION_UPDATE,
                obj=work,
                details=f"Mise à jour travaux sur « {prop.name} »",
            )
            messages.success(request, "Travaux mis à jour.")
            return redirect("rental:property_works_list", property_pk=prop.pk)
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = PropertyWorkForm(instance=work)
    return render(
        request,
        "rental/property_work_form.html",
        {"property": prop, "form": form, "work": work},
    )


@login_required
def property_work_delete(request: HttpRequest, property_pk: int, pk: int) -> HttpResponse:
    prop = get_object_or_404(properties_visible_to(request.user), pk=property_pk)
    work = get_object_or_404(PropertyWork, pk=pk, property=prop)
    if request.method == "POST":
        log_user_activity(
            request,
            UserActivityLog.ACTION_DELETE,
            object_repr=str(work)[:255],
            details=f"Suppression travaux sur « {prop.name} »",
        )
        work.delete()
        messages.success(request, "Entrée supprimée.")
        return redirect("rental:property_works_list", property_pk=prop.pk)
    return render(
        request,
        "rental/property_work_confirm_delete.html",
        {"property": prop, "work": work},
    )


@login_required
def tenant_list(request: HttpRequest) -> HttpResponse:
    show_archived = request.GET.get("archived") == "1"
    qs = tenants_visible_to(request.user).select_related("organisation").order_by(
        "last_name", "first_name"
    )
    if not show_archived:
        qs = qs.filter(archived_at__isnull=True)
    return render(
        request,
        "rental/tenant_list.html",
        {"tenants": qs, "show_archived": show_archived},
    )


@login_required
def tenant_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = TenantForm(request.POST, user=request.user)
        if form.is_valid():
            tenant = form.save(commit=False)
            tenant.owner = request.user
            if form.cleaned_data.get("sharing_scope") == TenantForm.SCOPE_TEAM:
                tenant.organisation = get_or_create_default_organisation(request.user)
            else:
                tenant.organisation = None
            tenant.save()
            log_user_activity(
                request,
                UserActivityLog.ACTION_CREATE,
                obj=tenant,
                details="Création d’un locataire",
            )
            messages.success(request, "Locataire créé avec succès.")
            return redirect("rental:tenant_list")
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = TenantForm(user=request.user)
    return render(request, "rental/tenant_form.html", {"form": form})


@login_required
def tenant_edit(request: HttpRequest, pk: int) -> HttpResponse:
    tenant = get_object_or_404(tenants_visible_to(request.user), pk=pk)
    if request.method == "POST":
        form = TenantForm(request.POST, instance=tenant, user=request.user)
        if form.is_valid():
            updated = form.save(commit=False)
            if form.cleaned_data.get("sharing_scope") == TenantForm.SCOPE_TEAM:
                updated.organisation = get_or_create_default_organisation(request.user)
            else:
                updated.organisation = None
            updated.save()
            messages.success(request, "Locataire mis à jour.")
            return redirect("rental:tenant_list")
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = TenantForm(instance=tenant, user=request.user)
    return render(request, "rental/tenant_form.html", {"form": form, "tenant": tenant})


@login_required
def tenant_delete(request: HttpRequest, pk: int) -> HttpResponse:
    tenant = get_object_or_404(tenants_visible_to(request.user), pk=pk)
    if request.method == "POST":
        tenant.delete()
        messages.success(request, "Locataire supprimé.")
        return redirect("rental:tenant_list")
    return render(request, "rental/tenant_confirm_delete.html", {"tenant": tenant})


RENTINVOICE_PAGE_SIZE = 25
LEASE_PAGE_SIZE = 25


@login_required
def lease_list(request: HttpRequest) -> HttpResponse:
    show_archived = request.GET.get("archived") == "1"
    qs = leases_visible_to(request.user).select_related(
        "property", "property__organisation"
    ).prefetch_related("tenants").order_by("-start_date")
    if not show_archived:
        qs = qs.filter(archived_at__isnull=True)
    paginator = Paginator(qs, LEASE_PAGE_SIZE)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)
    return render(
        request,
        "rental/lease_list.html",
        {"page_obj": page_obj, "leases": page_obj.object_list, "show_archived": show_archived},
    )


def _generate_monthly_invoices_for_lease(lease: Lease) -> None:
    """Génère les loyers pour les 12 prochains mois à partir d'aujourd'hui si manquants."""
    today = date.today()
    year = today.year
    for month in range(1, 13):
        due_date = date(year, month, min(lease.start_date.day, 28))
        if due_date < lease.start_date:
            continue
        RentInvoice.objects.get_or_create(
            lease=lease,
            due_date=due_date,
            defaults={
                "amount_rent": lease.rent,
                "amount_charges": lease.charges,
            },
        )


@login_required
def lease_detail(request: HttpRequest, pk: int) -> HttpResponse:
    lease = get_object_or_404(
        leases_visible_to(request.user)
        .prefetch_related(
            "tenants",
            Prefetch(
                "practical_infos",
                queryset=LeasePracticalInfo.objects.order_by("sort_order", "id"),
            ),
            Prefetch(
                "lease_announcements",
                queryset=LeaseAnnouncement.objects.order_by("-published_at"),
            ),
        ),
        pk=pk,
    )
    recent_invoices = list(lease.rent_invoices.order_by("-due_date")[:12])
    recent_invoices.reverse()
    inspections = lease.inspection_reports.all()
    ct_lease = ContentType.objects.get_for_model(Lease)
    signing_invitations = list(
        SigningInvitation.objects.filter(content_type=ct_lease, object_id=lease.pk)
        .select_related("tenant")
        .order_by("role", "email")
    )
    profile = _get_profile(request.user)
    maintenance_requests = list(
        lease.maintenance_requests.exclude(status__in=MaintenanceRequest.CLOSED_STATUSES)
        .select_related(
            "submitted_by",
            "lease",
            "lease__property",
            "lease__property__organisation",
        )
        .prefetch_related(
            "attachments",
            Prefetch(
                "thread_messages",
                queryset=MaintenanceThreadMessage.objects.order_by("created_at", "id"),
            ),
        )
        .annotate(thread_msg_count=Count("thread_messages"))
        .order_by("-created_at")[:40]
    )
    return render(
        request,
        "rental/lease_detail.html",
        {
            "lease": lease,
            "recent_invoices": recent_invoices,
            "inspections": inspections,
            "signing_invitations": signing_invitations,
            "is_premium": profile.is_premium,
            "maintenance_requests": maintenance_requests,
            "maintenance_status_choices": MaintenanceRequest.STATUS_CHOICES,
        },
    )


@login_required
def maintenance_requests_list(request: HttpRequest) -> HttpResponse:
    """Liste centralisée des demandes d'intervention déposées par les locataires (portail Premium)."""
    redirect_resp = _require_premium(request)
    if redirect_resp:
        return redirect_resp
    leases_qs = leases_visible_to(request.user)
    status_filter = (request.GET.get("status") or "").strip().upper()
    valid_status = {c[0] for c in MaintenanceRequest.STATUS_CHOICES}
    if status_filter and status_filter not in valid_status and status_filter != "OPEN":
        status_filter = ""

    focus_raw = (request.GET.get("focus") or "").strip()
    focus_pk = int(focus_raw) if focus_raw.isdigit() else None
    maintenance_focus_mode = False

    _maint_base = (
        MaintenanceRequest.objects.filter(lease__in=leases_qs)
        .select_related("lease", "lease__property", "lease__property__organisation", "submitted_by")
        .prefetch_related(
            "attachments",
            Prefetch(
                "thread_messages",
                queryset=MaintenanceThreadMessage.objects.order_by("created_at", "id"),
            ),
        )
    )

    if focus_pk is not None:
        qs = _maint_base.filter(pk=focus_pk)
        if qs.exists():
            qs = qs.annotate(thread_msg_count=Count("thread_messages")).order_by("-created_at", "-id")
            maintenance_focus_mode = True
        else:
            messages.warning(
                request,
                "Demande introuvable ou sans accès. Affichage de la liste complète.",
            )
            focus_pk = None

    if not maintenance_focus_mode:
        qs = _maint_base
        if status_filter == "OPEN":
            qs = qs.filter(
                status__in=[
                    MaintenanceRequest.STATUS_PENDING,
                    MaintenanceRequest.STATUS_IN_PROGRESS,
                ]
            )
        elif status_filter in valid_status:
            qs = qs.filter(status=status_filter)
        else:
            qs = qs.exclude(status__in=MaintenanceRequest.CLOSED_STATUSES)

        qs = qs.annotate(thread_msg_count=Count("thread_messages")).order_by("-created_at", "-id")

    base_all = MaintenanceRequest.objects.filter(lease__in=leases_qs)
    status_counts = {row["status"]: row["c"] for row in base_all.values("status").annotate(c=Count("id"))}
    open_count = status_counts.get(MaintenanceRequest.STATUS_PENDING, 0) + status_counts.get(
        MaintenanceRequest.STATUS_IN_PROGRESS, 0
    )
    total_count = base_all.exclude(status__in=MaintenanceRequest.CLOSED_STATUSES).count()

    portal_leases = list(
        leases_qs.filter(archived_at__isnull=True, is_active=True)
        .select_related("property", "property__organisation")
        .prefetch_related(
            "tenants",
            Prefetch(
                "practical_infos",
                queryset=LeasePracticalInfo.objects.order_by("sort_order", "id"),
            ),
            Prefetch(
                "lease_announcements",
                queryset=LeaseAnnouncement.objects.order_by("-published_at"),
            ),
        )
        .order_by("property__name", "id")
    )

    paginator = Paginator(qs, 30)
    page = paginator.get_page(request.GET.get("page") or 1)
    status_rows = [
        (val, label, status_counts.get(val, 0))
        for val, label in MaintenanceRequest.STATUS_CHOICES
    ]

    _maint_params = request.GET.copy()
    _maint_params.pop("page", None)
    maint_querystring = _maint_params.urlencode()
    _maint_params.pop("status", None)
    maint_params_without_status = _maint_params.urlencode()

    _maint_back = request.GET.copy()
    _maint_back.pop("page", None)
    _maint_back.pop("focus", None)
    maint_back_to_list_querystring = _maint_back.urlencode()

    return render(
        request,
        "rental/maintenance_requests_list.html",
        {
            "page_obj": page,
            "maintenance_requests": page.object_list,
            "maintenance_status_choices": MaintenanceRequest.STATUS_CHOICES,
            "status_filter": status_filter,
            "status_rows": status_rows,
            "open_count": open_count,
            "total_count": total_count,
            "portal_leases": portal_leases,
            "maint_querystring": maint_querystring,
            "maint_params_without_status": maint_params_without_status,
            "maintenance_focus_mode": maintenance_focus_mode,
            "maintenance_focus_pk": focus_pk if maintenance_focus_mode else None,
            "maint_back_to_list_querystring": maint_back_to_list_querystring,
        },
    )


@login_required
def lease_create(request: HttpRequest) -> HttpResponse:
    user = request.user
    initial = {}
    template_id = request.GET.get("template")
    if template_id:
        tmpl = lease_templates_visible_to(user).filter(pk=template_id).first()
        if tmpl:
            initial["lease_document"] = tmpl.content
    if request.method == "POST":
        form = LeaseForm(request.POST, request.FILES)
        form.fields["property"].queryset = _active_properties(user)
        form.fields["tenants"].queryset = _active_tenants(user)
        if form.is_valid():
            lease = form.save()
            _generate_monthly_invoices_for_lease(lease)
            log_user_activity(
                request,
                UserActivityLog.ACTION_CREATE,
                obj=lease,
                details="Création d’un bail (loyers générés)",
            )
            messages.success(request, "Bail créé avec succès, loyers générés.")
            return redirect("rental:lease_list")
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = LeaseForm(initial=initial)
        form.fields["property"].queryset = _active_properties(user)
        form.fields["tenants"].queryset = _active_tenants(user)
    lease_templates = lease_templates_visible_to(user).order_by("name")
    return render(request, "rental/lease_form.html", {"form": form, "lease_templates": lease_templates})


@login_required
def lease_edit(request: HttpRequest, pk: int) -> HttpResponse:
    user = request.user
    lease = get_object_or_404(leases_visible_to(user), pk=pk)
    if request.method == "POST":
        form = LeaseForm(request.POST, request.FILES, instance=lease)
        form.fields["property"].queryset = properties_visible_to(user)
        form.fields["tenants"].queryset = tenants_visible_to(user)
        if form.is_valid():
            form.save()
            lease.refresh_from_db()
            log_user_activity(
                request,
                UserActivityLog.ACTION_UPDATE,
                obj=lease,
                details="Modification d’un bail",
            )
            messages.success(request, "Bail mis à jour.")
            return redirect("rental:lease_detail", pk=lease.pk)
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = LeaseForm(instance=lease)
        form.fields["property"].queryset = properties_visible_to(user)
        form.fields["tenants"].queryset = tenants_visible_to(user)
    lease_templates = lease_templates_visible_to(user).order_by("name")
    return render(request, "rental/lease_form.html", {"form": form, "lease": lease, "lease_templates": lease_templates})


@login_required
def lease_delete(request: HttpRequest, pk: int) -> HttpResponse:
    lease = get_object_or_404(leases_visible_to(request.user), pk=pk)
    if request.method == "POST":
        log_user_activity(
            request,
            UserActivityLog.ACTION_DELETE,
            obj=lease,
            details="Suppression d’un bail",
        )
        lease.delete()
        messages.success(request, "Bail supprimé.")
        return redirect("rental:lease_list")
    return render(request, "rental/lease_confirm_delete.html", {"lease": lease})


def _end_of_next_month(d: date) -> date:
    """Dernier jour du mois prochain."""
    if d.month == 12:
        return date(d.year + 1, 1, 1) - timedelta(days=1)
    return date(d.year, d.month + 2, 1) - timedelta(days=1)


MONTH_NAMES_FR = (
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
)


@login_required
def rentinvoice_list(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status")
    month_param = request.GET.get("month")  # YYYY-MM pour détail payés par mois
    today = date.today()
    end_next = _end_of_next_month(today)
    base_qs = RentInvoice.objects.filter(
        lease__property__in=properties_visible_to(request.user)
    ).select_related(
        "lease", "lease__property", "lease__property__organisation"
    ).prefetch_related("lease__tenants")

    paid_months = None
    paid_month = None  # (year, month) si on affiche le détail d'un mois
    paid_month_label = None

    if status == "LATE":
        qs = base_qs.filter(due_date__lt=today, status__in=["DUE", "LATE"])
    elif status == "DUE":
        qs = base_qs.filter(
            status__in=["DUE", "LATE"],
            due_date__gte=today,
            due_date__lte=end_next,
        )
    elif status == "PAID":
        if month_param:
            parts = month_param.strip().split("-")
            if len(parts) == 2:
                try:
                    year, month = int(parts[0]), int(parts[1])
                    if 1 <= month <= 12 and year >= 2000 and year <= 2100:
                        qs = base_qs.filter(
                            status="PAID",
                            due_date__year=year,
                            due_date__month=month,
                        ).order_by("due_date")
                        paid_month = (year, month)
                        paid_month_label = f"{MONTH_NAMES_FR[month - 1]} {year}"
                    else:
                        qs = base_qs.filter(status="PAID").order_by("-due_date")
                except ValueError:
                    qs = base_qs.filter(status="PAID").order_by("-due_date")
            else:
                qs = base_qs.filter(status="PAID").order_by("-due_date")
        else:
            # Liste des mois ayant au moins un loyer payé
            paid_qs = base_qs.filter(status="PAID")
            months_agg = (
                paid_qs.values("due_date__year", "due_date__month")
                .annotate(
                    count=Count("id"),
                    total=Sum(F("amount_rent") + F("amount_charges")),
                )
                .order_by("-due_date__year", "-due_date__month")
            )
            paid_months = [
                {
                    "year": row["due_date__year"],
                    "month": row["due_date__month"],
                    "count": row["count"],
                    "total": row["total"] or 0,
                    "label": f"{MONTH_NAMES_FR[row['due_date__month'] - 1]} {row['due_date__year']}",
                    "param": f"{row['due_date__year']}-{row['due_date__month']:02d}",
                }
                for row in months_agg
            ]
            qs = []  # pas de tableau d'invoices sur la vue par mois
    else:
        first_current = today.replace(day=1)
        qs = base_qs.filter(
            due_date__gte=first_current,
            due_date__lte=end_next,
        )
    if not isinstance(qs, list):
        qs = qs.order_by("due_date")
        paginator = Paginator(qs, RENTINVOICE_PAGE_SIZE)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)
        invoices = page_obj.object_list
    else:
        page_obj = None
        invoices = qs

    # Préserver status et month dans les liens de pagination
    query_base = request.GET.copy()
    if "page" in query_base:
        query_base.pop("page")

    return render(
        request,
        "rental/rentinvoice_list.html",
        {
            "invoices": invoices,
            "page_obj": page_obj,
            "query_string": query_base.urlencode(),
            "filter_status": status or "ALL",
            "paid_months": paid_months,
            "paid_month": paid_month,
            "paid_month_label": paid_month_label,
        },
    )


@login_required
def rentinvoice_mark_paid(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    invoice = get_object_or_404(
        RentInvoice,
        pk=pk,
        lease__property__in=properties_visible_to(request.user),
    )
    invoice.status = "PAID"
    invoice.paid_date = date.today()
    invoice.save(update_fields=["status", "paid_date"])
    if not invoice.payments.exists():
        RentPayment.objects.create(
            rent_invoice=invoice,
            amount=invoice.total_amount,
            paid_at=invoice.paid_date,
            method=RentPayment.METHOD_TRANSFER,
        )
    log_user_activity(
        request,
        UserActivityLog.ACTION_UPDATE,
        obj=invoice,
        details=(
            f"Loyer marqué payé — échéance {invoice.due_date} — "
            f"bien {invoice.lease.property.name}"
        ),
    )

    # Envoi de la quittance par email aux locataires
    try:
        invoice.refresh_from_db()
        lease = invoice.lease
        tenant_count = lease.tenants.count()
        if tenant_count == 0:
            messages.warning(
                request,
                "Loyer marqué comme payé. Ce bail n'a aucun locataire rattaché : allez dans Baux > Modifier le bail et sélectionnez au moins un locataire.",
            )
        else:
            # Recharger le bail depuis la BDD pour éviter un cache M2M vide, puis récupérer les emails
            from rental.models import Lease
            fresh_lease = Lease.objects.prefetch_related("tenants").get(pk=lease.pk)
            recipient_list = []
            for t in fresh_lease.tenants.all():
                addr = (t.email or "").strip()
                if addr:
                    recipient_list.append(addr)
            pdf_bytes = build_quittance_pdf(invoice)
            filename = quittance_attachment_filename(invoice, None)
            sent = send_quittance_email(invoice, pdf_bytes, filename, recipient_list=recipient_list)
            if sent:
                messages.success(
                    request,
                    "Loyer marqué comme payé. La quittance a été envoyée par email aux locataires.",
                )
            else:
                # Diagnostic : mêmes locataires que pour recipient_list (lease.tenants)
                infos = []
                for t in lease.tenants.all():
                    email_val = (t.email or "").strip()
                    infos.append(f"{t} (email en base : « {email_val or 'vide'} »)")
                messages.warning(
                    request,
                    "Loyer marqué comme payé. Quittance non envoyée : "
                    + " ; ".join(infos)
                    + ". Renseignez l’email dans Locataires > Modifier puis enregistrez.",
                )
    except Exception:
        messages.success(
            request,
            "Loyer marqué comme payé. L'envoi de la quittance par email a échoué ; vous pouvez la télécharger et l'envoyer manuellement.",
        )

    return redirect("rental:rentinvoice_list")


@login_required
def rentinvoice_quittance_pdf(request: HttpRequest, pk: int) -> HttpResponse:
    """Génère et retourne la quittance PDF pour un loyer donné."""
    invoice = get_object_or_404(
        RentInvoice,
        pk=pk,
        lease__property__in=properties_visible_to(request.user),
    )
    try:
        pdf_bytes = build_quittance_pdf(invoice)
    except Exception:
        messages.error(
            request,
            "Impossible de générer la quittance pour le moment. Réessayez ou contactez le support.",
        )
        return redirect("rental:rentinvoice_list")
    filename = quittance_attachment_filename(invoice, None)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    cd = content_disposition_header(False, filename)
    if cd:
        response["Content-Disposition"] = cd
    return response


@login_required
def rentinvoice_quittance_tenant_pdf(
    request: HttpRequest, pk: int, tenant_pk: int
) -> HttpResponse:
    """Génère et retourne la quittance PDF pour un loyer donné, part d'un locataire (colocation)."""
    invoice = get_object_or_404(
        RentInvoice,
        pk=pk,
        lease__property__in=properties_visible_to(request.user),
    )
    tenant = get_object_or_404(
        Tenant,
        pk=tenant_pk,
        leases=invoice.lease,
    )
    try:
        pdf_bytes = build_quittance_pdf(invoice, tenant=tenant)
    except Exception:
        messages.error(
            request,
            "Impossible de générer la quittance pour le moment. Réessayez ou contactez le support.",
        )
        return redirect("rental:rentinvoice_list")
    filename = quittance_attachment_filename(invoice, tenant)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    cd = content_disposition_header(False, filename)
    if cd:
        response["Content-Disposition"] = cd
    return response


@login_required
def rentinvoice_send_quittance_per_tenant(
    request: HttpRequest, pk: int
) -> HttpResponseRedirect:
    """Envoie une quittance (part du loyer) à chaque locataire du bail (colocation)."""
    if request.method != "POST":
        return redirect("rental:rentinvoice_list")
    invoice = get_object_or_404(
        RentInvoice,
        pk=pk,
        lease__property__in=properties_visible_to(request.user),
    )
    if invoice.status != "PAID":
        messages.error(request, "Ce loyer n'est pas marqué comme payé.")
        return redirect("rental:rentinvoice_list")
    tenant_count = invoice.lease.tenants.count()
    if tenant_count < 2:
        messages.info(
            request,
            "Ce bail n'a qu'un seul locataire. Utilisez le lien PDF pour la quittance globale.",
        )
        return redirect("rental:rentinvoice_list")
    try:
        sent, with_email = send_quittance_per_tenant(invoice)
        if sent == with_email and sent > 0:
            messages.success(
                request,
                f"Quittance (part du loyer) envoyée par email aux {sent} locataire(s).",
            )
        elif sent > 0:
            messages.warning(
                request,
                f"{sent} quittance(s) envoyée(s) sur {with_email} locataire(s) avec email.",
            )
        else:
            messages.warning(
                request,
                "Aucun locataire n'a d'adresse email enregistrée : quittances non envoyées.",
            )
    except Exception:
        messages.error(
            request,
            "L'envoi des quittances a échoué. Vous pouvez télécharger les PDF par locataire et les envoyer manuellement.",
        )
    return redirect("rental:rentinvoice_list")


@login_required
def rent_revise(request: HttpRequest, lease_pk: int) -> HttpResponse:
    """
    Révision du montant du loyer : modifie uniquement le prochain loyer à venir.
    """
    lease = get_object_or_404(leases_visible_to(request.user), pk=lease_pk)
    today = date.today()
    next_invoice = (
        RentInvoice.objects.filter(lease=lease, due_date__gte=today)
        .order_by("due_date")
        .first()
    )
    if not next_invoice:
        messages.warning(
            request,
            "Aucun loyer à venir pour ce bail. Vous pouvez générer des échéances ou éditer le bail.",
        )
        return redirect("rental:lease_detail", pk=lease_pk)
    if request.method == "POST":
        form = RentRevisionForm(request.POST)
        if form.is_valid():
            next_invoice.amount_rent = form.cleaned_data["amount_rent"]
            next_invoice.amount_charges = form.cleaned_data["amount_charges"]
            next_invoice.save(update_fields=["amount_rent", "amount_charges"])
            log_user_activity(
                request,
                UserActivityLog.ACTION_UPDATE,
                obj=next_invoice,
                details=(
                    f"Révision loyer — échéance {next_invoice.due_date} — "
                    f"loyer {next_invoice.amount_rent} € / charges {next_invoice.amount_charges} €"
                ),
            )
            messages.success(
                request,
                f"Le montant du loyer du {next_invoice.due_date} a été mis à jour.",
            )
            return redirect("rental:rentinvoice_list")
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = RentRevisionForm(
            initial={
                "amount_rent": next_invoice.amount_rent,
                "amount_charges": next_invoice.amount_charges,
            }
        )
    return render(
        request,
        "rental/rent_revise.html",
        {"form": form, "lease": lease, "next_invoice": next_invoice},
    )


# --- Archivage ---


@login_required
def property_archive(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    prop = get_object_or_404(Property, pk=pk, owner=request.user)
    if request.method == "POST":
        prop.archived_at = tz.now()
        prop.save(update_fields=["archived_at"])
        messages.success(request, "Bien archivé.")
    return redirect("rental:property_list")


@login_required
def property_unarchive(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    prop = get_object_or_404(Property, pk=pk, owner=request.user)
    if request.method == "POST":
        profile = getattr(request.user, "profile", None)
        max_p = (
            max_active_properties_for_volume_segment_key(profile.volume_segment_key)
            if profile
            else None
        )
        if max_p is not None and active_property_count_for_user(request.user) >= max_p:
            messages.error(request, message_active_property_quota_reached(request.user))
        else:
            prop.archived_at = None
            prop.save(update_fields=["archived_at"])
            messages.success(request, "Bien désarchivé.")
    return redirect("rental:property_list")


@login_required
def tenant_archive(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    tenant = get_object_or_404(tenants_visible_to(request.user), pk=pk)
    if request.method == "POST":
        tenant.archived_at = tz.now()
        tenant.save(update_fields=["archived_at"])
        messages.success(request, "Locataire archivé.")
    return redirect("rental:tenant_list")


@login_required
def tenant_unarchive(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    tenant = get_object_or_404(tenants_visible_to(request.user), pk=pk)
    if request.method == "POST":
        tenant.archived_at = None
        tenant.save(update_fields=["archived_at"])
        messages.success(request, "Locataire désarchivé.")
    return redirect("rental:tenant_list")


@login_required
def lease_archive(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    lease = get_object_or_404(leases_visible_to(request.user), pk=pk)
    if request.method == "POST":
        lease.archived_at = tz.now()
        lease.save(update_fields=["archived_at"])
        messages.success(request, "Bail archivé.")
    return redirect("rental:lease_list")


@login_required
def lease_unarchive(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    lease = get_object_or_404(leases_visible_to(request.user), pk=pk)
    if request.method == "POST":
        lease.archived_at = None
        lease.save(update_fields=["archived_at"])
        messages.success(request, "Bail désarchivé.")
    return redirect("rental:lease_list")


# --- États des lieux ---


def _user_leases(request):
    return leases_visible_to(request.user).select_related(
        "property", "property__organisation"
    ).prefetch_related("tenants")


@login_required
def inspection_list(request: HttpRequest) -> HttpResponse:
    lease_id = request.GET.get("lease")
    qs = InspectionReport.objects.filter(
        lease__property__in=properties_visible_to(request.user)
    ).select_related(
        "lease", "lease__property", "lease__property__organisation"
    ).prefetch_related("lease__tenants")
    if lease_id:
        qs = qs.filter(lease_id=lease_id)
    return render(
        request,
        "rental/inspection_list.html",
        {"inspections": qs, "lease_id": lease_id},
    )


@login_required
def inspection_detail(request: HttpRequest, pk: int) -> HttpResponse:
    insp = get_object_or_404(
        InspectionReport, pk=pk, lease__property__in=properties_visible_to(request.user)
    )
    ct_insp = ContentType.objects.get_for_model(InspectionReport)
    signing_invitations = list(
        SigningInvitation.objects.filter(content_type=ct_insp, object_id=insp.pk)
        .select_related("tenant")
        .order_by("role", "email")
    )
    profile = _get_profile(request.user)
    return render(
        request,
        "rental/inspection_detail.html",
        {
            "inspection": insp,
            "signing_invitations": signing_invitations,
            "is_premium": profile.is_premium,
        },
    )


@login_required
def inspection_create(request: HttpRequest) -> HttpResponse:
    user = request.user
    initial = {}
    if request.GET.get("lease"):
        lease = get_object_or_404(
            leases_visible_to(user), pk=request.GET["lease"]
        )
        initial["lease"] = lease
    template_id = request.GET.get("template")
    if template_id:
        tmpl = inspection_templates_visible_to(user).filter(pk=template_id).first()
        if tmpl:
            initial["notes"] = tmpl.content
    if request.method == "POST":
        form = InspectionReportForm(request.POST, request.FILES)
        form.fields["lease"].queryset = _user_leases(request)
        if form.is_valid():
            form.save()
            messages.success(request, "État des lieux enregistré.")
            lease_pk = form.instance.lease_id
            return redirect("rental:lease_detail", pk=lease_pk)
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = InspectionReportForm(initial=initial)
        form.fields["lease"].queryset = _user_leases(request)
    inspection_templates = inspection_templates_visible_to(user).order_by("name")
    return render(request, "rental/inspection_form.html", {"form": form, "inspection_templates": inspection_templates})


@login_required
def inspection_edit(request: HttpRequest, pk: int) -> HttpResponse:
    insp = get_object_or_404(
        InspectionReport, pk=pk, lease__property__in=properties_visible_to(request.user)
    )
    if request.method == "POST":
        form = InspectionReportForm(request.POST, request.FILES, instance=insp)
        form.fields["lease"].queryset = _user_leases(request)
        if form.is_valid():
            form.save()
            messages.success(request, "État des lieux mis à jour.")
            return redirect("rental:inspection_detail", pk=insp.pk)
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = InspectionReportForm(instance=insp)
        form.fields["lease"].queryset = _user_leases(request)
    inspection_templates = inspection_templates_visible_to(request.user).order_by("name")
    return render(
        request,
        "rental/inspection_form.html",
        {"form": form, "inspection": insp, "inspection_templates": inspection_templates},
    )


@login_required
def inspection_delete(request: HttpRequest, pk: int) -> HttpResponse:
    insp = get_object_or_404(
        InspectionReport, pk=pk, lease__property__in=properties_visible_to(request.user)
    )
    lease_pk = insp.lease_id
    if request.method == "POST":
        insp.delete()
        messages.success(request, "État des lieux supprimé.")
        return redirect("rental:lease_detail", pk=lease_pk)
    return render(
        request,
        "rental/inspection_confirm_delete.html",
        {"inspection": insp},
    )


# --- Signature électronique (baux et états des lieux) ---


def _owner_signatory_name(user) -> str:
    """Nom affiché pour le bailleur (propriétaire du bien)."""
    if not user:
        return "Bailleur"
    name = (getattr(user, "get_full_name", None) and user.get_full_name()) or ""
    name = name.strip()
    if name:
        return name
    un = (getattr(user, "username", None) or "").strip()
    if un:
        return un
    em = (getattr(user, "email", None) or "").strip()
    if em:
        return em.split("@")[0]
    return "Bailleur"


def _auto_sign_landlord_invitation(inv: SigningInvitation, request: HttpRequest | None = None) -> bool:
    """Enregistre la signature du bailleur sans page dédiée (invitation rôle bailleur uniquement)."""
    if inv.role != SigningInvitation.ROLE_LANDLORD:
        return False
    if DocumentSignature.objects.filter(invitation=inv).exists():
        return False
    doc = inv.document
    if isinstance(doc, Lease):
        owner = doc.property.owner
    elif isinstance(doc, InspectionReport):
        owner = doc.lease.property.owner
    else:
        return False
    name = _owner_signatory_name(owner)
    ip = _get_client_ip(request) if request else None
    sig = DocumentSignature.objects.create(
        invitation=inv,
        signatory_name=name,
        ip_address=ip,
    )
    inv.signed_at = sig.signed_at
    inv.save(update_fields=["signed_at"])
    return True


def _invitation_signed(inv: SigningInvitation) -> bool:
    """Compatibilité défensive : invitation signée si signed_at est présent."""
    return bool(getattr(inv, "signed_at", None))


def _dispatch_signing_invitation_emails(invitations: list[SigningInvitation], base_url: str) -> int:
    """Envoie les emails de signature (synchrone) et retourne le nombre réellement envoyés."""
    sent_count = 0
    conn = None
    try:
        conn = get_connection(fail_silently=False)
        conn.open()
    except Exception:
        logger.exception("SMTP connection open failed for signature resend")
        return 0
    for inv in invitations:
        sign_url = f"{base_url}{reverse('sign_document', args=[inv.token])}"
        ok = send_signing_invitation_email(inv, sign_url, connection=conn)
        if ok:
            sent_count += 1
        else:
            logger.warning(
                "send_signing_invitation_email returned False (invitation=%s, email=%s)",
                inv.pk,
                inv.email,
            )
    try:
        conn.close()
    except Exception:
        pass
    return sent_count


def _create_signing_invitations_for_lease(lease, base_url: str, request=None) -> dict:
    """Crée les invitations, enregistre la signature bailleur automatiquement, envoie les emails aux locataires."""
    from django.utils import timezone as tz

    ct_lease = ContentType.objects.get_for_model(Lease)
    expires_at = tz.now() + timedelta(days=30)
    tenant_invitations: list = []
    landlord_auto_signed = False
    owner = lease.property.owner
    owner_email = (getattr(owner, "email", None) or "").strip().lower()
    if owner_email:
        inv = SigningInvitation.objects.filter(
            content_type=ct_lease,
            object_id=lease.pk,
            email__iexact=owner_email,
            role=SigningInvitation.ROLE_LANDLORD,
        ).first()
        if not inv:
            inv = SigningInvitation.objects.create(
                content_type=ct_lease,
                object_id=lease.pk,
                email=owner_email,
                role=SigningInvitation.ROLE_LANDLORD,
                expires_at=expires_at,
            )
        landlord_auto_signed = _auto_sign_landlord_invitation(inv, request)
    for tenant in lease.tenants.all():
        email = (getattr(tenant, "email", None) or "").strip().lower()
        if not email:
            continue
        inv = SigningInvitation.objects.filter(
            content_type=ct_lease,
            object_id=lease.pk,
            email__iexact=email,
            role=SigningInvitation.ROLE_TENANT,
            tenant=tenant,
        ).first()
        if not inv:
            inv = SigningInvitation.objects.create(
                content_type=ct_lease,
                object_id=lease.pk,
                email=email,
                role=SigningInvitation.ROLE_TENANT,
                tenant=tenant,
                expires_at=expires_at,
            )
        elif not _invitation_signed(inv):
            # Renvoi explicite : prolonger la validité des liens déjà créés mais non signés.
            inv.expires_at = expires_at
            inv.save(update_fields=["expires_at"])
        if not _invitation_signed(inv):
            tenant_invitations.append(inv)
    sent_count = _dispatch_signing_invitation_emails(tenant_invitations, base_url)
    return {
        "tenant_invitations": tenant_invitations,
        "landlord_auto_signed": landlord_auto_signed,
        "tenant_email_sent_count": sent_count,
    }


def _create_signing_invitations_for_inspection(inspection, base_url: str, request=None) -> dict:
    """Crée les invitations pour un état des lieux, signe le bailleur, envoie les emails aux locataires."""
    from django.utils import timezone as tz

    ct_insp = ContentType.objects.get_for_model(InspectionReport)
    expires_at = tz.now() + timedelta(days=30)
    tenant_invitations: list = []
    landlord_auto_signed = False
    lease = inspection.lease
    owner = lease.property.owner
    owner_email = (getattr(owner, "email", None) or "").strip().lower()
    if owner_email:
        inv = SigningInvitation.objects.filter(
            content_type=ct_insp,
            object_id=inspection.pk,
            email__iexact=owner_email,
            role=SigningInvitation.ROLE_LANDLORD,
        ).first()
        if not inv:
            inv = SigningInvitation.objects.create(
                content_type=ct_insp,
                object_id=inspection.pk,
                email=owner_email,
                role=SigningInvitation.ROLE_LANDLORD,
                expires_at=expires_at,
            )
        landlord_auto_signed = _auto_sign_landlord_invitation(inv, request)
    for tenant in lease.tenants.all():
        email = (getattr(tenant, "email", None) or "").strip().lower()
        if not email:
            continue
        inv = SigningInvitation.objects.filter(
            content_type=ct_insp,
            object_id=inspection.pk,
            email__iexact=email,
            role=SigningInvitation.ROLE_TENANT,
            tenant=tenant,
        ).first()
        if not inv:
            inv = SigningInvitation.objects.create(
                content_type=ct_insp,
                object_id=inspection.pk,
                email=email,
                role=SigningInvitation.ROLE_TENANT,
                tenant=tenant,
                expires_at=expires_at,
            )
        elif not _invitation_signed(inv):
            # Renvoi explicite : prolonger la validité des liens déjà créés mais non signés.
            inv.expires_at = expires_at
            inv.save(update_fields=["expires_at"])
        if not _invitation_signed(inv):
            tenant_invitations.append(inv)
    sent_count = _dispatch_signing_invitation_emails(tenant_invitations, base_url)
    return {
        "tenant_invitations": tenant_invitations,
        "landlord_auto_signed": landlord_auto_signed,
        "tenant_email_sent_count": sent_count,
    }


@login_required
def lease_request_signatures(request: HttpRequest, pk: int) -> HttpResponse:
    """Lance la demande de signatures électroniques pour un bail (Premium)."""
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    lease = get_object_or_404(leases_visible_to(request.user), pk=pk)
    if request.method != "POST":
        return redirect("rental:lease_detail", pk=pk)
    base_url = request.build_absolute_uri("/").rstrip("/")
    try:
        out = _create_signing_invitations_for_lease(lease, base_url, request)
    except Exception:
        logger.exception("lease_request_signatures failed for lease=%s", lease.pk)
        messages.error(
            request,
            "Une erreur est survenue pendant le renvoi des signatures. Réessayez dans quelques secondes.",
        )
        return redirect("rental:lease_detail", pk=pk)
    tenant_count = len(out["tenant_invitations"])
    sent_count = int(out.get("tenant_email_sent_count", tenant_count))
    landlord_signed = out["landlord_auto_signed"]
    if landlord_signed:
        messages.success(
            request,
            "Votre signature en tant que bailleur a été enregistrée automatiquement.",
        )
    if tenant_count:
        messages.success(
            request,
            f"Demande de signature traitée pour {tenant_count} locataire(s), emails envoyés: {sent_count}.",
        )
        if sent_count < tenant_count:
            messages.warning(
                request,
                "Certains emails n'ont pas pu être envoyés. Vérifiez les adresses locataires et réessayez.",
            )
    if not landlord_signed and not tenant_count:
        owner_em = (getattr(lease.property.owner, "email", None) or "").strip()
        any_tenant_em = any((t.email or "").strip() for t in lease.tenants.all())
        if not owner_em and not any_tenant_em:
            messages.warning(
                request,
                "Aucune invitation créée (vérifiez que le bailleur et les locataires ont une adresse email).",
            )
        else:
            messages.info(
                request,
                "Aucune nouvelle invitation : votre signature de bailleur est déjà enregistrée et les "
                "invitations locataires ont déjà été envoyées.",
            )
    return redirect("rental:lease_detail", pk=pk)


@login_required
def inspection_request_signatures(request: HttpRequest, pk: int) -> HttpResponse:
    """Lance la demande de signatures électroniques pour un état des lieux (Premium)."""
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    insp = get_object_or_404(
        InspectionReport, pk=pk, lease__property__in=properties_visible_to(request.user)
    )
    if request.method != "POST":
        return redirect("rental:inspection_detail", pk=pk)
    base_url = request.build_absolute_uri("/").rstrip("/")
    try:
        out = _create_signing_invitations_for_inspection(insp, base_url, request)
    except Exception:
        logger.exception("inspection_request_signatures failed for inspection=%s", insp.pk)
        messages.error(
            request,
            "Une erreur est survenue pendant le renvoi des signatures. Réessayez dans quelques secondes.",
        )
        return redirect("rental:inspection_detail", pk=pk)
    tenant_count = len(out["tenant_invitations"])
    sent_count = int(out.get("tenant_email_sent_count", tenant_count))
    landlord_signed = out["landlord_auto_signed"]
    if landlord_signed:
        messages.success(
            request,
            "Votre signature en tant que bailleur a été enregistrée automatiquement.",
        )
    if tenant_count:
        messages.success(
            request,
            f"Demande de signature traitée pour {tenant_count} locataire(s), emails envoyés: {sent_count}.",
        )
        if sent_count < tenant_count:
            messages.warning(
                request,
                "Certains emails n'ont pas pu être envoyés. Vérifiez les adresses locataires et réessayez.",
            )
    if not landlord_signed and not tenant_count:
        owner_em = (getattr(insp.lease.property.owner, "email", None) or "").strip()
        any_tenant_em = any((t.email or "").strip() for t in insp.lease.tenants.all())
        if not owner_em and not any_tenant_em:
            messages.warning(
                request,
                "Aucune invitation créée (vérifiez les adresses email du bailleur et des locataires).",
            )
        else:
            messages.info(
                request,
                "Aucune nouvelle invitation : votre signature de bailleur est déjà enregistrée et les "
                "invitations locataires ont déjà été envoyées.",
            )
    return redirect("rental:inspection_detail", pk=pk)


def sign_document(request: HttpRequest, token: str) -> HttpResponse:
    """Page de signature électronique : confirmation sans dessin (document signé par le signataire, date + symbole de validation)."""
    inv = get_object_or_404(SigningInvitation, token=token)
    if inv.is_signed:
        return render(request, "rental/sign_done.html", {"invitation": inv})
    if inv.is_expired:
        return render(request, "rental/sign_expired.html", {"invitation": inv}, status=410)

    doc = inv.document
    if hasattr(doc, "property"):
        doc_label = f"Bail – {doc.property.name} – {doc.tenants_display}"
    else:
        doc_label = f"État des lieux {doc.get_report_type_display()} – {doc.lease.property.name}"

    initial_name = ""
    if inv.tenant:
        initial_name = f"{inv.tenant.first_name} {inv.tenant.last_name}".strip()
    form = DocumentSignatureForm(initial={"signatory_name": initial_name})

    if request.method == "POST":
        form = DocumentSignatureForm(request.POST)
        if form.is_valid():
            sig = DocumentSignature(
                invitation=inv,
                signatory_name=form.cleaned_data["signatory_name"].strip(),
                ip_address=_get_client_ip(request),
            )
            sig.save()
            inv.signed_at = sig.signed_at
            inv.save(update_fields=["signed_at"])
            return render(request, "rental/sign_done.html", {"invitation": inv})

    return render(
        request,
        "rental/sign_form.html",
        {"form": form, "document_label": doc_label, "invitation": inv},
    )


def _get_client_ip(request) -> str:
    """Retourne l'adresse IP du client (pour traçabilité de la signature)."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


@login_required
def lease_attached_download(request: HttpRequest, pk: int) -> FileResponse:
    """Télécharge le PDF joint au bail (hors génération SyLoc)."""
    lease = get_object_or_404(leases_visible_to(request.user), pk=pk)
    if not lease.attached_file:
        raise Http404("Aucun fichier joint.")
    filename = os.path.basename(lease.attached_file.name) or "bail.pdf"
    response = FileResponse(lease.attached_file.open("rb"), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


@login_required
def inspection_attached_download(request: HttpRequest, pk: int) -> FileResponse:
    """Télécharge le PDF joint à l'état des lieux."""
    insp = get_object_or_404(
        InspectionReport, pk=pk, lease__property__in=properties_visible_to(request.user)
    )
    if not insp.attached_file:
        raise Http404("Aucun fichier joint.")
    filename = os.path.basename(insp.attached_file.name) or "etat_des_lieux.pdf"
    response = FileResponse(insp.attached_file.open("rb"), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


@login_required
def lease_signed_pdf(request: HttpRequest, pk: int) -> HttpResponse:
    """Télécharge le PDF du bail (avec signatures si présentes). Réservé Premium."""
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    lease = get_object_or_404(leases_visible_to(request.user), pk=pk)
    try:
        pdf_bytes = build_lease_pdf(lease)
    except Exception as e:
        messages.error(request, f"Impossible de générer le PDF : {e}")
        return redirect("rental:lease_detail", pk=pk)
    filename = lease_attachment_filename(lease)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    cd = content_disposition_header(True, filename)
    if cd:
        response["Content-Disposition"] = cd
    return response


@login_required
def inspection_signed_pdf(request: HttpRequest, pk: int) -> HttpResponse:
    """Télécharge le PDF de l'état des lieux (avec signatures si présentes). Réservé Premium."""
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    insp = get_object_or_404(
        InspectionReport, pk=pk, lease__property__in=properties_visible_to(request.user)
    )
    try:
        pdf_bytes = build_inspection_pdf(insp)
    except Exception as e:
        messages.error(request, f"Impossible de générer le PDF : {e}")
        return redirect("rental:inspection_detail", pk=pk)
    filename = inspection_attachment_filename(insp)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    cd = content_disposition_header(True, filename)
    if cd:
        response["Content-Disposition"] = cd
    return response


# --- Synchronisation bancaire (Premium) ---


def _parse_bank_csv(file) -> list[dict]:
    """
    Parse un CSV de relevé bancaire. Retourne une liste de dicts {date, amount, label}.
    Accepte : Date (jj/mm/aaaa ou aaaa-mm-jj), Montant OU Débit+Crédit, Libellé.
    """
    import csv
    from decimal import Decimal, InvalidOperation

    decoded = file.read().decode("utf-8-sig", errors="replace").strip()
    dialect = csv.Sniffer().sniff(decoded[:1024], delimiters=";,\t")
    reader = csv.reader(decoded.splitlines(), dialect=dialect)
    rows = list(reader)
    if not rows:
        return []
    header = [h.strip().lower().replace(" ", "_") for h in rows[0]]
    data_rows = rows[1:]
    # Trouver les indices
    date_idx = None
    amount_idx = None
    debit_idx = None
    credit_idx = None
    label_idx = None
    for i, col in enumerate(header):
        if "date" in col:
            date_idx = i
        elif "montant" in col or "amount" in col:
            amount_idx = i
        elif "débit" in col or "debit" in col:
            debit_idx = i
        elif "crédit" in col or "credit" in col:
            credit_idx = i
        elif "libellé" in col or "libelle" in col or "description" in col or "label" in col:
            label_idx = i
    if date_idx is None:
        return []
    if amount_idx is None and (debit_idx is None or credit_idx is None):
        return []

    result = []
    for row in data_rows:
        if len(row) <= max(date_idx, amount_idx or 0, debit_idx or 0, credit_idx or 0, label_idx or 0):
            continue
        raw_date = row[date_idx].strip()
        if not raw_date:
            continue
        # Parse date
        from datetime import datetime
        try:
            for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
                try:
                    d = datetime.strptime(raw_date, fmt).date()
                    break
                except ValueError:
                    continue
            else:
                continue
        except Exception:
            continue
        # Montant
        if amount_idx is not None:
            try:
                amt = Decimal(row[amount_idx].replace(",", ".").replace(" ", ""))
            except (InvalidOperation, IndexError):
                continue
        else:
            try:
                deb = Decimal(row[debit_idx].replace(",", ".").replace(" ", "")) if row[debit_idx].strip() else Decimal(0)
                cred = Decimal(row[credit_idx].replace(",", ".").replace(" ", "")) if row[credit_idx].strip() else Decimal(0)
                amt = cred - deb
            except (InvalidOperation, IndexError):
                continue
        label = row[label_idx].strip()[:500] if label_idx is not None and label_idx < len(row) else ""
        result.append({"date": d, "amount": amt, "label": label})
    return result


@login_required
def bank_account_list(request: HttpRequest) -> HttpResponse:
    """Liste des comptes bancaires (Premium)."""
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    accounts = bank_accounts_visible_to(request.user)
    return render(request, "rental/bank_account_list.html", {"accounts": accounts})


@login_required
def bank_account_create(request: HttpRequest) -> HttpResponse:
    """Création d'un compte bancaire (Premium)."""
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    if request.method == "POST":
        form = BankAccountForm(request.POST)
        if form.is_valid():
            acc = form.save(commit=False)
            acc.owner = request.user
            acc.organisation = get_or_create_default_organisation(request.user)
            acc.save()
            messages.success(request, "Compte bancaire créé.")
            return redirect("rental:bank_transaction_list", account_pk=acc.pk)
        messages.error(request, "Veuillez corriger les erreurs.")
    else:
        form = BankAccountForm()
    return render(request, "rental/bank_account_form.html", {"form": form})


@login_required
def bank_account_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Suppression d'un compte bancaire (Premium)."""
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    account = get_object_or_404(bank_accounts_visible_to(request.user), pk=pk)
    if request.method == "POST":
        account.delete()
        messages.success(request, "Compte bancaire supprimé.")
        return redirect("rental:bank_account_list")
    return render(request, "rental/bank_account_confirm_delete.html", {"account": account})


@login_required
def bank_transaction_list(request: HttpRequest, account_pk: int) -> HttpResponse:
    """Liste des opérations d'un compte + import CSV + rapprochement (Premium)."""
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    account = get_object_or_404(bank_accounts_visible_to(request.user), pk=account_pk)
    show_unmatched = request.GET.get("unmatched") == "1"
    qs = account.transactions.all().select_related("rent_invoice", "rent_invoice__lease", "rent_invoice__lease__property")
    if show_unmatched:
        qs = qs.filter(rent_invoice__isnull=True)
    qs = qs[:200]  # limite
    # Loyers non payés (pour le formulaire de rapprochement)
    unpaid_invoices = (
        RentInvoice.objects.filter(
            lease__property__in=properties_visible_to(request.user),
            status__in=["DUE", "LATE"],
        )
        .select_related("lease", "lease__property")
        .order_by("-due_date")[:100]
    )
    upload_form = BankStatementUploadForm()
    return render(
        request,
        "rental/bank_transaction_list.html",
        {
            "account": account,
            "transactions": qs,
            "show_unmatched": show_unmatched,
            "unpaid_invoices": unpaid_invoices,
            "upload_form": upload_form,
        },
    )


@login_required
def bank_import_csv(request: HttpRequest, account_pk: int) -> HttpResponse:
    """Import d'un fichier CSV de relevé bancaire (Premium)."""
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    account = get_object_or_404(bank_accounts_visible_to(request.user), pk=account_pk)
    if request.method != "POST":
        return redirect("rental:bank_transaction_list", account_pk=account.pk)
    form = BankStatementUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, form.errors.get("file", ["Fichier invalide."])[0])
        return redirect("rental:bank_transaction_list", account_pk=account.pk)
    try:
        rows = _parse_bank_csv(request.FILES["file"])
    except Exception as e:
        messages.error(request, f"Erreur lors de la lecture du fichier : {e}")
        return redirect("rental:bank_transaction_list", account_pk=account.pk)
    if not rows:
        messages.warning(request, "Aucune ligne valide trouvée dans le CSV (vérifiez le format : Date, Montant ou Débit/Crédit, Libellé).")
        return redirect("rental:bank_transaction_list", account_pk=account.pk)
    for r in rows:
        BankTransaction.objects.create(
            account=account,
            date=r["date"],
            amount=r["amount"],
            label=r["label"],
        )
    messages.success(request, f"{len(rows)} opération(s) importée(s).")
    return redirect("rental:bank_transaction_list", account_pk=account.pk)


@login_required
def bank_match_transaction(request: HttpRequest, transaction_pk: int) -> HttpResponse:
    """Rapproche une opération bancaire avec un loyer (Premium). Optionnellement marque le loyer comme payé."""
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    transaction = get_object_or_404(
        BankTransaction.objects.filter(account__in=bank_accounts_visible_to(request.user)),
        pk=transaction_pk,
    )
    if request.method != "POST":
        return redirect("rental:bank_transaction_list", account_pk=transaction.account_id)
    invoice_id = request.POST.get("rent_invoice_id")
    if not invoice_id:
        messages.error(request, "Veuillez sélectionner un loyer.")
        return redirect("rental:bank_transaction_list", account_pk=transaction.account_id)
    invoice = RentInvoice.objects.filter(
        lease__property__in=properties_visible_to(request.user),
        pk=invoice_id,
    ).first()
    if not invoice:
        messages.error(request, "Loyer introuvable.")
        return redirect("rental:bank_transaction_list", account_pk=transaction.account_id)
    transaction.rent_invoice = invoice
    transaction.save(update_fields=["rent_invoice_id"])
    if request.POST.get("mark_paid") == "1":
        invoice.status = "PAID"
        invoice.paid_date = transaction.date
        invoice.save(update_fields=["status", "paid_date"])
        messages.success(request, "Opération rapprochée et loyer marqué comme payé.")
    else:
        messages.success(request, "Opération rapprochée.")
    return redirect("rental:bank_transaction_list", account_pk=transaction.account_id)


@login_required
def bank_unmatch_transaction(request: HttpRequest, transaction_pk: int) -> HttpResponse:
    """Annule le rapprochement d'une opération (Premium)."""
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    transaction = get_object_or_404(
        BankTransaction.objects.filter(account__in=bank_accounts_visible_to(request.user)),
        pk=transaction_pk,
    )
    if request.method == "POST":
        if transaction.rent_invoice_id:
            transaction.rent_invoice = None
            transaction.save(update_fields=["rent_invoice_id"])
            messages.success(request, "Rapprochement annulé.")
    return redirect("rental:bank_transaction_list", account_pk=transaction.account_id)


@login_required
def rentability_ai_page(request: HttpRequest) -> HttpResponse:
    """Page IA analyse rentabilité : indicateurs + analyse par bien (Premium)."""
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    properties = properties_visible_to(request.user).filter(archived_at__isnull=True).order_by("name")
    results = []
    for prop in properties:
        metrics = get_property_metrics(prop)
        analysis = get_rentability_analysis(prop, metrics)
        results.append({"property": prop, "metrics": metrics, "analysis": analysis})
    ai_configured = bool(getattr(settings, "OPENAI_API_KEY", "").strip())
    return render(
        request,
        "rental/rentability_ai.html",
        {"results": results, "ai_configured": ai_configured},
    )


# --- Modèles de bail ---


@login_required
def lease_template_list(request: HttpRequest) -> HttpResponse:
    qs = lease_templates_visible_to(request.user).order_by("name")
    return render(
        request,
        "rental/lease_template_list.html",
        {"templates": qs},
    )


@login_required
def lease_template_create(request: HttpRequest) -> HttpResponse:
    initial = {}
    if request.GET.get("from_default") == "1":
        initial["content"] = get_default_lease_content()
        initial["name"] = "Bail type (générique)"
    if request.method == "POST":
        form = LeaseTemplateForm(request.POST)
        if form.is_valid():
            tmpl = form.save(commit=False)
            tmpl.owner = request.user
            tmpl.organisation = get_or_create_default_organisation(request.user)
            tmpl.save()
            messages.success(request, "Modèle de bail créé.")
            return redirect("rental:lease_template_list")
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = LeaseTemplateForm(initial=initial)
    return render(request, "rental/lease_template_form.html", {"form": form})


@login_required
def lease_template_edit(request: HttpRequest, pk: int) -> HttpResponse:
    tmpl = get_object_or_404(lease_templates_visible_to(request.user), pk=pk)
    if request.method == "POST":
        form = LeaseTemplateForm(request.POST, instance=tmpl)
        if form.is_valid():
            form.save()
            messages.success(request, "Modèle de bail mis à jour.")
            return redirect("rental:lease_template_list")
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = LeaseTemplateForm(instance=tmpl)
    return render(request, "rental/lease_template_form.html", {"form": form, "template": tmpl})


@login_required
def lease_template_delete(request: HttpRequest, pk: int) -> HttpResponse:
    tmpl = get_object_or_404(lease_templates_visible_to(request.user), pk=pk)
    if request.method == "POST":
        tmpl.delete()
        messages.success(request, "Modèle de bail supprimé.")
        return redirect("rental:lease_template_list")
    return render(request, "rental/lease_template_confirm_delete.html", {"template": tmpl})


# --- Modèles d'état des lieux ---


@login_required
def inspection_template_list(request: HttpRequest) -> HttpResponse:
    qs = inspection_templates_visible_to(request.user).order_by("name")
    return render(
        request,
        "rental/inspection_template_list.html",
        {"templates": qs},
    )


@login_required
def inspection_template_create(request: HttpRequest) -> HttpResponse:
    initial = {}
    if request.GET.get("from_default") == "1":
        initial["content"] = get_default_inspection_content()
        initial["name"] = "État des lieux type (générique)"
    if request.method == "POST":
        form = InspectionTemplateForm(request.POST)
        if form.is_valid():
            tmpl = form.save(commit=False)
            tmpl.owner = request.user
            tmpl.organisation = get_or_create_default_organisation(request.user)
            tmpl.save()
            messages.success(request, "Modèle d'état des lieux créé.")
            return redirect("rental:inspection_template_list")
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = InspectionTemplateForm(initial=initial)
    return render(request, "rental/inspection_template_form.html", {"form": form})


@login_required
def inspection_template_edit(request: HttpRequest, pk: int) -> HttpResponse:
    tmpl = get_object_or_404(inspection_templates_visible_to(request.user), pk=pk)
    if request.method == "POST":
        form = InspectionTemplateForm(request.POST, instance=tmpl)
        if form.is_valid():
            form.save()
            messages.success(request, "Modèle d'état des lieux mis à jour.")
            return redirect("rental:inspection_template_list")
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = InspectionTemplateForm(instance=tmpl)
    return render(request, "rental/inspection_template_form.html", {"form": form, "template": tmpl})


@login_required
def inspection_template_delete(request: HttpRequest, pk: int) -> HttpResponse:
    tmpl = get_object_or_404(inspection_templates_visible_to(request.user), pk=pk)
    if request.method == "POST":
        tmpl.delete()
        messages.success(request, "Modèle d'état des lieux supprimé.")
        return redirect("rental:inspection_template_list")
    return render(request, "rental/inspection_template_confirm_delete.html", {"template": tmpl})


@login_required
@require_POST
def lease_portal_practical_add(request: HttpRequest, lease_pk: int) -> HttpResponseRedirect:
    lease = get_object_or_404(leases_visible_to(request.user), pk=lease_pk)
    if not _get_profile(request.user).is_premium:
        messages.info(request, "Réservé aux abonnés Premium.")
        return redirect("subscription")
    title = (request.POST.get("title") or "").strip()
    body = (request.POST.get("body") or "").strip()
    try:
        sort_order = int(request.POST.get("sort_order") or "0")
    except ValueError:
        sort_order = 0
    if not title or not body:
        messages.error(request, "Titre et texte obligatoires pour l'info pratique.")
        return _portal_lease_content_redirect(request, lease_pk)
    LeasePracticalInfo.objects.create(
        lease=lease, title=title[:200], body=body, sort_order=max(0, min(sort_order, 32000))
    )
    messages.success(request, "Info pratique ajoutée (visible sur le portail locataire).")
    return _portal_lease_content_redirect(request, lease_pk)


@login_required
@require_POST
def lease_portal_practical_delete(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    info = get_object_or_404(
        LeasePracticalInfo.objects.filter(lease__in=leases_visible_to(request.user)),
        pk=pk,
    )
    lease_pk = info.lease_id
    info.delete()
    messages.success(request, "Info pratique supprimée.")
    return _portal_lease_content_redirect(request, lease_pk)


@login_required
@require_POST
def lease_portal_announcement_add(request: HttpRequest, lease_pk: int) -> HttpResponseRedirect:
    lease = get_object_or_404(leases_visible_to(request.user), pk=lease_pk)
    if not _get_profile(request.user).is_premium:
        messages.info(request, "Réservé aux abonnés Premium.")
        return redirect("subscription")
    title = (request.POST.get("title") or "").strip()
    body = (request.POST.get("body") or "").strip()
    if not title or not body:
        messages.error(request, "Titre et message obligatoires pour l'annonce.")
        return _portal_lease_content_redirect(request, lease_pk)
    ann = LeaseAnnouncement(lease=lease, title=title[:200], body=body)
    expires_date_str = (request.POST.get("expires_date") or "").strip()
    if expires_date_str:
        try:
            d = date.fromisoformat(expires_date_str)
            ann.expires_at = tz.make_aware(datetime.combine(d, time(23, 59, 0)))
        except ValueError:
            pass
    ann.save()
    messages.success(request, "Annonce publiée sur le portail locataire.")
    return _portal_lease_content_redirect(request, lease_pk)


@login_required
@require_POST
def lease_portal_announcement_delete(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    ann = get_object_or_404(
        LeaseAnnouncement.objects.filter(lease__in=leases_visible_to(request.user)),
        pk=pk,
    )
    lease_pk = ann.lease_id
    ann.delete()
    messages.success(request, "Annonce supprimée.")
    return _portal_lease_content_redirect(request, lease_pk)


@login_required
@require_POST
def maintenance_request_landlord_update(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    req = get_object_or_404(
        MaintenanceRequest.objects.filter(lease__in=leases_visible_to(request.user)),
        pk=pk,
    )
    if not _get_profile(request.user).is_premium:
        messages.info(request, "Réservé aux abonnés Premium.")
        return redirect("subscription")
    if req.is_closed:
        messages.info(request, "Cette demande est terminée ou annulée ; elle n’est plus modifiable.")
        if request.POST.get("return_to") == "maintenance_list":
            return redirect(
                f"{reverse('rental:maintenance_requests_list')}?focus={req.pk}#maintenance-focus-{req.pk}"
            )
        return redirect("rental:lease_detail", pk=req.lease_id)
    status = (request.POST.get("status") or "").strip()
    valid = {c[0] for c in MaintenanceRequest.STATUS_CHOICES}
    if status in valid:
        req.status = status
    new_msg = (request.POST.get("landlord_reply") or "").strip()
    if new_msg:
        MaintenanceThreadMessage.objects.create(
            maintenance_request=req,
            sender=MaintenanceThreadMessage.SENDER_LANDLORD,
            body=new_msg,
        )
    latest_landlord = (
        MaintenanceThreadMessage.objects.filter(
            maintenance_request=req,
            sender=MaintenanceThreadMessage.SENDER_LANDLORD,
        )
        .order_by("-created_at", "-id")
        .first()
    )
    req.landlord_reply = latest_landlord.body if latest_landlord else ""
    req.save()
    messages.success(request, "Demande mise à jour.")
    if request.POST.get("return_to") == "maintenance_list":
        return redirect(
            f"{reverse('rental:maintenance_requests_list')}?focus={req.pk}#maintenance-focus-{req.pk}"
        )
    return redirect("rental:lease_detail", pk=req.lease_id)


@login_required
@require_POST
def maintenance_request_acknowledge(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    """Enregistre un accusé de réception visible par le locataire sur le portail."""
    req = get_object_or_404(
        MaintenanceRequest.objects.filter(lease__in=leases_visible_to(request.user)),
        pk=pk,
    )
    if not _get_profile(request.user).is_premium:
        messages.info(request, "Réservé aux abonnés Premium.")
        return redirect("subscription")
    if req.is_closed:
        messages.info(request, "Cette demande est terminée ou annulée ; l’accusé de réception ne s’applique plus.")
        if request.POST.get("return_to") == "maintenance_list":
            return redirect(
                f"{reverse('rental:maintenance_requests_list')}?focus={req.pk}#maintenance-focus-{req.pk}"
            )
        return redirect("rental:lease_detail", pk=req.lease_id)
    if req.acknowledged_at is None:
        req.acknowledged_at = tz.now()
        req.status = MaintenanceRequest.STATUS_IN_PROGRESS
        req.save(update_fields=["acknowledged_at", "updated_at", "status"])
        messages.success(
            request,
            "Accusé de réception enregistré : statut passé à « En cours », visible par le locataire sur le portail.",
        )
    else:
        messages.info(request, "L’accusé de réception était déjà enregistré.")
    if request.POST.get("return_to") == "maintenance_list":
        return redirect(
            f"{reverse('rental:maintenance_requests_list')}?focus={req.pk}#maintenance-focus-{req.pk}"
        )
    return redirect("rental:lease_detail", pk=req.lease_id)

