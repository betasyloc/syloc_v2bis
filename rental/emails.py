"""Envoi des emails de rappel de loyer, relance impayés, quittance et liens de signature."""
from __future__ import annotations

import mimetypes
import logging

from django.conf import settings
from django.core.mail import EmailMessage, send_mail
logger = logging.getLogger(__name__)


from django.template import Context, Template
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import linebreaks


def _lease_tenant_emails(lease_or_id):
    """Liste des emails des locataires du bail (colocation : tous ceux qui ont un email).
    Accepte un objet Lease ou l'ID du bail (int).
    """
    from .models import Tenant
    lease_id = lease_or_id.pk if hasattr(lease_or_id, "pk") else int(lease_or_id)
    emails = []
    for t in Tenant.objects.filter(leases__id=lease_id).only("email"):
        addr = (t.email or "").strip()
        if addr:
            emails.append(addr)
    return emails


def send_rent_reminder(invoice) -> bool:
    """
    Envoie un rappel de loyer aux locataires (avant échéance).
    En colocation, envoie à tous les locataires ayant un email.
    Retourne True si au moins un email a été envoyé.
    """
    recipient_list = _lease_tenant_emails(invoice.lease)
    if not recipient_list:
        return False
    tenant = invoice.lease.tenants.first()
    subject = f"Rappel : loyer dû le {invoice.due_date.strftime('%d/%m/%Y')}"
    prop = invoice.lease.property
    context = {
        "tenant": tenant,
        "invoice": invoice,
        "property": prop,
        "due_date": invoice.due_date,
        "total_amount": invoice.total_amount,
    }
    body = render_to_string("rental/emails/rent_reminder.txt", context)
    html_body = render_to_string("rental/emails/rent_reminder.html", context)

    sent = send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipient_list,
        fail_silently=True,
        html_message=html_body,
    )
    return sent > 0


def send_late_rent_reminder(
    invoice,
    *,
    days_after_due: int,
    reminder_rank: int = 1,
    rule=None,
) -> bool:
    """
    Envoie une relance impayé aux locataires (palier J+N après échéance).
    En colocation, envoie à tous les locataires ayant un email.
    Si ``rule`` (ReminderRule) a un corps personnalisé, il est interprété comme template Django
    avec les mêmes variables que le modèle par défaut.
    """
    recipient_list = _lease_tenant_emails(invoice.lease)
    if not recipient_list:
        return False
    tenant = invoice.lease.tenants.first()
    prop = invoice.lease.property
    context = {
        "tenant": tenant,
        "invoice": invoice,
        "property": prop,
        "due_date": invoice.due_date,
        "total_amount": invoice.total_amount,
        "reminder_number": reminder_rank,
        "days_after_due": days_after_due,
        "reminder_label": f"J+{days_after_due}",
    }

    if rule is not None:
        subject = rule.email_subject
        if (rule.email_body_template or "").strip():
            tpl = Template(rule.email_body_template)
            ctx = Context(context)
            body = tpl.render(ctx)
            html_body = linebreaks(body, autoescape=True)
        else:
            body = render_to_string("rental/emails/late_reminder.txt", context)
            html_body = render_to_string("rental/emails/late_reminder.html", context)
    else:
        subject = (
            f"Relance impayé {context['reminder_label']} : "
            f"loyer {invoice.due_date.strftime('%d/%m/%Y')}"
        )
        body = render_to_string("rental/emails/late_reminder.txt", context)
        html_body = render_to_string("rental/emails/late_reminder.html", context)

    sent = send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipient_list,
        fail_silently=True,
        html_message=html_body,
    )
    return sent > 0


