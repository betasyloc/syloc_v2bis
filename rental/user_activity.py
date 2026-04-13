"""
Journalisation centralisée des actions utilisateur (UserActivityLog).

À appeler depuis les vues après une opération réussie. Ne pas logger les échecs de formulaire.
"""

from __future__ import annotations

from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.http import HttpRequest

from .models import UserActivityLog


def _client_ip(request: HttpRequest | None) -> str | None:
    if not request:
        return None
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


def log_user_activity(
    request: HttpRequest | None,
    action: str,
    *,
    obj: models.Model | None = None,
    object_repr: str | None = None,
    details: str = "",
) -> UserActivityLog | None:
    """
    Enregistre une ligne d'audit pour l'utilisateur authentifié.

    :param request: requête HTTP (pour user et IP) ; si anonyme, ne rien enregistrer.
    :param action: une des valeurs UserActivityLog.ACTION_* .
    :param obj: instance liée (remplit content_type / object_id) ; laisser None pour événements sans modèle.
    :param object_repr: libellé court si ``obj`` est absent ou déjà supprimé (max 255 car.).
    :param details: texte libre (ex. contexte Stripe, montants) pour le support.
    """
    user = getattr(request, "user", None) if request else None
    if user is None or not getattr(user, "is_authenticated", False):
        return None

    ct: ContentType | None = None
    oid: int | None = None
    repr_str = (object_repr or "").strip()

    if obj is not None:
        ct = ContentType.objects.get_for_model(obj.__class__)
        pk_val: Any = getattr(obj, "pk", None)
        oid = int(pk_val) if pk_val is not None else None
        if not repr_str:
            repr_str = str(obj)[:255]
    if not repr_str:
        repr_str = "—"

    details_clean = (details or "").strip()
    if len(details_clean) > 10000:
        details_clean = details_clean[:9997] + "…"

    return UserActivityLog.objects.create(
        user=user,
        action=action,
        content_type=ct,
        object_id=oid,
        object_repr=repr_str[:255],
        details=details_clean,
        ip_address=_client_ip(request) or None,
    )
