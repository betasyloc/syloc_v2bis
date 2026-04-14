"""Mode découverte (plan gratuit) : lecture seule côté serveur et indicateur UI."""
from __future__ import annotations

from .models import Plan, UserProfile


def user_has_discovery_readonly(user) -> bool:
    """True si l'utilisateur est en essai gratuit (hors staff) : pas de création / modification."""
    if not user.is_authenticated or getattr(user, "is_staff", False):
        return False
    try:
        return user.profile.plan.slug == Plan.FREE
    except UserProfile.DoesNotExist:
        return False


# POST autorisés en mode découverte (abonnement, déconnexion, auth publique, suggestions).
DISCOVERY_ALLOW_POST_PREFIXES = (
    "/abonnement/mode-test/",
    "/logout/",
    "/portal/quitter-mode/",
    "/premium/checkout/",
    "/premium/changer-formule/",
    "/premium/portal/",
    "/webhooks/stripe/",
    "/suggestions/soumettre/",
    "/login/",
    "/creer-compte/",
    "/mot-de-passe-oublie/",
    "/identifiant-oublie/",
    "/compte/profil/",
    "/compte/suppression/",
)


def discovery_get_blocks_write_paths(path: str) -> bool:
    """True si un GET/HEAD sur ce chemin tente d'accéder à une vue d'écriture sous /rental/."""
    if not path.startswith("/rental/"):
        return False
    needles = (
        "/add/",
        "/edit/",
        "/delete/",
        "/archive/",
        "/unarchive/",
        "/revise-rent/",
        "/mark-paid/",
        "/signatures/",
        "/import/",
        "/match/",
        "/unmatch/",
        "/portal-link/",
        "/send-quittance",
    )
    return any(n in path for n in needles)


def discovery_post_allowed(path: str, method: str) -> bool:
    """True si la requête mutante est autorisée en mode découverte."""
    if method != "POST":
        return False
    if path.startswith("/portal/") and "/quitter-mode/" not in path:
        return True
    if any(path.startswith(p) for p in DISCOVERY_ALLOW_POST_PREFIXES):
        return True
    if path.startswith("/reinitialiser/"):
        return True
    if path.startswith("/sign/"):
        return True
    return False
