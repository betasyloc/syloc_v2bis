from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from .discovery import (
    discovery_get_blocks_write_paths,
    discovery_post_allowed,
    user_has_discovery_readonly,
)


class DiscoveryReadOnlyMiddleware:
    """
    Plan « essai gratuit » : bloque les écritures (hors abonnement Stripe, déconnexion, suggestions).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not user_has_discovery_readonly(request.user):
            return self.get_response(request)

        path = request.path or "/"

        if request.method in ("GET", "HEAD") and discovery_get_blocks_write_paths(path):
            return self._refuse(request)

        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if discovery_post_allowed(path, request.method):
                return self.get_response(request)
            return self._refuse(request)

        return self.get_response(request)

    def _refuse(self, request):
        messages.warning(
            request,
            "En mode découverte, SyLoc est en lecture seule. "
            "Souscrivez à une offre dans Abonnement pour créer ou modifier vos données.",
        )
        referer = request.META.get("HTTP_REFERER")
        if referer and url_has_allowed_host_and_scheme(
            url=referer,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(referer)
        return redirect("dashboard")


class TenantPortalIsolationMiddleware:
    """
    Isole la navigation d'un locataire connecté via lien portail (jeton).
    Tant que le mode portail est actif en session, toute page hors portail
    redirige vers l'espace locataire correspondant.
    """

    SESSION_KEY = "tenant_portal_token"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = request.session.get(self.SESSION_KEY)
        if not token:
            return self.get_response(request)

        path = request.path or "/"
        portal_prefix = f"/portal/{token}/"
        allowed_prefixes = (
            portal_prefix,
            "/static/",
            "/media/",
            "/logout/",
            "/portal/quitter-mode/",
        )
        # Pages légales et suppression de compte accessibles même en mode portail.
        if (
            path.startswith("/mentions-legales")
            or path.startswith("/confidentialite")
            or path.startswith("/cookies")
            or path.startswith("/compte/suppression")
        ):
            return self.get_response(request)

        if (
            path.startswith(allowed_prefixes)
            or path == "/favicon.ico"
        ):
            return self.get_response(request)

        return redirect("tenant_portal", token=token)
