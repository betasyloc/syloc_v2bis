# Vues réservées aux abonnés Premium (rapports, rappels, documents, portail, intégrations, audit).

from __future__ import annotations

import mimetypes
import os
from calendar import monthrange
from datetime import date, timedelta
from io import BytesIO

import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Sum, F, Q, Count, Prefetch
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render

from .forms import (
    LetterTemplateForm,
    ReminderRuleForm,
    StoredDocumentForm,
    TenantPortalMaintenanceForm,
    TenantPortalMeterForm,
)
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .letter_defaults import ensure_default_letter_templates
from .reminder_defaults import ensure_default_reminder_rules
from .pdf import (
    build_lease_pdf,
    build_quittance_pdf,
    lease_attachment_filename,
    quittance_attachment_filename,
)
from .models import (
    Property,
    PropertyWork,
    Tenant,
    Lease,
    RentInvoice,
    Plan,
    UserProfile,
    Organisation,
    PropertyTag,
    PropertyDiagnostic,
    InspectionReport,
    ReminderRule,
    LetterTemplate,
    StoredDocument,
    Webhook,
    UserActivityLog,
    TenantPortalAccess,
    LeasePracticalInfo,
    LeaseAnnouncement,
    MaintenanceRequest,
    MaintenanceRequestAttachment,
    MaintenanceThreadMessage,
    MeterReading,
    maintenance_requests_needing_tenant_attention_qs,
    properties_visible_to,
    tenants_visible_to,
    leases_visible_to,
    get_or_create_default_organisation,
    letter_templates_visible_to,
    stored_documents_visible_to,
)

logger = logging.getLogger(__name__)

TENANT_PORTAL_SESSION_KEY = "tenant_portal_token"


def _tenant_portal_demandes_redirect_url(token: str, request: HttpRequest) -> str:
    """Redirection vers la section Demandes ; conserve ?status= si transmis (champ caché POST)."""
    s = (request.POST.get("maint_status_filter") or "").strip().upper()
    valid = {c[0] for c in MaintenanceRequest.STATUS_CHOICES}
    path = reverse("tenant_portal", args=[token])
    if s == "OPEN" or s in valid:
        return f"{path}?{urlencode({'status': s})}#demandes"
    return f"{path}#demandes"


def _get_profile(user):
    try:
        return user.profile
    except UserProfile.DoesNotExist:
        try:
            default_plan = Plan.objects.get(slug=Plan.FREE)
        except Plan.DoesNotExist:
            default_plan = Plan.get_base()
        return UserProfile.objects.create(user=user, plan=default_plan)


def _require_premium(request):
    if not request.user.is_authenticated:
        return redirect(settings.LOGIN_URL)
    if not _get_profile(request.user).is_premium:
        messages.info(request, "Cette fonctionnalité est réservée aux abonnés Premium.")
        return redirect("subscription")
    return None


def _resolve_portal_access(
    request: HttpRequest,
    token: str,
) -> tuple[TenantPortalAccess | None, HttpResponse | None]:
    """
    Valide le jeton portail et active l'isolation de navigation en session
    pour les visiteurs anonymes.
    """
    access = get_object_or_404(TenantPortalAccess, token=token)
    if not request.user.is_authenticated:
        request.session[TENANT_PORTAL_SESSION_KEY] = token
    if timezone.now() > access.expires_at:
        if request.session.get(TENANT_PORTAL_SESSION_KEY) == token:
            request.session.pop(TENANT_PORTAL_SESSION_KEY, None)
        return None, render(request, "rental/portal/portal_expired.html")
    return access, None


@require_http_methods(["POST"])
def tenant_portal_exit_mode(request: HttpRequest) -> HttpResponseRedirect:
    """Quitte le mode portail locataire (suppression du jeton en session)."""
    had_portal_mode = bool(request.session.get(TENANT_PORTAL_SESSION_KEY))
    request.session.pop(TENANT_PORTAL_SESSION_KEY, None)
    if had_portal_mode:
        messages.info(request, "Vous avez quitté le mode locataire.")
    return redirect("dashboard" if request.user.is_authenticated else "home")


# --- Rapports et statistiques ---


@login_required
def analytics_report(request: HttpRequest) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    user = request.user
    props = properties_visible_to(user).filter(archived_at__isnull=True)
    today = timezone.now().date()
    month_start = today.replace(day=1)

    # Chiffres du mois
    rents_month = RentInvoice.objects.filter(
        lease__property__in=props,
        due_date__gte=month_start,
        due_date__lte=today,
    ).aggregate(
        total_due=Sum(F("amount_rent") + F("amount_charges")),
        paid=Sum(F("amount_rent") + F("amount_charges"), filter=Q(status="PAID")),
    )
    total_due = rents_month["total_due"] or 0
    paid = rents_month["paid"] or 0
    late_count = RentInvoice.objects.filter(
        lease__property__in=props,
        status="LATE",
    ).count()
    # Taux d'occupation (baux actifs / biens)
    active_leases = Lease.objects.filter(
        property__in=props,
        archived_at__isnull=True,
        is_active=True,
        end_date__gte=today,
    ).count()
    occupancy = (active_leases / props.count() * 100) if props.exists() else 0

    context = {
        "total_due": total_due,
        "paid": paid,
        "late_count": late_count,
        "occupancy": round(occupancy, 1),
        "properties_count": props.count(),
        "active_leases_count": active_leases,
    }
    return render(request, "rental/reports/analytics.html", context)


