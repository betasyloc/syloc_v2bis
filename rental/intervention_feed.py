"""Fil chronologique des interventions pour les bailleurs (page Notifications)."""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Max
from django.urls import reverse

from .models import MaintenanceRequest, MaintenanceThreadMessage, leases_visible_to


def _message_times_by_request(mr_ids: list[int]) -> dict[int, list]:
    grouped: dict[int, list] = {}
    if not mr_ids:
        return grouped
    for mid, created in MaintenanceThreadMessage.objects.filter(
        maintenance_request_id__in=mr_ids,
    ).values_list("maintenance_request_id", "created_at"):
        grouped.setdefault(mid, []).append(created)
    return grouped


def _nearest_message_seconds(message_times: dict[int, list], maintenance_request_id: int, at) -> float | None:
    best = None
    for created in message_times.get(maintenance_request_id, ()):
        delta = abs((created - at).total_seconds())
        if best is None or delta < best:
            best = delta
    return best


def intervention_exchange_timeline_for_landlord(user, limit=50):
    """
    Entrées récentes : messages du fil, accusés de réception, changement de statut sans nouveau message.
    """
    lease_qs = leases_visible_to(user)
    mr_ids = list(
        MaintenanceRequest.objects.filter(lease__in=lease_qs).values_list("id", flat=True)
    )
    if not mr_ids:
        return []
    list_url = reverse("rental:maintenance_requests_list")
    entries: list[dict] = []

    msg_qs = (
        MaintenanceThreadMessage.objects.filter(maintenance_request_id__in=mr_ids)
        .select_related(
            "maintenance_request",
            "maintenance_request__lease",
            "maintenance_request__lease__property",
            "maintenance_request__submitted_by",
        )
        .order_by("-created_at", "-id")[: max(limit * 4, 80)]
    )
    for msg in msg_qs:
        mr = msg.maintenance_request
        prop = mr.lease.property
        tenant_lbl = f"{mr.submitted_by.first_name} {mr.submitted_by.last_name}".strip() or str(
            mr.submitted_by
        )
        entries.append(
            {
                "at": msg.created_at,
                "kind": "thread",
                "sender": msg.sender,
                "summary": mr.title,
                "body_preview": (msg.body[:280] + "…") if len(msg.body) > 280 else msg.body,
                "meta": f"{prop.name} · {tenant_lbl}",
                "mr_id": mr.pk,
                "href": list_url,
            }
        )

    for mr in (
        MaintenanceRequest.objects.filter(lease__in=lease_qs, acknowledged_at__isnull=False)
        .select_related("lease", "lease__property", "submitted_by")
        .order_by("-acknowledged_at")[:120]
    ):
        tenant_lbl = f"{mr.submitted_by.first_name} {mr.submitted_by.last_name}".strip() or str(
            mr.submitted_by
        )
        entries.append(
            {
                "at": mr.acknowledged_at,
                "kind": "ack",
                "sender": "system",
                "summary": mr.title,
                "body_preview": "Accusé de réception enregistré — visible par le locataire sur son portail.",
                "meta": f"{mr.lease.property.name} · {tenant_lbl}",
                "mr_id": mr.pk,
                "href": list_url,
            }
        )

    msg_times_lessor = _message_times_by_request(mr_ids)
    for mr in (
        MaintenanceRequest.objects.filter(lease__in=lease_qs)
        .select_related("lease", "lease__property", "submitted_by")
        .annotate(latest_msg_ts=Max("thread_messages__created_at"))
        .order_by("-updated_at")[:200]
    ):
        anchor = mr.latest_msg_ts or mr.created_at
        if mr.acknowledged_at and mr.acknowledged_at > anchor:
            anchor = mr.acknowledged_at
        if mr.updated_at <= anchor + timedelta(seconds=1):
            continue
        near = _nearest_message_seconds(msg_times_lessor, mr.pk, mr.updated_at)
        if near is not None and near < 2.0:
            continue
        tenant_lbl = f"{mr.submitted_by.first_name} {mr.submitted_by.last_name}".strip() or str(
            mr.submitted_by
        )
        entries.append(
            {
                "at": mr.updated_at,
                "kind": "status",
                "sender": "system",
                "summary": mr.title,
                "body_preview": f"Mise à jour de la demande — statut « {mr.get_status_display()} ».",
                "meta": f"{mr.lease.property.name} · {tenant_lbl}",
                "mr_id": mr.pk,
                "href": list_url,
            }
        )

    entries.sort(key=lambda e: e["at"], reverse=True)
    return entries[:limit]
