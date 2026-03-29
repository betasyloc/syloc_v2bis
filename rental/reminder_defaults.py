"""Règles de relance par défaut (J+7, J+15, J+30) pour les loyers impayés."""
from __future__ import annotations

from django.contrib.auth import get_user_model

from .models import ReminderRule, get_or_create_default_organisation

User = get_user_model()

# (jours après échéance, objet du mail, corps optionnel : vide = modèle SyLoc)
DEFAULT_REMINDER_RULES: list[tuple[int, str, str]] = [
    (
        7,
        "Relance J+7 – loyer impayé",
        "",
    ),
    (
        15,
        "Relance J+15 – loyer impayé",
        "",
    ),
    (
        30,
        "Relance J+30 – loyer impayé",
        "",
    ),
]


def ensure_default_reminder_rules(user: User) -> int:
    """
    Crée les trois règles J+7, J+15 et J+30 si elles manquent pour cet utilisateur.
    Retourne le nombre de règles créées.
    """
    org = get_or_create_default_organisation(user)
    created = 0
    for days_after_due, email_subject, email_body in DEFAULT_REMINDER_RULES:
        if ReminderRule.objects.filter(owner=user, days_after_due=days_after_due).exists():
            continue
        ReminderRule.objects.create(
            owner=user,
            organisation=org,
            days_after_due=days_after_due,
            email_subject=email_subject,
            email_body_template=email_body,
            is_active=True,
        )
        created += 1
    return created