def send_quittance_email(invoice, pdf_bytes: bytes, filename: str, recipient_list: list | None = None) -> int:
    """
    Envoie la quittance PDF par email aux locataires du bail.
    Si recipient_list est fourni, on l'utilise ; sinon on le calcule via _lease_tenant_emails(invoice.lease).
    Retourne le nombre d'emails envoyés (1 si envoi réussi, 0 sinon).
    """
    if recipient_list is None:
        recipient_list = _lease_tenant_emails(invoice.lease)
    if not recipient_list:
        return 0
    due = invoice.due_date
    prop = invoice.lease.property
    subject = f"Quittance de loyer - {prop.name} - {due.strftime('%d/%m/%Y')}"
    body = (
        f"Bonjour,\n\n"
        f"Veuillez trouver ci-joint votre quittance de loyer pour {prop.name} "
        f"(période {due.strftime('%d/%m/%Y')}).\n\n"
        f"Cordialement,\n"
        f"Votre bailleur"
    )
    msg = EmailMessage(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipient_list,
    )
    msg.attach(filename, pdf_bytes, "application/pdf")
    try:
        msg.send(fail_silently=False)
        return 1
    except Exception:
        return 0


def send_quittance_to_tenant(invoice, tenant, pdf_bytes: bytes, filename: str) -> bool:
    """
    Envoie la quittance PDF (part du locataire) à un seul locataire.
    Retourne True si l'email a été envoyé (le locataire doit avoir un email).
    """
    email = (tenant.email or "").strip()
    if not email:
        return False
    due = invoice.due_date
    prop = invoice.lease.property
    subject = f"Quittance de loyer (votre part) - {prop.name} - {due.strftime('%d/%m/%Y')}"
    body = (
        f"Bonjour {tenant.first_name},\n\n"
        f"Veuillez trouver ci-joint votre quittance de loyer (part colocation) "
        f"pour {prop.name} (période {due.strftime('%d/%m/%Y')}).\n\n"
        f"Cordialement,\n"
        f"Votre bailleur"
    )
    msg = EmailMessage(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    msg.attach(filename, pdf_bytes, "application/pdf")
    try:
        msg.send(fail_silently=False)
        return True
    except Exception:
        return False


def send_quittance_per_tenant(invoice) -> tuple[int, int]:
    """
    Pour un bail avec plusieurs locataires : génère et envoie une quittance
    à chaque locataire (montant = part égale du loyer et des charges).
    Retourne (nombre d'emails envoyés, nombre de locataires avec email).
    """
    from .pdf import build_quittance_pdf, quittance_attachment_filename

    tenants = list(invoice.lease.tenants.all())
    if len(tenants) < 2:
        return (0, 0)
    sent = 0
    with_email = 0
    for tenant in tenants:
        if not (tenant.email or "").strip():
            continue
        with_email += 1
        pdf_bytes = build_quittance_pdf(invoice, tenant=tenant)
        filename = quittance_attachment_filename(invoice, tenant)
        if send_quittance_to_tenant(invoice, tenant, pdf_bytes, filename):
            sent += 1
    return (sent, with_email)


def send_signing_invitation_email(invitation, sign_url: str, connection=None) -> tuple[bool, str]:
    """
    Envoie l'email contenant le lien de signature électronique pour un bail ou un état des lieux.
    Retourne (ok, erreur) : ok=True si l'email a été envoyé.
    """
    doc = invitation.document
    if hasattr(doc, "property"):  # Lease
        doc_type = "bail"
        doc_label = f"Bail – {doc.property.name}"
    else:  # InspectionReport
        doc_type = "état des lieux"
        doc_label = f"État des lieux {doc.get_report_type_display()} – {doc.lease.property.name}"

    subject = f"SyLoc – Signature électronique : {doc_label}"
    body = (
        f"Bonjour,\n\n"
        f"Vous êtes invité(e) à signer électroniquement le document suivant : {doc_label}.\n\n"
        f"Cliquez sur le lien ci-dessous pour accéder à la page de signature (lien valide 30 jours) :\n\n"
        f"{sign_url}\n\n"
        f"Si vous n'êtes pas à l'origine de cette demande, vous pouvez ignorer ce message.\n\n"
        f"SyLoc – Gestion locative"
    )
    try:
        msg = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[invitation.email],
            connection=connection,
        )
        sent = msg.send(fail_silently=False)
        return (sent > 0, "")
    except Exception as exc:
        logger.exception(
            "send_signing_invitation_email failed (invitation=%s, email=%s)",
            getattr(invitation, "pk", None),
            getattr(invitation, "email", None),
        )
        return (False, str(exc)[:240])


