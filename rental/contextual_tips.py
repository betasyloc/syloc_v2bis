"""Astuces courtes contextuelles selon la page et les données utilisateur."""
from __future__ import annotations

from django.db.models import Exists, OuterRef, Q
from django.urls import NoReverseMatch, reverse

from .discovery import user_has_discovery_readonly
from .models import (
    InspectionReport,
    Lease,
    RentInvoice,
    leases_visible_to,
    properties_visible_to,
    tenants_visible_to,
)

MAX_TIPS = 3

_ACTIVE_LEASE = {
    "is_active": True,
    "archived_at__isnull": True,
}


def _active_properties_qs(user):
    return properties_visible_to(user).filter(archived_at__isnull=True)


def _active_lease_exists():
    return Lease.objects.filter(property=OuterRef("pk"), **_ACTIVE_LEASE)


def _safe_reverse(viewname, kwargs=None):
    try:
        return reverse(viewname, kwargs=kwargs or {})
    except NoReverseMatch:
        return None


def _tip(text, link_label=None, link_url=None):
    t = {"text": text}
    if link_url and link_label:
        t["link_url"] = link_url
        t["link_label"] = link_label
    return t


def tips_dashboard(user):
    props = _active_properties_qs(user)
    tips = []
    if not props.exists():
        url = _safe_reverse("rental:property_create")
        tips.append(
            _tip(
                "Ajoutez votre premier bien pour suivre loyers, encaissements et rentabilité.",
                "Créer un bien",
                url,
            )
        )
        return tips

    leased = props.filter(Exists(_active_lease_exists()))
    incomplete = leased.filter(
        Q(monthly_charges__isnull=True) | Q(monthly_mortgage__isnull=True)
    )
    if incomplete.exists():
        url = _safe_reverse("rental:property_list")
        tips.append(
            _tip(
                "Certains biens loués n’ont pas toutes les informations de charges ou de crédit "
                "— complétez la fiche bien pour un cashflow plus fidèle.",
                "Voir mes biens",
                url,
            )
        )

    need_dpe = leased.filter(Q(dpe_class="") | Q(dpe_class__isnull=True))
    if need_dpe.exists() and len(tips) < MAX_TIPS:
        url = _safe_reverse("rental:property_list")
        tips.append(
            _tip(
                "Pensez à renseigner le DPE (classe énergétique et validité) sur vos biens "
                "pour anticiper la réglementation.",
                "Liste des biens",
                url,
            )
        )

    return tips[:MAX_TIPS]


def tips_property_list(user):
    props = _active_properties_qs(user)
    tips = []
    if not props.exists():
        url = _safe_reverse("rental:property_create")
        tips.append(
            _tip(
                "Vous n’avez encore aucun bien : commencez par en créer un.",
                "Créer un bien",
                url,
            )
        )
        return tips

    leased = props.filter(Exists(_active_lease_exists()))
    incomplete = leased.filter(
        Q(monthly_charges__isnull=True) | Q(monthly_mortgage__isnull=True)
    )
    if incomplete.exists():
        tips.append(
            _tip(
                "Pour au moins un bien loué, les charges mensuelles ou la mensualité de crédit "
                "ne sont pas entièrement renseignées — ouvrez la fiche bien pour les compléter.",
                "Modifier un bien",
                _safe_reverse("rental:property_edit", kwargs={"pk": incomplete.first().pk}),
            )
        )
    return tips[:MAX_TIPS]


def tips_property_edit(user, pk: int):
    prop = (
        properties_visible_to(user)
        .filter(pk=pk, archived_at__isnull=True)
        .first()
    )
    if not prop:
        return []
    tips = []
    has_lease = Lease.objects.filter(property=prop, **_ACTIVE_LEASE).exists()
    if not has_lease:
        return []

    if prop.monthly_charges is None:
        tips.append(
            _tip(
                "Vous n’avez pas saisi de charges mensuelles pour ce bien (copro, assurance…). "
                "Indiquez un montant ou 0 € pour affiner le cashflow du tableau de bord.",
            )
        )
    elif prop.monthly_mortgage is None:
        tips.append(
            _tip(
                "La mensualité de crédit n’est pas renseignée : indiquez-la si le bien est financé, "
                "ou 0 € sinon.",
            )
        )
    return tips[:MAX_TIPS]