@login_required
def report_pdf(request: HttpRequest) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

    user = request.user
    props = properties_visible_to(user).filter(archived_at__isnull=True)
    today = timezone.now().date()
    month_start = today.replace(day=1)
    rents = RentInvoice.objects.filter(
        lease__property__in=props,
        due_date__gte=month_start,
        due_date__lte=today,
    ).select_related("lease", "lease__property")

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles = getSampleStyleSheet()
    style_h = ParagraphStyle(name="H", parent=styles["Heading1"], fontSize=16, spaceAfter=12)
    style_n = ParagraphStyle(name="N", parent=styles["Normal"], fontSize=10, spaceAfter=6)

    story = [
        Paragraph("Rapport SyLoc – Synthèse mensuelle", style_h),
        Paragraph(f"Période : {month_start.strftime('%d/%m/%Y')} – {today.strftime('%d/%m/%Y')}", style_n),
        Spacer(1, 0.5*cm),
    ]
    total_rent = sum(float(r.amount_rent or 0) + float(r.amount_charges or 0) for r in rents)
    paid_rent = sum(float(r.amount_rent or 0) + float(r.amount_charges or 0) for r in rents if r.status == "PAID")
    story.append(Paragraph(f"Loyers dus ce mois : {total_rent:.2f} €", style_n))
    story.append(Paragraph(f"Encaissés : {paid_rent:.2f} €", style_n))
    story.append(Paragraph(f"En retard : {sum(1 for r in rents if r.status == 'LATE')} quittance(s)", style_n))
    doc.build(story)
    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="syloc-rapport.pdf"'
    return response


@login_required
def cashflow_forecast(request: HttpRequest) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    user = request.user
    props = properties_visible_to(user).filter(archived_at__isnull=True)
    today = timezone.now().date()
    current_year = today.year
    _mois = ("", "janvier", "février", "mars", "avril", "mai", "juin",
             "juillet", "août", "septembre", "octobre", "novembre", "décembre")
    forecast = []
    for month in range(1, 13):
        month_start = date(current_year, month, 1)
        _, last = monthrange(current_year, month)
        month_end = month_start.replace(day=last)
        rents = RentInvoice.objects.filter(
            lease__property__in=props,
            due_date__gte=month_start,
            due_date__lte=month_end,
        ).aggregate(tot=Sum(F("amount_rent") + F("amount_charges")))["tot"] or 0
        charges = sum(
            float(p.monthly_mortgage or 0) + float(p.monthly_charges or 0)
            for p in props
        )
        forecast.append({
            "month": month_start,
            "label": f"{_mois[month]} {current_year}",
            "rents": float(rents),
            "charges": charges,
            "net": float(rents) - charges,
        })
    return render(request, "rental/reports/cashflow_forecast.html", {
        "forecast": forecast,
        "current_year": current_year,
    })


# --- Automatisation et rappels ---


@login_required
def reminder_rules_list(request: HttpRequest) -> HttpResponse:
    """Relances automatiques : accessible à tout compte connecté (pas réservé au Premium)."""
    n_new = ensure_default_reminder_rules(request.user)
    if n_new:
        messages.success(
            request,
            f"{n_new} règle(s) de relance J+7, J+15 et J+30 ont été ajoutées. Vous pouvez les personnaliser ci-dessous.",
        )
    rules = list(ReminderRule.objects.filter(owner=request.user).order_by("days_after_due"))

    if request.method == "POST":
        forms_out: list[tuple[ReminderRule, ReminderRuleForm]] = []
        all_ok = True
        for rule in rules:
            form = ReminderRuleForm(request.POST, instance=rule, prefix=f"rule_{rule.pk}")
            forms_out.append((rule, form))
            if not form.is_valid():
                all_ok = False
        if all_ok:
            for _, form in forms_out:
                form.save()
            messages.success(request, "Règles de relance enregistrées.")
            return redirect("rental:reminder_rules")
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
        return render(request, "rental/reminders/reminder_rules.html", {"rule_forms": forms_out})

    rule_forms = [
        (rule, ReminderRuleForm(instance=rule, prefix=f"rule_{rule.pk}"))
        for rule in rules
    ]
    return render(request, "rental/reminders/reminder_rules.html", {"rule_forms": rule_forms})


@login_required
def renewal_reminders_list(request: HttpRequest) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    today = timezone.now().date()
    in_3_months = today + timedelta(days=90)
    leases = leases_visible_to(request.user).filter(
        archived_at__isnull=True,
        is_active=True,
        end_date__gte=today,
        end_date__lte=in_3_months,
    ).select_related("property", "property__organisation").order_by("end_date")
    return render(request, "rental/reminders/renewal_reminders.html", {"leases": leases})


