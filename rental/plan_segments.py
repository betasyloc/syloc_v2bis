"""Segments de volume (biens actifs) — positionnement tarifaire aux côtés des offres Basic / Premium."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PropertyVolumeSegment:
    """Segment « nombre de biens » affiché sur les pages d’offres / abonnement."""

    key: str
    label: str
    range_label: str
    logic: str


# Grille marketing affichée sur la page Abonnement (par palier de biens actifs).
# Les boutons Stripe utilisent les prix configurés globalement ; ces libellés reflètent le barème cible.
SUBSCRIPTION_VOLUME_TIER_ROWS: tuple[dict[str, str], ...] = (
    {
        "key": "perso",
        "row_title": "1 à 5",
        "pill": "Perso",
        "base_monthly": "7 €",
        "base_annual": "67 €",
        "base_meta_full": "84 € sans remise",
        "base_meta_save": "−17 €",
        "prem_monthly": "15 €",
        "prem_annual": "144 €",
        "prem_meta_full": "180 € sans remise",
        "prem_meta_save": "−36 €",
    },
    {
        "key": "actif",
        "row_title": "6 à 25",
        "pill": "Actif",
        "base_monthly": "15 €",
        "base_annual": "144 €",
        "base_meta_full": "180 € sans remise",
        "base_meta_save": "−36 €",
        "prem_monthly": "39 €",
        "prem_annual": "374 €",
        "prem_meta_full": "468 € sans remise",
        "prem_meta_save": "−94 €",
    },
    {
        "key": "pro",
        "row_title": "26 à 80",
        "pill": "Pro",
        "base_monthly": "35 €",
        "base_annual": "336 €",
        "base_meta_full": "420 € sans remise",
        "base_meta_save": "−84 €",
        "prem_monthly": "99 €",
        "prem_annual": "950 €",
        "prem_meta_full": "1\u00a0188\u00a0€ sans remise",
        "prem_meta_save": "−238 €",
    },
)

PROPERTY_VOLUME_SEGMENTS: tuple[PropertyVolumeSegment, ...] = (
    PropertyVolumeSegment(
        key="perso",
        label="Perso / essentiel",
        range_label="1 à 5 biens",
        logic=(
            "Particulier, un à deux biens typiquement avec une marge ; "
            "prix d’entrée et conversion."
        ),
    ),
    PropertyVolumeSegment(
        key="actif",
        label="Actif / portefeuille",
        range_label="6 à 25 biens",
        logic="Semi-professionnel, plusieurs lots ; cœur récurrent du service.",
    ),
    PropertyVolumeSegment(
        key="pro",
        label="Pro / structure",
        range_label="26 à 80 biens",
        logic="Petite gérance, SCI multiples, équipe ; volume et accompagnement réels.",
    ),
    PropertyVolumeSegment(
        key="volume",
        label="Volume",
        range_label="Plus de 80 biens",
        logic=(
            "Portefeuilles importants : tarif personnalisé et accompagnement dédié — nous contacter."
        ),
    ),
)


def property_volume_segment_for_count(count: int) -> PropertyVolumeSegment:
    """Segment correspondant au nombre de biens actifs (non archivés). 0 biens → Perso."""
    n = max(0, int(count))
    if n <= 5:
        return PROPERTY_VOLUME_SEGMENTS[0]
    if n <= 25:
        return PROPERTY_VOLUME_SEGMENTS[1]
    if n <= 80:
        return PROPERTY_VOLUME_SEGMENTS[2]
    return PROPERTY_VOLUME_SEGMENTS[3]


def active_property_count_for_user(user) -> int:
    from rental.models import properties_visible_to

    return properties_visible_to(user).filter(archived_at__isnull=True).count()


def max_active_properties_for_volume_segment_key(key: str) -> int | None:
    """Plafond de biens actifs (non archivés) par segment ; ``volume`` = illimité (``None``)."""
    k = (key or "").strip() or "perso"
    if k == "perso":
        return 5
    if k == "actif":
        return 25
    if k == "pro":
        return 80
    if k == "volume":
        return None
    return 5


def volume_segment_label_for_key(key: str) -> str:
    k = (key or "").strip() or "perso"
    for seg in PROPERTY_VOLUME_SEGMENTS:
        if seg.key == k:
            return seg.label
    return PROPERTY_VOLUME_SEGMENTS[0].label


def user_at_active_property_quota(user) -> bool:
    """True si l’utilisateur ne peut plus activer un bien de plus (création ou désarchivage)."""
    if not getattr(user, "is_authenticated", False):
        return False
    profile = getattr(user, "profile", None)
    if not profile:
        return False
    max_p = max_active_properties_for_volume_segment_key(profile.volume_segment_key)
    if max_p is None:
        return False
    return active_property_count_for_user(user) >= max_p


def message_active_property_quota_reached(user) -> str:
    if not getattr(user, "is_authenticated", False):
        return ""
    profile = getattr(user, "profile", None)
    if not profile:
        return ""
    max_p = max_active_properties_for_volume_segment_key(profile.volume_segment_key)
    if max_p is None:
        return ""
    label = volume_segment_label_for_key(profile.volume_segment_key)
    return (
        f"Vous avez atteint la limite de {max_p} bien(s) actif(s) pour le segment « {label} ». "
        "Archivez un bien, passez à un segment supérieur (page Abonnement) ou contactez-nous."
    )
