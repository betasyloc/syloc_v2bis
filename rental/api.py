# API REST SyLoc (Premium) – authentification par clé et vues JSON.

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import ApiKey, properties_visible_to, tenants_visible_to, leases_visible_to
from .models import Property, Tenant, Lease, RentInvoice

User = get_user_model()


def _get_api_key_from_request(request):
    """Extrait la clé API de la requête (Authorization: Bearer ou X-Api-Key)."""
    auth = request.META.get("HTTP_AUTHORIZATION") or ""
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.META.get("HTTP_X_API_KEY") or request.GET.get("api_key") or ""


def get_user_from_api_request(request):
    """Retourne l'utilisateur associé à la clé API ou None."""
    raw = _get_api_key_from_request(request)
    return ApiKey.get_user_for_key(raw)


def _require_api_key(view_func):
    """Décorateur : exige une clé API valide et un compte Premium. Attache request.api_user."""
    def wrapped(request, *args, **kwargs):
        user = get_user_from_api_request(request)
        if not user or not user.is_authenticated:
            return JsonResponse({"error": "Clé API manquante ou invalide."}, status=401)
        from .models import UserProfile, Plan
        try:
            profile = UserProfile.objects.get(user=user)
        except UserProfile.DoesNotExist:
            return JsonResponse({"error": "Profil non trouvé."}, status=403)
        if not profile.is_premium:
            return JsonResponse({"error": "L'accès API est réservé aux abonnés Premium."}, status=403)
        request.api_user = user
        return view_func(request, *args, **kwargs)
    return wrapped


def _serialize_property(p):
    return {
        "id": p.pk,
        "name": p.name,
        "address": p.address or "",
        "city": p.city or "",
        "zip_code": p.zip_code or "",
        "owner_type": p.owner_type,
    }


def _serialize_tenant(t):
    return {
        "id": t.pk,
        "name": t.name or "",
        "email": t.email or "",
        "phone": t.phone or "",
    }


def _serialize_lease(l):
    return {
        "id": l.pk,
        "property_id": l.property_id,
        "start_date": str(l.start_date) if l.start_date else None,
        "end_date": str(l.end_date) if l.end_date else None,
        "monthly_rent": str(l.monthly_rent) if l.monthly_rent else None,
    }


def _serialize_rent(r):
    return {
        "id": r.pk,
        "lease_id": r.lease_id,
        "due_date": str(r.due_date) if r.due_date else None,
        "amount_rent": str(r.amount_rent) if r.amount_rent else None,
        "amount_charges": str(r.amount_charges) if r.amount_charges else None,
        "status": r.status,
    }


@require_GET
@csrf_exempt
@_require_api_key
def api_properties(request):
    """GET /api/v1/properties/ – liste des biens visibles par l'utilisateur."""
    qs = properties_visible_to(request.api_user).filter(archived_at__isnull=True).order_by("name")
    return JsonResponse({"properties": [_serialize_property(p) for p in qs]})


@require_GET
@csrf_exempt
@_require_api_key
def api_tenants(request):
    """GET /api/v1/tenants/ – liste des locataires."""
    qs = tenants_visible_to(request.api_user).filter(archived_at__isnull=True).order_by("name")
    return JsonResponse({"tenants": [_serialize_tenant(t) for t in qs]})


@require_GET
@csrf_exempt
@_require_api_key
def api_leases(request):
    """GET /api/v1/leases/ – liste des baux."""
    qs = leases_visible_to(request.api_user).filter(archived_at__isnull=True).order_by("-start_date")
    return JsonResponse({"leases": [_serialize_lease(l) for l in qs]})


@require_GET
@csrf_exempt
@_require_api_key
def api_rents(request):
    """GET /api/v1/rents/ – liste des loyers (quittances)."""
    props = properties_visible_to(request.api_user).filter(archived_at__isnull=True)
    qs = RentInvoice.objects.filter(lease__property__in=props).select_related("lease").order_by("-due_date")[:500]
    return JsonResponse({"rents": [_serialize_rent(r) for r in qs]})


@require_GET
@csrf_exempt
@_require_api_key
def api_me(request):
    """GET /api/v1/me – infos du compte (vérification de clé)."""
    u = request.api_user
    return JsonResponse({
        "username": u.get_username(),
        "email": getattr(u, "email", "") or "",
    })