# --- Documents et conformité ---


@login_required
def letter_templates_list(request: HttpRequest) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    n_new = ensure_default_letter_templates(request.user)
    if n_new:
        messages.success(
            request,
            f"{n_new} modèle(s) de lettre type ont été ajoutés. Vous pouvez les personnaliser ci-dessous.",
        )
    templates = letter_templates_visible_to(request.user).order_by("letter_type", "name")
    return render(request, "rental/documents/letter_templates.html", {"templates": templates})


@login_required
def letter_template_create(request: HttpRequest) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    if request.method == "POST":
        form = LetterTemplateForm(request.POST)
        if form.is_valid():
            tmpl = form.save(commit=False)
            tmpl.owner = request.user
            tmpl.organisation = get_or_create_default_organisation(request.user)
            tmpl.save()
            messages.success(request, "Modèle de lettre créé.")
            return redirect("rental:letter_templates")
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = LetterTemplateForm()
    return render(request, "rental/documents/letter_template_form.html", {"form": form})


@login_required
def letter_template_edit(request: HttpRequest, pk: int) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    tmpl = get_object_or_404(letter_templates_visible_to(request.user), pk=pk)
    if request.method == "POST":
        form = LetterTemplateForm(request.POST, instance=tmpl)
        if form.is_valid():
            form.save()
            messages.success(request, "Modèle de lettre mis à jour.")
            return redirect("rental:letter_templates")
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = LetterTemplateForm(instance=tmpl)
    return render(request, "rental/documents/letter_template_form.html", {"form": form, "template": tmpl})


@login_required
def letter_template_delete(request: HttpRequest, pk: int) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    tmpl = get_object_or_404(letter_templates_visible_to(request.user), pk=pk)
    if request.method == "POST":
        tmpl.delete()
        messages.success(request, "Modèle de lettre supprimé.")
        return redirect("rental:letter_templates")
    return render(
        request,
        "rental/documents/letter_template_confirm_delete.html",
        {"template": tmpl},
    )


@login_required
def diagnostics_list(request: HttpRequest) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    props = properties_visible_to(request.user).select_related("organisation")
    diagnostics = (
        PropertyDiagnostic.objects.filter(property__in=props)
        .select_related("property", "property__organisation")
        .order_by("-expiry_date", "-done_date")
    )
    rows: list[dict] = []
    for d in diagnostics:
        rows.append(
            {
                "property_name": d.property.name,
                "property_pk": d.property_id,
                "is_team_property": bool(d.property.organisation_id),
                "organisation_name": (
                    d.property.organisation.name if d.property.organisation_id else ""
                ),
                "type_label": d.get_diagnostic_type_display(),
                "done_date": d.done_date,
                "expiry_date": d.expiry_date,
            }
        )
    # DPE saisi sur la fiche bien (hors lignes « DPE » déjà créées en diagnostic séparé)
    dpe_already = set(
        PropertyDiagnostic.objects.filter(
            property__in=props,
            diagnostic_type=PropertyDiagnostic.TYPE_DPE,
        ).values_list("property_id", flat=True)
    )
    for p in (
        props.filter(Q(dpe_valid_until__isnull=False) | ~Q(dpe_class=""))
        .exclude(pk__in=dpe_already)
        .order_by("name")
    ):
        type_label = f"DPE ({p.dpe_class})" if p.dpe_class else "DPE"
        rows.append(
            {
                "property_name": p.name,
                "property_pk": p.pk,
                "is_team_property": bool(p.organisation_id),
                "organisation_name": (
                    p.organisation.name if p.organisation_id else ""
                ),
                "type_label": type_label,
                "done_date": None,
                "expiry_date": p.dpe_valid_until,
            }
        )
    rows.sort(
        key=lambda r: (r["expiry_date"] is None, r["expiry_date"] or date.min),
        reverse=True,
    )
    return render(request, "rental/documents/diagnostics.html", {"rows": rows})


