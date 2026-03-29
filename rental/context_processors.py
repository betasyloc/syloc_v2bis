"""Context processors pour les templates."""
from django.conf import settings
from django.urls import reverse

from .contextual_tips import get_contextual_tips_for_request
from .discovery import user_has_discovery_readonly
from .models import Plan, UserProfile, maintenance_requests_pending_landlord_action_qs

# Pages d'accès rapide Premium : afficher un lien de retour (vers Premium ou vers la page d'entrée de la section)
# Pour les sous-pages : (libellé, nom d'URL cible). Sinon retour à Premium.
PREMIUM_BACK_SECTIONS = {
    # Sous-pages → retour vers la page d'entrée de la section
    "property_edit": ("Retour au dashboard par bien", "rental:property_dashboard"),
    "property_delete": ("Retour au dashboard par bien", "rental:property_dashboard"),
    "bank_account_create": ("Retour à Banque", "rental:bank_account_list"),
    "bank_account_delete": ("Retour à Banque", "rental:bank_account_list"),
    "bank_transaction_list": ("Retour à Banque", "rental:bank_account_list"),
    "bank_import_csv": ("Retour à Banque", "rental:bank_account_list"),
    "bank_match_transaction": ("Retour à Banque", "rental:bank_account_list"),
    "bank_unmatch_transaction": ("Retour à Banque", "rental:bank_account_list"),
    "cashflow_forecast": ("Retour aux rapports", "rental:analytics_report"),
    "report_pdf": ("Retour aux rapports", "rental:analytics_report"),
    "renewal_reminders": ("Retour aux rappels", "rental:reminder_rules"),
    "reminder_rules": ("Retour au tableau de bord", "dashboard"),
    "portal_link_create": ("Retour au portail locataire", "rental:portal_tenant_list"),
    "maintenance_requests_list": ("Retour aux baux", "rental:lease_list"),
    "stored_document_create": ("Retour aux documents stockés", "rental:stored_documents"),
    "stored_document_edit": ("Retour aux documents stockés", "rental:stored_documents"),
    "stored_document_delete": ("Retour aux documents stockés", "rental:stored_documents"),
}

# Toutes les pages Premium (y compris pages d'entrée) : afficher un lien (vers section ou Premium)
PREMIUM_BACK_URL_NAMES = frozenset({
    "team_page",
    "bank_account_list",
    "bank_account_create",
    "bank_account_delete",
    "bank_transaction_list",
    "bank_import_csv",
    "bank_match_transaction",
    "bank_unmatch_transaction",
    "rentability_ai",
    "api_access",
    "mobile_app",
    "analytics_report",
    "report_pdf",
    "cashflow_forecast",
    "reminder_rules",
    "renewal_reminders",
    "letter_templates",
    "diagnostics_list",
    "stored_documents",
    "stored_document_create",
    "stored_document_edit",
    "stored_document_delete",
    "property_dashboard",
    "property_edit",
    "property_delete",
    "portal_tenant_list",
    "portal_link_create",
    "maintenance_requests_list",
    "webhooks_list",
    "calendar_ics",
    "activity_log",
    "data_export",
})


def subscription(request):
    """Ajoute is_premium, user_plan, liens Premium, discovery_readonly, tenant_maintenance_new_count."""
    if not request.user.is_authenticated:
        return {
            "is_premium": False,
            "user_plan": None,
            "show_premium_back_link": False,
            "premium_back_label": None,
            "premium_back_url": None,
            "discovery_readonly": False,
            "tenant_maintenance_new_count": 0,
        }
    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        try:
            default_plan = Plan.objects.get(slug=Plan.FREE)
        except Plan.DoesNotExist:
            default_plan = Plan.get_base()
        profile = UserProfile.objects.create(
            user=request.user,
            plan=default_plan,
        )
    url_name = getattr(
        getattr(request, "resolver_match", None),
        "url_name",
        None,
    )
    show_premium_back_link = url_name in PREMIUM_BACK_URL_NAMES
    premium_back_label = None
    premium_back_url = None
    if show_premium_back_link:
        if url_name in PREMIUM_BACK_SECTIONS:
            label, target_name = PREMIUM_BACK_SECTIONS[url_name]
            premium_back_label = label
            premium_back_url = reverse(target_name)
        else:
            premium_back_label = "Retour à Premium"
            premium_back_url = reverse("premium")

    tenant_maintenance_new_count = 0
    if profile.is_premium:
        tenant_maintenance_new_count = maintenance_requests_pending_landlord_action_qs(
            request.user
        ).count()

    return {
        "is_premium": profile.is_premium,
        "user_plan": profile.plan,
        "show_premium_back_link": show_premium_back_link,
        "premium_back_label": premium_back_label,
        "premium_back_url": premium_back_url,
        "discovery_readonly": user_has_discovery_readonly(request.user),
        "tenant_maintenance_new_count": tenant_maintenance_new_count,
    }


def static_asset_version(request):
    """Évite que le navigateur garde un vieux style.css / app.js après déploiement."""
    return {
        "STATIC_ASSET_VERSION": getattr(settings, "STATIC_ASSET_VERSION", "1"),
    }


def contextual_tips(request):
    """Astuces courtes selon la page et les données (hors mode découverte)."""
    if not request.user.is_authenticated:
        return {"contextual_tips": []}
    return {"contextual_tips": get_contextual_tips_for_request(request)}