def send_portal_link_email(tenant, portal_url: str, expires_at) -> bool:
    """
    Envoie par email le lien d'accès au portail locataire au locataire.
    Retourne True si l'email a été envoyé, False si pas d'email ou échec.
    """
    if not tenant.email or not tenant.email.strip():
        return False
    subject = "SyLoc – Accès à votre espace locataire"
    body = (
        f"Bonjour {tenant.first_name},\n\n"
        f"Votre bailleur vous envoie un lien pour accéder à votre espace locataire SyLoc.\n"
        f"Vous pourrez y consulter vos baux et quittances.\n\n"
        f"Lien d'accès (valide jusqu'au {expires_at.strftime('%d/%m/%Y')}) :\n\n"
        f"{portal_url}\n\n"
        f"Si vous n'êtes pas à l'origine de cette demande, vous pouvez ignorer ce message.\n\n"
        f"Cordialement,\n"
        f"SyLoc – Gestion locative"
    )
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[tenant.email.strip()],
            fail_silently=True,
        )
        return True
    except Exception:
        return False


def get_suggestion_staff_recipient_emails() -> list[str]:
    """Destinataires des notifications de suggestions (tableau de bord)."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    explicit = getattr(settings, "SUGGESTION_STAFF_EMAILS", None) or []
    if explicit:
        return list(explicit)
    return [
        u.email.strip()
        for u in User.objects.filter(is_staff=True, is_active=True).exclude(email__exact="")
        if (u.email or "").strip()
    ]


def send_user_suggestion_notifications(suggestion) -> tuple[bool, str]:
    """
    Notifie le staff par email (avec pièces jointes) et envoie un accusé au contributeur.
    Retourne (succès envoi staff, message d'erreur éventuel).
    """
    recipients = get_suggestion_staff_recipient_emails()
    if not recipients:
        return False, (
            "Aucune adresse de destination : renseignez SUGGESTION_STAFF_EMAILS ou des comptes staff avec email."
        )

    subject = f"[SyLoc] Suggestion – {suggestion.get_theme_display()} – {suggestion.contact_email}"
    body = (
        f"Nouvelle suggestion depuis la page Suggestions SyLoc.\n\n"
        f"Thème : {suggestion.get_theme_display()}\n"
        f"Email de contact : {suggestion.contact_email}\n"
        f"Compte : {suggestion.user.get_username()} (id={suggestion.user_id})\n\n"
        f"Message :\n{suggestion.message}\n\n"
        f"Pièces jointes : {suggestion.attachments.count()} fichier(s).\n"
        f"ID en base : {suggestion.pk}\n"
    )
    try:
        msg = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=recipients,
        )
        for att in suggestion.attachments.all():
            name = (att.original_name or "").strip() or "piece_jointe"
            att.file.open("rb")
            try:
                data = att.file.read()
            finally:
                att.file.close()
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            msg.attach(name, data, ctype)
        msg.send(fail_silently=False)
    except Exception as exc:
        return False, str(exc)

    try:
        send_mail(
            subject="SyLoc – Votre suggestion a bien été envoyée",
            message=(
                "Bonjour,\n\n"
                "Merci pour votre retour. L'équipe SyLoc en a bien pris connaissance.\n\n"
                "Cordialement,\n"
                "L'équipe SyLoc"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[suggestion.contact_email],
            fail_silently=True,
        )
    except Exception:
        pass
    return True, ""