@login_required
def stored_documents_list(request: HttpRequest) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    props = properties_visible_to(request.user)
    tenants = tenants_visible_to(request.user)
    docs = (
        StoredDocument.objects.filter(Q(property__in=props) | Q(tenant__in=tenants))
        .select_related("property", "tenant")
        .order_by("-created_at")[:200]
    )
    rows: list[dict] = []
    for doc in docs:
        rows.append(
            {
                "title": doc.title,
                "type_label": doc.get_document_type_display(),
                "where": _stored_doc_where(doc),
                "date": doc.created_at,
                "file_url": reverse("rental:stored_document_download", args=[doc.pk])
                if doc.file
                else None,
                "download_name": "document",
                "source": "stored",
                "stored_pk": doc.pk,
            }
        )
    leases = leases_visible_to(request.user).select_related("property")
    for lease in leases:
        if lease.attached_file:
            rows.append(
                {
                    "title": f"Bail — PDF joint ({lease.property.name})",
                    "type_label": "Bail",
                    "where": lease.property.name,
                    "date": lease.updated_at,
                    "file_url": reverse("rental:lease_attached_download", args=[lease.pk]),
                    "download_name": "bail",
                    "source": "lease",
                    "stored_pk": None,
                }
            )
    inspections = (
        InspectionReport.objects.filter(lease__in=leases)
        .filter(attached_file__isnull=False)
        .exclude(attached_file="")
        .select_related("lease", "lease__property")
        .order_by("-report_date", "-created_at")[:200]
    )
    for insp in inspections:
        label = insp.get_report_type_display()
        rows.append(
            {
                "title": f"État des lieux ({label}) — {insp.lease.property.name}",
                "type_label": "État des lieux",
                "where": insp.lease.property.name,
                "date": insp.created_at,
                "file_url": reverse("rental:inspection_attached_download", args=[insp.pk]),
                "download_name": "etat_des_lieux",
                "source": "inspection",
                "stored_pk": None,
            }
        )
    rows.sort(key=lambda r: r["date"], reverse=True)
    rows = rows[:300]
    return render(request, "rental/documents/stored_documents.html", {"rows": rows})


def _stored_doc_where(doc: StoredDocument) -> str:
    if doc.property_id and doc.tenant_id:
        return f"{doc.property.name} — {doc.tenant}"
    if doc.property_id:
        return doc.property.name
    if doc.tenant_id:
        return str(doc.tenant)
    return "—"


@login_required
def stored_document_download(request: HttpRequest, pk: int) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    doc = get_object_or_404(stored_documents_visible_to(request.user), pk=pk)
    if not doc.file:
        raise Http404("Aucun fichier.")
    filename = os.path.basename(doc.file.name) or "document"
    content_type, _ = mimetypes.guess_type(filename)
    if not content_type:
        content_type = "application/octet-stream"
    response = FileResponse(doc.file.open("rb"), content_type=content_type)
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


@login_required
def stored_document_create(request: HttpRequest) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    if request.method == "POST":
        form = StoredDocumentForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Document enregistré.")
            return redirect("rental:stored_documents")
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = StoredDocumentForm(user=request.user)
    return render(request, "rental/documents/stored_document_form.html", {"form": form})