def tips_tenant_list(user):
    props = _active_properties_qs(user)
    if not props.exists():
        return []
    tenants = tenants_visible_to(user).filter(archived_at__isnull=True)
    if tenants.exists():
        return []
    url = _safe_reverse("rental:tenant_create")
    return [
        _tip(
            "Ajoutez un locataire pour pouvoir créer un bail et générer les loyers.",
            "Nouveau locataire",
            url,
        )
    ]


def tips_lease_list(user):
    props = _active_properties_qs(user)
    if not props.exists():
        return []
    leases = leases_visible_to(user).filter(archived_at__isnull=True)
    if leases.exists():
        return []
    url = _safe_reverse("rental:lease_create")
    return [
        _tip(
            "Créez un bail pour lier un locataire à un bien et faire apparaître les échéances de loyer.",
            "Nouveau bail",
            url,
        )
    ]


def tips_rentinvoice_list(user):
    active_leases = leases_visible_to(user).filter(**_ACTIVE_LEASE)
    if not active_leases.exists():
        return []
    if RentInvoice.objects.filter(lease__in=active_leases).exists():
        return []
    url = _safe_reverse("rental:lease_list")
    return [
        _tip(
            "Aucun loyer n’apparaît encore : les échéances sont créées avec vos baux actifs. "
            "Vérifiez qu’un bail est bien actif et à jour.",
            "Voir les baux",
            url,
        )
    ]


def tips_property_dashboard(user):
    props = _active_properties_qs(user)
    tips = []
    if not props.exists():
        return tips
    leased = props.filter(Exists(_active_lease_exists()))
    incomplete = leased.filter(
        Q(monthly_charges__isnull=True) | Q(monthly_mortgage__isnull=True)
    )
    if incomplete.exists():
        tips.append(
            _tip(
                "Renseignez charges et crédit sur chaque fiche bien pour des indicateurs cohérents par logement.",
                "Liste des biens",
                _safe_reverse("rental:property_list"),
            )
        )
    return tips[:MAX_TIPS]


def tips_lease_detail(user, lease_pk: int):
    lease = leases_visible_to(user).filter(pk=lease_pk).first()
    if not lease:
        return []
    if lease.tenants.exists():
        return []
    url = _safe_reverse("rental:lease_edit", kwargs={"pk": lease_pk})
    return [
        _tip(
            "Ce bail n’a pas encore de locataire associé : ajoutez-en au moins un pour les quittances et le portail.",
            "Modifier le bail",
            url,
        )
    ]


def tips_inspection_list(user):
    active_leases = leases_visible_to(user).filter(**_ACTIVE_LEASE)
    if not active_leases.exists():
        return []
    if InspectionReport.objects.filter(lease__in=active_leases).exists():
        return []
    url = _safe_reverse("rental:inspection_create")
    return [
        _tip(
            "Vous n’avez pas encore d’état des lieux enregistré : pensez à documenter l’entrée ou la sortie du locataire.",
            "Nouvel état des lieux",
            url,
        )
    ]


def get_contextual_tips_for_request(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return []
    if user_has_discovery_readonly(request.user):
        return []

    match = getattr(request, "resolver_match", None)
    if not match:
        return []

    url_name = match.url_name
    kwargs = match.kwargs or {}

    if url_name == "dashboard":
        return tips_dashboard(request.user)
    if url_name == "property_list":
        return tips_property_list(request.user)
    if url_name == "property_edit" and kwargs.get("pk"):
        return tips_property_edit(request.user, int(kwargs["pk"]))
    if url_name == "tenant_list":
        return tips_tenant_list(request.user)
    if url_name == "lease_list":
        return tips_lease_list(request.user)
    if url_name == "rentinvoice_list":
        return tips_rentinvoice_list(request.user)
    if url_name == "property_dashboard":
        return tips_property_dashboard(request.user)
    if url_name == "lease_detail" and kwargs.get("pk"):
        return tips_lease_detail(request.user, int(kwargs["pk"]))
    if url_name == "inspection_list":
        return tips_inspection_list(request.user)

    return []