@login_required
def stored_document_edit(request: HttpRequest, pk: int) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    doc = get_object_or_404(stored_documents_visible_to(request.user), pk=pk)
    if request.method == "POST":
        form = StoredDocumentForm(request.POST, request.FILES, instance=doc, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Document mis à jour.")
            return redirect("rental:stored_documents")
        messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = StoredDocumentForm(instance=doc, user=request.user)
    return render(
        request,
        "rental/documents/stored_document_form.html",
        {"form": form, "document": doc},
    )


@login_required
def stored_document_delete(request: HttpRequest, pk: int) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    doc = get_object_or_404(stored_documents_visible_to(request.user), pk=pk)
    if request.method == "POST":
        if doc.file:
            doc.file.delete(save=False)
        doc.delete()
        messages.success(request, "Document supprimé.")
        return redirect("rental:stored_documents")
    return render(
        request,
        "rental/documents/stored_document_confirm_delete.html",
        {"document": doc},
    )


# --- Multi-biens (dashboard par bien) ---


@login_required
def property_dashboard(request: HttpRequest) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    props = properties_visible_to(request.user).filter(
        archived_at__isnull=True
    ).select_related("organisation").prefetch_related("tags")
    today = timezone.now().date()
    property_stats = []
    for p in props:
        active_lease = Lease.objects.filter(property=p, archived_at__isnull=True, is_active=True).first()
        late_count = RentInvoice.objects.filter(lease__property=p, status="LATE").count()
        property_stats.append({
            "property": p,
            "active_lease": active_lease,
            "late_count": late_count,
        })
    return render(request, "rental/property_dashboard.html", {"property_stats": property_stats})


# --- Portail locataire ---


@login_required
def portal_tenant_list(request: HttpRequest) -> HttpResponse:
    """Liste des locataires avec lien pour générer un accès portail (Premium)."""
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    tenants = tenants_visible_to(request.user).filter(archived_at__isnull=True).order_by("last_name", "first_name")
    return render(request, "rental/portal/portal_tenant_list.html", {"tenants": tenants})


@login_required
def portal_link_create(request: HttpRequest, tenant_pk: int) -> HttpResponse:
    """Génère un lien d'accès au portail pour un locataire (Premium) et l'envoie par email."""
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    tenant = get_object_or_404(tenants_visible_to(request.user), pk=tenant_pk)
    from datetime import timedelta
    from .emails import send_portal_link_email

    expires_at = timezone.now() + timedelta(days=30)
    access = TenantPortalAccess.objects.filter(tenant=tenant).order_by("-created_at").first()
    if access:
        access.expires_at = expires_at
        access.save(update_fields=["expires_at"])
    else:
        access = TenantPortalAccess.objects.create(tenant=tenant, expires_at=expires_at)
    portal_url = request.build_absolute_uri(reverse("tenant_portal", args=[access.token]))

    email_sent = send_portal_link_email(tenant, portal_url, expires_at)
    if email_sent:
        messages.success(request, f"Le lien a été envoyé par email à {tenant.email}.")
    elif tenant.email:
        messages.warning(request, "Le lien a été généré mais l'envoi par email a échoué. Copiez le lien ci-dessous.")
    else:
        messages.warning(request, "Ce locataire n'a pas d'adresse email. Copiez le lien ci-dessous pour le lui transmettre.")

    return render(request, "rental/portal/portal_link_created.html", {
        "tenant": tenant,
        "portal_url": portal_url,
        "expires_at": expires_at,
        "email_sent": email_sent,
    })


def tenant_portal(request: HttpRequest, token: str) -> HttpResponse:
    access, expired_response = _resolve_portal_access(request, token)
    if expired_response is not None:
        return expired_response
    assert access is not None
    tenant = access.tenant
    leases = (
        tenant.leases.filter(archived_at__isnull=True, is_active=True)
        .select_related("property")
        .prefetch_related("lease_announcements")
    )
    lease_ids = list(leases.values_list("pk", flat=True))
    prop_ids = list(leases.values_list("property_id", flat=True))
    today = timezone.now().date()

    invoices = sorted(
        RentInvoice.objects.filter(lease__in=leases)
        .select_related("lease", "lease__property")
        .order_by("-due_date", "-id")[:48],
        key=lambda inv: (inv.due_date, inv.pk),
    )

    overdue_count = sum(1 for inv in invoices if inv.status == "LATE")

    next_unpaid = (
        RentInvoice.objects.filter(lease__in=leases, status__in=["DUE", "LATE"])
        .select_related("lease", "lease__property")
        .order_by("due_date")
        .first()
    )

    stored_qs = (
        StoredDocument.objects.filter(shared_with_portal=True)
        .filter(Q(tenant_id=tenant.pk) | Q(property_id__in=prop_ids))
        .exclude(file="")
        .exclude(file__isnull=True)
        .select_related("property", "tenant")
        .order_by("-created_at")
    )
    documents_by_year: dict = {}
    for doc in stored_qs:
        documents_by_year.setdefault(doc.created_at.year, []).append(doc)
    documents_by_year_list = [
        (y, documents_by_year[y]) for y in sorted(documents_by_year.keys(), reverse=True)
    ]

    inspections_shared = list(
        InspectionReport.objects.filter(lease_id__in=lease_ids, shared_with_portal=True)
        .exclude(attached_file="")
        .exclude(attached_file__isnull=True)
        .select_related("lease", "lease__property")
        .order_by("-report_date")
    )

    practical_rows = list(
        LeasePracticalInfo.objects.filter(lease__in=leases)
        .select_related("lease", "lease__property")
        .order_by("lease_id", "sort_order", "id")
    )

    announcements = []
    for lease in leases:
        for ann in lease.lease_announcements.all():
            if ann.is_visible_now():
                announcements.append(ann)
    announcements.sort(key=lambda a: a.published_at, reverse=True)

    status_filter = (request.GET.get("status") or "").strip().upper()
    valid_status = {c[0] for c in MaintenanceRequest.STATUS_CHOICES}
    if status_filter and status_filter not in valid_status and status_filter != "OPEN":
        status_filter = ""

    _maint_prefetch = (
        MaintenanceRequest.objects.filter(submitted_by=tenant, lease__in=leases)
        .select_related("lease", "lease__property")
        .prefetch_related(
            "attachments",
            Prefetch(
                "thread_messages",
                queryset=MaintenanceThreadMessage.objects.order_by("created_at", "id"),
            ),
        )
    )
    qs = _maint_prefetch
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

    qs = qs.order_by("-created_at", "-id")

    base_all = MaintenanceRequest.objects.filter(submitted_by=tenant, lease__in=leases)
    status_counts = {row["status"]: row["c"] for row in base_all.values("status").annotate(c=Count("id"))}
    open_count = status_counts.get(MaintenanceRequest.STATUS_PENDING, 0) + status_counts.get(
        MaintenanceRequest.STATUS_IN_PROGRESS, 0
    )
    total_count = base_all.exclude(status__in=MaintenanceRequest.CLOSED_STATUSES).count()

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

    meter_readings = list(
        MeterReading.objects.filter(submitted_by=tenant, lease__in=leases)
        .select_related("lease", "lease__property")
        .order_by("-reading_date", "-created_at")[:30]
    )

    if overdue_count > 0:
        dashboard_status = "action"
        dashboard_message = (
            f"{overdue_count} échéance(s) en retard. Merci de régulariser ou de contacter votre bailleur."
        )
    elif next_unpaid:
        dashboard_status = "action"
        dashboard_message = (
            f"À régler : {next_unpaid.total_amount} € pour le {next_unpaid.due_date.strftime('%d/%m/%Y')} "
            f"— {next_unpaid.lease.property.name} ({next_unpaid.get_status_display()})."
        )
    else:
        dashboard_status = "ok"
        dashboard_message = "Aucune échéance impayée parmi les loyers affichés."

    reminders = []
    for lease in leases:
        if lease.tenant_insurance_valid_until:
            days_ins = (lease.tenant_insurance_valid_until - today).days
            if days_ins <= 30:
                reminders.append(
                    {
                        "text": (
                            f"Assurance habitation ({lease.property.name}) : "
                            f"validité jusqu'au {lease.tenant_insurance_valid_until.strftime('%d/%m/%Y')}."
                        )
                    }
                )
        if lease.lease_renewal_reminder_date:
            days_r = (lease.lease_renewal_reminder_date - today).days
            if -14 <= days_r <= 90:
                reminders.append(
                    {
                        "text": (
                            f"Rappel ({lease.property.name}) : date à noter le "
                            f"{lease.lease_renewal_reminder_date.strftime('%d/%m/%Y')}."
                        )
                    }
                )

    maint_form = TenantPortalMaintenanceForm(leases=leases)
    meter_form = TenantPortalMeterForm(leases=leases)
    portal_has_rent_rib = any(bool((getattr(l, "rent_payment_iban", None) or "").strip()) for l in leases)

    tenant_portal_demandes_attention_count = maintenance_requests_needing_tenant_attention_qs(
        tenant,
        leases,
    ).count()

    return render(
        request,
        "rental/portal/tenant_portal.html",
        {
            "tenant": tenant,
            "leases": leases,
            "portal_has_rent_rib": portal_has_rent_rib,
            "invoices": invoices,
            "portal_token": token,
            "next_unpaid": next_unpaid,
            "documents_by_year_list": documents_by_year_list,
            "inspections_shared": inspections_shared,
            "practical_rows": practical_rows,
            "announcements": announcements,
            "maintenance_list": page.object_list,
            "maintenance_page_obj": page,
            "status_filter": status_filter,
            "status_rows": status_rows,
            "open_count": open_count,
            "total_count": total_count,
            "maint_querystring": maint_querystring,
            "maint_params_without_status": maint_params_without_status,
            "meter_readings": meter_readings,
            "dashboard_status": dashboard_status,
            "dashboard_message": dashboard_message,
            "reminders": reminders,
            "maint_form": maint_form,
            "meter_form": meter_form,
            "today": today,
            "tenant_portal_demandes_attention_count": tenant_portal_demandes_attention_count,
        },
    )


@require_http_methods(["POST"])
def tenant_portal_maintenance_create(request: HttpRequest, token: str) -> HttpResponseRedirect:
    access, expired_response = _resolve_portal_access(request, token)
    if expired_response is not None:
        return expired_response
    assert access is not None
    tenant = access.tenant
    leases = tenant.leases.filter(archived_at__isnull=True, is_active=True)
    form = TenantPortalMaintenanceForm(request.POST, leases=leases)
    if form.is_valid():
        lease = form.cleaned_data["lease"]
        mr = MaintenanceRequest.objects.create(
            lease=lease,
            submitted_by=tenant,
            category=form.cleaned_data["category"],
            title=form.cleaned_data["title"],
            description=form.cleaned_data["description"],
            status=MaintenanceRequest.STATUS_PENDING,
        )
        for f in request.FILES.getlist("attachments")[:5]:
            if f.size > 5 * 1024 * 1024:
                messages.warning(request, "Une pièce jointe a été ignorée (max 5 Mo par fichier).")
                continue
            MaintenanceRequestAttachment.objects.create(
                request=mr,
                file=f,
                original_name=getattr(f, "name", "") or "",
            )
        messages.success(request, "Votre demande a été transmise au bailleur.")
    else:
        messages.error(request, "Formulaire de demande incomplet ou invalide.")
    return HttpResponseRedirect(_tenant_portal_demandes_redirect_url(token, request))


@require_http_methods(["POST"])
def tenant_portal_maintenance_reply(request: HttpRequest, token: str, pk: int) -> HttpResponseRedirect:
    """Ajoute un message locataire sur le fil d'une demande d'intervention."""
    access, expired_response = _resolve_portal_access(request, token)
    if expired_response is not None:
        return expired_response
    assert access is not None
    tenant = access.tenant
    leases = tenant.leases.filter(archived_at__isnull=True, is_active=True)
    mr = get_object_or_404(
        MaintenanceRequest.objects.filter(pk=pk, submitted_by=tenant, lease__in=leases)
    )
    if mr.is_closed:
        messages.warning(
            request,
            "Cette demande est clôturée ; vous ne pouvez plus y répondre.",
        )
        return HttpResponseRedirect(_tenant_portal_demandes_redirect_url(token, request))
    body = (request.POST.get("body") or "").strip()
    if not body:
        messages.error(request, "Veuillez saisir un message.")
        return HttpResponseRedirect(_tenant_portal_demandes_redirect_url(token, request))
    if len(body) > 8000:
        messages.error(request, "Message trop long (max. 8 000 caractères).")
        return HttpResponseRedirect(_tenant_portal_demandes_redirect_url(token, request))
    MaintenanceThreadMessage.objects.create(
        maintenance_request=mr,
        sender=MaintenanceThreadMessage.SENDER_TENANT,
        body=body,
    )
    messages.success(request, "Votre message a été envoyé au bailleur.")
    return HttpResponseRedirect(_tenant_portal_demandes_redirect_url(token, request))


@require_http_methods(["POST"])
def tenant_portal_meter_submit(request: HttpRequest, token: str) -> HttpResponseRedirect:
    access, expired_response = _resolve_portal_access(request, token)
    if expired_response is not None:
        return expired_response
    assert access is not None
    tenant = access.tenant
    leases = tenant.leases.filter(archived_at__isnull=True, is_active=True)
    form = TenantPortalMeterForm(request.POST, leases=leases)
    if form.is_valid():
        MeterReading.objects.create(
            lease=form.cleaned_data["lease"],
            submitted_by=tenant,
            meter_type=form.cleaned_data["meter_type"],
            reading_date=form.cleaned_data["reading_date"],
            value=form.cleaned_data["value"],
            unit=(form.cleaned_data.get("unit") or "").strip(),
            notes=(form.cleaned_data.get("notes") or "").strip(),
        )
        messages.success(request, "Relevé de compteurs enregistré.")
    else:
        messages.error(request, "Relevé de compteurs invalide — vérifiez les champs.")
    return HttpResponseRedirect(reverse("tenant_portal", args=[token]) + "#releves")


def tenant_portal_document_download(request: HttpRequest, token: str, pk: int) -> FileResponse | HttpResponse:
    access, expired_response = _resolve_portal_access(request, token)
    if expired_response is not None:
        return expired_response
    assert access is not None
    tenant = access.tenant
    leases = tenant.leases.filter(archived_at__isnull=True, is_active=True)
    prop_ids = set(leases.values_list("property_id", flat=True))
    doc = get_object_or_404(StoredDocument, pk=pk, shared_with_portal=True)
    allowed = (doc.tenant_id == tenant.pk) or (doc.property_id and doc.property_id in prop_ids)
    if not allowed or not doc.file:
        raise Http404()
    filename = os.path.basename(doc.file.name) or "document"
    return FileResponse(doc.file.open("rb"), as_attachment=True, filename=filename)


def tenant_portal_inspection_download(request: HttpRequest, token: str, pk: int) -> FileResponse | HttpResponse:
    access, expired_response = _resolve_portal_access(request, token)
    if expired_response is not None:
        return expired_response
    assert access is not None
    tenant = access.tenant
    insp = get_object_or_404(
        InspectionReport.objects.filter(lease__tenants=tenant, shared_with_portal=True),
        pk=pk,
    )
    if not insp.attached_file:
        raise Http404()
    fn = os.path.basename(insp.attached_file.name) or "etat-des-lieux.pdf"
    return FileResponse(insp.attached_file.open("rb"), as_attachment=True, filename=fn)


def tenant_portal_maintenance_attachment_download(
    request: HttpRequest, token: str, att_pk: int
) -> FileResponse | HttpResponse:
    access, expired_response = _resolve_portal_access(request, token)
    if expired_response is not None:
        return expired_response
    assert access is not None
    tenant = access.tenant
    att = get_object_or_404(MaintenanceRequestAttachment.objects.select_related("request__lease"), pk=att_pk)
    if not att.request.lease.tenants.filter(pk=tenant.pk).exists():
        raise Http404()
    fn = os.path.basename(att.file.name) or "piece-jointe"
    return FileResponse(att.file.open("rb"), as_attachment=True, filename=fn)


def tenant_portal_lease_pdf(request: HttpRequest, token: str, lease_pk: int) -> HttpResponse:
    """PDF du bail pour le locataire, authentifié par le jeton du portail (sans compte SyLoc)."""
    access, expired_response = _resolve_portal_access(request, token)
    if expired_response is not None:
        return expired_response
    assert access is not None
    tenant = access.tenant
    lease = get_object_or_404(
        Lease.objects.filter(
            tenants=tenant,
            archived_at__isnull=True,
            is_active=True,
        ).select_related("property"),
        pk=lease_pk,
    )
    try:
        pdf_bytes = build_lease_pdf(lease)
    except Exception as e:
        return HttpResponse(
            f"Impossible de générer le PDF : {e}",
            status=500,
            content_type="text/plain; charset=utf-8",
        )
    filename = lease_attachment_filename(lease)
    # FileResponse + as_attachment : nom de fichier fiable (évite « 10 » venant de l’URL …/10.pdf).
    return FileResponse(
        BytesIO(pdf_bytes),
        content_type="application/pdf",
        as_attachment=True,
        filename=filename,
    )


def tenant_portal_lease_pdf_redirect(request: HttpRequest, token: str, lease_pk: int) -> HttpResponseRedirect:
    """Ancienne URL …/bail/<pk>.pdf → nouvelle route sans .pdf dans le chemin."""
    return redirect("tenant_portal_lease_pdf", token=token, lease_pk=lease_pk)


def tenant_portal_quittance_pdf(request: HttpRequest, token: str, invoice_pk: int) -> HttpResponse:
    """Quittance PDF pour un loyer payé, authentifié par le jeton du portail."""
    access, expired_response = _resolve_portal_access(request, token)
    if expired_response is not None:
        return expired_response
    assert access is not None
    tenant = access.tenant
    invoice = get_object_or_404(RentInvoice, pk=invoice_pk)
    if invoice.status != "PAID":
        raise Http404("Quittance disponible uniquement pour les loyers marqués comme payés.")
    if not invoice.lease.tenants.filter(pk=tenant.pk).exists():
        raise Http404()
    try:
        if invoice.lease.tenants.count() > 1:
            pdf_bytes = build_quittance_pdf(invoice, tenant=tenant)
        else:
            pdf_bytes = build_quittance_pdf(invoice)
    except Exception as e:
        logger.exception(
            "tenant_portal_quittance_pdf: échec génération (invoice_pk=%s, tenant_pk=%s)",
            invoice_pk,
            tenant.pk,
        )
        if settings.DEBUG:
            return HttpResponse(
                f"{type(e).__name__}: {e}",
                status=500,
                content_type="text/plain; charset=utf-8",
            )
        return HttpResponse(
            "Impossible de générer la quittance pour le moment.",
            status=500,
            content_type="text/plain; charset=utf-8",
        )
    filename = quittance_attachment_filename(invoice, tenant)
    return FileResponse(
        BytesIO(pdf_bytes),
        content_type="application/pdf",
        as_attachment=True,
        filename=filename,
    )


def tenant_portal_quittance_pdf_redirect(request: HttpRequest, token: str, invoice_pk: int) -> HttpResponseRedirect:
    """Ancienne URL …/quittance/<pk>.pdf → nouvelle route."""
    return redirect("tenant_portal_quittance_pdf", token=token, invoice_pk=invoice_pk)


# --- Intégrations ---


@login_required
def webhooks_list(request: HttpRequest) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    webhooks = Webhook.objects.filter(owner=request.user).order_by("-created_at")
    return render(request, "rental/integrations/webhooks.html", {"webhooks": webhooks})


@login_required
def calendar_ics(request: HttpRequest) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    from django.http import HttpResponse
    user = request.user
    props = properties_visible_to(user).filter(archived_at__isnull=True)
    leases = Lease.objects.filter(property__in=props, archived_at__isnull=True, is_active=True)
    events = []
    for inv in RentInvoice.objects.filter(lease__in=leases).order_by("due_date")[:365]:
        events.append((inv.due_date, f"Loyer {inv.lease.property.name}", inv.lease.property.name))
    for lease in leases:
        if lease.end_date:
            events.append((lease.end_date, f"Fin de bail {lease.property.name}", lease.property.name))
    ics_lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SyLoc//FR"]
    for d, summary, loc in events:
        ics_lines.append("BEGIN:VEVENT")
        ics_lines.append(f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}")
        ics_lines.append(f"DTEND;VALUE=DATE:{(d + timedelta(days=1)).strftime('%Y%m%d')}")
        ics_lines.append(f"SUMMARY:{summary}")
        ics_lines.append(f"LOCATION:{loc}")
        ics_lines.append("END:VEVENT")
    ics_lines.append("END:VCALENDAR")
    response = HttpResponse("\r\n".join(ics_lines), content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="syloc.ics"'
    return response


# --- Sécurité et audit ---


@login_required
def activity_log_list(request: HttpRequest) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    logs = UserActivityLog.objects.filter(user=request.user).select_related("content_type").order_by("-timestamp")[:200]
    return render(request, "rental/audit/activity_log.html", {"logs": logs})


@login_required
def data_export(request: HttpRequest) -> HttpResponse:
    redirect_resp = _require_premium(request)
    if redirect_resp is not None:
        return redirect_resp
    if request.method != "POST":
        return render(request, "rental/audit/data_export.html")
    import json
    user = request.user
    props = list(properties_visible_to(user).values("id", "name", "address", "city", "zip_code"))
    prop_ids = [p["id"] for p in props]
    tenants = list(tenants_visible_to(user).values("id", "first_name", "last_name", "email", "phone"))
    leases = list(leases_visible_to(user).values("id", "property_id", "start_date", "end_date", "rent", "charges"))
    prop_works = list(
        PropertyWork.objects.filter(property_id__in=prop_ids).values(
            "id",
            "property_id",
            "work_type",
            "title",
            "description",
            "work_date",
            "amount",
            "created_at",
        )
    )
    now = timezone.now()
    data = {
        "format": "syloc_data_export_v1",
        "export_date": now.isoformat(timespec="seconds"),
        "properties": props,
        "property_works": prop_works,
        "tenants": tenants,
        "leases": leases,
    }
    response = HttpResponse(
        json.dumps(data, indent=2, default=str, ensure_ascii=False),
        content_type="application/json; charset=utf-8",
    )
    ts = timezone.localtime(now).strftime("%Y%m%d_%H%M")
    response["Content-Disposition"] = f'attachment; filename="syloc-export_{ts}.json"'
    return response
