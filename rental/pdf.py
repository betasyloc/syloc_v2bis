"""Génération des quittances, baux et états des lieux au format PDF (avec signatures électroniques)."""
from __future__ import annotations

import html
import logging
from datetime import date
from io import BytesIO
from calendar import monthrange
import os

logger = logging.getLogger(__name__)

from django.contrib.contenttypes.models import ContentType
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image as RLImage,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Couleurs SyLoc (alignées sur l’interface)
COLOR_PRIMARY = colors.HexColor("#0d9488")
COLOR_PRIMARY_LIGHT = colors.HexColor("#ccfbf1")
COLOR_TEXT = colors.HexColor("#1e293b")
COLOR_TEXT_MUTED = colors.HexColor("#64748b")

MOIS_FR = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


# Lieu et signature du bailleur (personnalisés)
QUITTANCE_FAIT_VILLE = "Angers"
QUITTANCE_SIGNATURE_TEXTE = "PSY"


def _xml_text(value) -> str:
    """Texte utilisateur pour ReportLab Paragraph (mini-HTML/XML : &, <, > interdits bruts)."""
    if value is None:
        return ""
    return html.escape(str(value), quote=False)


def _pdf_info_string(value) -> str:
    """Chaînes /Title et /Author des métadonnées PDF (pas de caractères de contrôle)."""
    if value is None:
        return ""
    t = "".join(c for c in str(value) if ord(c) >= 32)
    return (t[:250] or "").strip() or "SyLoc"


def _signature_display(style_signature):
    """Affiche la signature du bailleur (texte « PSY »)."""
    return Paragraph(QUITTANCE_SIGNATURE_TEXTE, style_signature)


def build_quittance_pdf(invoice, tenant=None, amount_rent=None, amount_charges=None) -> bytes:
    """
    Construit le PDF de la quittance de loyer pour une RentInvoice.
    Si tenant (et optionnellement amount_rent, amount_charges) est fourni, la quittance
    affiche uniquement ce locataire et le montant de sa part (pour colocation).
    Sinon, affiche tous les locataires et le montant total.
    Retourne les octets du PDF.
    """
    buffer = BytesIO()
    lease = invoice.lease
    prop = lease.property
    owner = prop.owner
    owner_display = (
        (getattr(owner, "get_full_name", None) and owner.get_full_name())
        or getattr(owner, "username", "")
        or (owner.email or "—")
    )
    # Auteur des métadonnées PDF (sinon lecteurs = « Anonymous ») — pas de tiret seul.
    owner_name_meta = owner_display if owner_display != "—" else "SyLoc"
    owner_email = (owner.email or "").strip()
    due = invoice.due_date
    month_name = MOIS_FR[due.month]
    year = due.year
    # Métadonnées PDF : sans auteur/titre explicites, beaucoup de lecteurs affichent « Anonymous ».
    pdf_title = f"Quittance {prop.name} - {month_name} {year}"
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.8 * cm,
        leftMargin=1.8 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.8 * cm,
        title=_pdf_info_string(pdf_title),
        author=_pdf_info_string(owner_name_meta),
        creator="SyLoc",
    )
    styles = getSampleStyleSheet()

    # Styles personnalisés
    style_title = ParagraphStyle(
        name="QuittanceTitle",
        parent=styles["Heading1"],
        fontSize=22,
        textColor=COLOR_PRIMARY,
        spaceAfter=4,
        spaceBefore=0,
        fontName="Helvetica-Bold",
    )
    style_subtitle = ParagraphStyle(
        name="QuittanceSubtitle",
        parent=styles["Normal"],
        fontSize=11,
        textColor=COLOR_TEXT_MUTED,
        spaceAfter=20,
    )
    style_section_title = ParagraphStyle(
        name="QuittanceSection",
        parent=styles["Normal"],
        fontSize=9,
        textColor=COLOR_TEXT_MUTED,
        fontName="Helvetica-Bold",
        spaceAfter=4,
        spaceBefore=14,
    )
    style_body = ParagraphStyle(
        name="QuittanceBody",
        parent=styles["Normal"],
        fontSize=10,
        textColor=COLOR_TEXT,
        spaceAfter=2,
    )
    style_note = ParagraphStyle(
        name="QuittanceNote",
        parent=styles["Normal"],
        fontSize=8,
        textColor=COLOR_TEXT_MUTED,
        spaceAfter=0,
        spaceBefore=12,
    )
    style_signature_label = ParagraphStyle(
        name="QuittanceSignatureLabel",
        parent=styles["Normal"],
        fontSize=9,
        textColor=COLOR_TEXT_MUTED,
        spaceAfter=6,
        spaceBefore=0,
    )
    style_signature_text = ParagraphStyle(
        name="QuittanceSignatureText",
        parent=styles["Normal"],
        fontSize=14,
        textColor=COLOR_TEXT,
        fontName="Helvetica-Oblique",
        spaceAfter=0,
        spaceBefore=4,
    )

    if tenant is not None:
        tenants_list = [tenant]
        n_tenants = max(1, lease.tenants.count())
        loyer_total = float(invoice.amount_rent or 0)
        charges_total = float(invoice.amount_charges or 0)
        if amount_rent is not None and amount_charges is not None:
            loyer = float(amount_rent)
            charges = float(amount_charges)
        else:
            loyer = round(loyer_total / n_tenants, 2)
            charges = round(charges_total / n_tenants, 2)
    else:
        tenants_list = list(lease.tenants.all())
        loyer = float(invoice.amount_rent or 0)
        charges = float(invoice.amount_charges or 0)

    # Période couverte
    _, last_day = monthrange(year, due.month)
    period_start = date(year, due.month, 1)
    period_end = date(year, due.month, last_day)
    period_str = f"du {period_start.strftime('%d/%m/%Y')} au {period_end.strftime('%d/%m/%Y')}"

    elements = []

    # ----- En-tête -----
    elements.append(Paragraph("SyLoc", style_title))
    elements.append(Paragraph("Quittance de loyer", style_subtitle))
    elements.append(Paragraph(f"<b>Période :</b> {period_str}", style_body))
    if tenant is not None:
        elements.append(Paragraph(
            f"<b>Part du locataire :</b> {_xml_text(tenant)} (colocation)",
            style_body,
        ))
    elements.append(Spacer(1, 0.6 * cm))

    # ----- Bloc Bailleur / Locataire / Bien (2 colonnes visuelles via table) -----
    if tenants_list:
        tenant_lines = []
        for t in tenants_list:
            line = _xml_text(str(t))
            em = (getattr(t, "email", None) or "").strip()
            if em:
                line += f" &lt;{_xml_text(em)}&gt;"
            tenant_lines.append(line)
        tenants_text = "<br/>".join(tenant_lines)
    else:
        tenants_text = "—"

    address_parts = [prop.name]
    if prop.address:
        address_parts.append(prop.address)
    if prop.zip_code or prop.city:
        address_parts.append(f"{prop.zip_code or ''} {prop.city or ''}".strip())
    address_text = _xml_text(", ".join(address_parts) or "—")

    block_data = [
        [
            Paragraph("<b>Bailleur</b>", style_body),
            Paragraph("<b>Locataire(s)</b>", style_body),
        ],
        [
            Paragraph(
                f"{_xml_text(owner_display)}<br/>{_xml_text(owner_email)}"
                if owner_email
                else _xml_text(owner_display),
                style_body,
            ),
            Paragraph(tenants_text, style_body),
        ],
        [
            Paragraph("<b>Adresse du bien loué</b>", style_body),
            Paragraph("", style_body),
        ],
        [
            Paragraph(address_text, style_body),
            Paragraph("", style_body),
        ],
    ]
    block_table = Table(block_data, colWidths=[8 * cm, 8 * cm])
    block_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (-1, -1), COLOR_TEXT),
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_PRIMARY_LIGHT),
        ("BACKGROUND", (0, 2), (0, 2), COLOR_PRIMARY_LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("SPAN", (1, 2), (1, 3)),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("INNERGRID", (0, 0), (-1, 1), 0.25, colors.HexColor("#e2e8f0")),
    ]))
    elements.append(block_table)
    elements.append(Spacer(1, 0.7 * cm))

    # ----- Détail des montants -----
    total = loyer + charges

    data = [
        ["Désignation", "Montant (€)"],
        ["Loyer", f"{loyer:,.2f}".replace(",", " ")],
        ["Charges", f"{charges:,.2f}".replace(",", " ")],
        ["Total à payer", f"{total:,.2f}".replace(",", " ")],
    ]
    table = Table(data, colWidths=[10 * cm, 4 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
        ("TOPPADDING", (0, 0), (-1, 0), 10),
        ("BACKGROUND", (0, -1), (-1, -1), COLOR_PRIMARY_LIGHT),
        ("TOPPADDING", (0, -1), (-1, -1), 10),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 0.8 * cm))

    # ----- Mention légale -----
    elements.append(Paragraph(
        "Reçu du présent loyer et des charges. Ce document ne peut servir de justificatif de domicile.",
        style_note,
    ))
    elements.append(Spacer(1, 1 * cm))

    # ----- Signature du bailleur -----
    today = date.today()
    fait_le = today.strftime("%d/%m/%Y")
    elements.append(Paragraph(
        f"Fait à {QUITTANCE_FAIT_VILLE}, le {fait_le}",
        style_signature_label,
    ))
    elements.append(Paragraph("Signature du bailleur", style_signature_label))
    elements.append(_signature_display(style_signature_text))

    try:
        doc.build(elements)
    except Exception:
        logger.exception("build_quittance_pdf: échec doc.build (invoice_id=%s)", getattr(invoice, "pk", None))
        raise
    return buffer.getvalue()


def _signatures_for_document(instance):
    """Retourne les DocumentSignature liées à un bail ou un état des lieux (via SigningInvitation)."""
    from django.db.models import Case, IntegerField, When

    from .models import DocumentSignature, SigningInvitation

    ct = ContentType.objects.get_for_model(instance)
    return (
        DocumentSignature.objects.filter(
            invitation__content_type=ct,
            invitation__object_id=instance.pk,
        )
        .select_related("invitation")
        .order_by(
            Case(
                When(invitation__role=SigningInvitation.ROLE_LANDLORD, then=0),
                When(invitation__role=SigningInvitation.ROLE_TENANT, then=1),
                default=2,
                output_field=IntegerField(),
            ),
            "invitation__tenant_id",
            "signed_at",
        )
    )


def _doc_styles():
    """Styles communs pour baux et états des lieux."""
    styles = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            name="DocTitle",
            parent=styles["Heading1"],
            fontSize=18,
            textColor=COLOR_PRIMARY,
            spaceAfter=8,
            fontName="Helvetica-Bold",
        ),
        "body": ParagraphStyle(
            name="DocBody",
            parent=styles["Normal"],
            fontSize=10,
            textColor=COLOR_TEXT,
            spaceAfter=6,
        ),
        "sig_label": ParagraphStyle(
            name="DocSigLabel",
            parent=styles["Normal"],
            fontSize=9,
            textColor=COLOR_TEXT_MUTED,
            spaceAfter=4,
            spaceBefore=14,
        ),
        "sig_section_title": ParagraphStyle(
            name="DocSigSectionTitle",
            parent=styles["Normal"],
            fontSize=14,
            textColor=COLOR_PRIMARY,
            fontName="Helvetica-Bold",
            spaceAfter=6,
            spaceBefore=0,
        ),
        "sig_subheading_first": ParagraphStyle(
            name="DocSigSubFirst",
            parent=styles["Normal"],
            fontSize=10,
            textColor=COLOR_TEXT,
            fontName="Helvetica-Bold",
            spaceBefore=4,
            spaceAfter=4,
        ),
        "sig_subheading_next": ParagraphStyle(
            name="DocSigSubNext",
            parent=styles["Normal"],
            fontSize=10,
            textColor=COLOR_TEXT,
            fontName="Helvetica-Bold",
            spaceBefore=14,
            spaceAfter=4,
        ),
        "sig_line": ParagraphStyle(
            name="DocSigLine",
            parent=styles["Normal"],
            fontSize=10,
            textColor=COLOR_TEXT,
            spaceBefore=0,
            spaceAfter=3,
            leftIndent=18,
        ),
    }


def _merge_pdf_parts(parts: list[bytes]) -> bytes:
    """Fusionne plusieurs documents PDF (chaque élément = octets d'un PDF)."""
    writer = PdfWriter()
    for part in parts:
        if not part:
            continue
        reader = PdfReader(BytesIO(part))
        for page in reader.pages:
            writer.add_page(page)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def _read_attached_pdf_bytes(filefield) -> bytes:
    """Lit le fichier joint ; lève ValueError si le PDF est illisible."""
    filefield.open("rb")
    try:
        raw = filefield.read()
    finally:
        filefield.close()
    try:
        PdfReader(BytesIO(raw))
    except Exception as exc:
        raise ValueError("Le fichier PDF joint est invalide ou corrompu.") from exc
    return raw


def _signatures_flowables(instance) -> list | None:
    """
    Flowables ReportLab pour l’annexe « Signatures électroniques » (bailleur puis locataires).
    Retourne None s’il n’y a aucune signature.
    """
    from .models import SigningInvitation

    signatures = list(_signatures_for_document(instance))
    if not signatures:
        return None

    landlord_sigs = [s for s in signatures if s.invitation.role == SigningInvitation.ROLE_LANDLORD]
    tenant_sigs = [s for s in signatures if s.invitation.role == SigningInvitation.ROLE_TENANT]

    styles = _doc_styles()
    flowables: list = [
        Spacer(1, 0.35 * cm),
        Paragraph("Signatures électroniques", styles["sig_section_title"]),
    ]

    if landlord_sigs:
        flowables.append(Paragraph("Bailleur", styles["sig_subheading_first"]))
        for sig in landlord_sigs:
            date_str = sig.signed_at.strftime("%d/%m/%Y à %H:%M")
            flowables.append(
                Paragraph(
                    f"✓ <b>{sig.signatory_name}</b> — {sig.invitation.get_role_display()} — le {date_str}",
                    styles["sig_line"],
                )
            )

    if tenant_sigs:
        sub_style = styles["sig_subheading_first"] if not landlord_sigs else styles["sig_subheading_next"]
        flowables.append(Paragraph("Locataires", sub_style))
        for sig in tenant_sigs:
            date_str = sig.signed_at.strftime("%d/%m/%Y à %H:%M")
            flowables.append(
                Paragraph(
                    f"✓ <b>{sig.signatory_name}</b> — {sig.invitation.get_role_display()} — le {date_str}",
                    styles["sig_line"],
                )
            )

    return flowables


def _build_signatures_pdf_bytes(instance) -> bytes | None:
    """PDF d’une page (ou plus) listant uniquement les signatures électroniques."""
    flowables = _signatures_flowables(instance)
    if not flowables:
        return None
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.8 * cm,
        leftMargin=1.8 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.8 * cm,
    )
    doc.build([KeepTogether(flowables)])
    return buffer.getvalue()


def _lease_body_flowables(lease, supplement_only: bool) -> list:
    """Contenu bail généré par SyLoc (sans signatures). supplement_only = texte après un PDF joint."""
    prop = lease.property
    styles = _doc_styles()
    elements = []

    if supplement_only:
        elements.append(Paragraph("Texte complémentaire (SyLoc)", styles["title"]))
    else:
        elements.append(Paragraph(
            f"SyLoc – Bail — {_xml_text(prop.name)}",
            styles["title"],
        ))

    elements.append(Paragraph(
        f"<b>Bien :</b> {_xml_text(prop.name)} – {_xml_text(lease.tenants_display)}",
        styles["body"],
    ))
    end_str = lease.end_date.strftime("%d/%m/%Y") if lease.end_date else "non précisée"
    elements.append(Paragraph(
        f"<b>Période :</b> du {lease.start_date.strftime('%d/%m/%Y')} au {end_str}",
        styles["body"],
    ))
    elements.append(Spacer(1, 0.5 * cm))

    if lease.lease_document:
        for block in (lease.lease_document or "").split("\n\n"):
            if block.strip():
                elements.append(Paragraph(block.strip().replace("\n", "<br/>"), styles["body"]))
    else:
        if supplement_only:
            elements.append(Paragraph("(Aucun texte complémentaire.)", styles["body"]))
        else:
            elements.append(Paragraph("(Aucun texte de bail saisi.)", styles["body"]))

    return elements


def _lease_simple_doc_template(buffer: BytesIO, lease) -> SimpleDocTemplate:
    """Modèle PDF bail avec métadonnées (titre / auteur)."""
    prop = lease.property
    owner = prop.owner
    owner_display = (
        (getattr(owner, "get_full_name", None) and owner.get_full_name())
        or getattr(owner, "username", "")
        or (owner.email or "—")
    )
    owner_name_meta = owner_display if owner_display != "—" else "SyLoc"
    pdf_title = f"Bail {prop.name} — {lease.start_date}"
    return SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.8 * cm,
        leftMargin=1.8 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.8 * cm,
        title=_pdf_info_string(pdf_title),
        author=_pdf_info_string(owner_name_meta),
        creator="SyLoc",
    )


def _build_lease_body_rl_pdf(lease, supplement_only: bool) -> bytes:
    """Contenu bail généré par SyLoc (sans signatures). supplement_only = texte après un PDF joint."""
    buffer = BytesIO()
    doc = _lease_simple_doc_template(buffer, lease)
    doc.build(_lease_body_flowables(lease, supplement_only))
    return buffer.getvalue()


def _build_lease_supplement_and_signatures_pdf(lease) -> bytes:
    """
    Un seul PDF : texte complémentaire + signatures électroniques.
    Évite une page ReportLab dédiée au seul complément puis une autre aux seules signatures
    (PDF joint + complément + signatures passent à 2 pages au lieu de 3).
    """
    buffer = BytesIO()
    doc = _lease_simple_doc_template(buffer, lease)
    sup = _lease_body_flowables(lease, supplement_only=True)
    sig_flowables = _signatures_flowables(lease)
    if sig_flowables:
        story = sup + [Spacer(1, 0.55 * cm), KeepTogether(sig_flowables)]
    else:
        story = sup
    doc.build(story)
    return buffer.getvalue()


def _inspection_body_flowables(inspection, supplement_only: bool) -> list:
    """Contenu état des lieux généré par SyLoc (sans signatures)."""
    styles = _doc_styles()
    elements = []

    if supplement_only:
        elements.append(Paragraph("Observations complémentaires (SyLoc)", styles["title"]))
    else:
        elements.append(Paragraph("SyLoc – État des lieux", styles["title"]))

    elements.append(Paragraph(
        f"<b>Bail :</b> {inspection.lease.property.name} – {inspection.lease.tenants_display}",
        styles["body"],
    ))
    elements.append(Paragraph(
        f"<b>Type :</b> {inspection.get_report_type_display()} – <b>Date :</b> {inspection.report_date}",
        styles["body"],
    ))
    elements.append(Spacer(1, 0.5 * cm))

    if inspection.notes:
        for block in (inspection.notes or "").split("\n\n"):
            if block.strip():
                elements.append(Paragraph(block.strip().replace("\n", "<br/>"), styles["body"]))
    else:
        if supplement_only:
            elements.append(Paragraph("(Aucune observation complémentaire.)", styles["body"]))
        else:
            elements.append(Paragraph("(Aucune observation.)", styles["body"]))

    return elements


def _inspection_simple_doc_template(buffer: BytesIO) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.8 * cm,
        leftMargin=1.8 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.8 * cm,
    )


def _build_inspection_body_rl_pdf(inspection, supplement_only: bool) -> bytes:
    """Contenu état des lieux généré par SyLoc (sans signatures)."""
    buffer = BytesIO()
    doc = _inspection_simple_doc_template(buffer)
    doc.build(_inspection_body_flowables(inspection, supplement_only))
    return buffer.getvalue()


def _build_inspection_supplement_and_signatures_pdf(inspection) -> bytes:
    """Un seul PDF : observations complémentaires + signatures (même logique que le bail)."""
    buffer = BytesIO()
    doc = _inspection_simple_doc_template(buffer)
    sup = _inspection_body_flowables(inspection, supplement_only=True)
    sig_flowables = _signatures_flowables(inspection)
    if sig_flowables:
        story = sup + [Spacer(1, 0.55 * cm), KeepTogether(sig_flowables)]
    else:
        story = sup
    doc.build(story)
    return buffer.getvalue()


def build_lease_pdf(lease) -> bytes:
    """
    Construit le PDF du bail : PDF joint éventuel, texte SyLoc, puis signatures électroniques.
    Si un PDF est joint et qu’un texte complémentaire existe, fusionne ce texte et les signatures
    dans un seul PDF (évite une page uniquement pour le complément puis une pour les signatures).
    Retourne les octets du PDF.
    """
    parts: list[bytes] = []
    attached = getattr(lease, "attached_file", None)
    if attached:
        parts.append(_read_attached_pdf_bytes(attached))
        if (lease.lease_document or "").strip():
            parts.append(_build_lease_supplement_and_signatures_pdf(lease))
        else:
            sig = _build_signatures_pdf_bytes(lease)
            if sig:
                parts.append(sig)
    else:
        parts.append(_build_lease_body_rl_pdf(lease, supplement_only=False))
        sig = _build_signatures_pdf_bytes(lease)
        if sig:
            parts.append(sig)
    return _merge_pdf_parts(parts)


def build_inspection_pdf(inspection) -> bytes:
    """
    Construit le PDF de l'état des lieux : PDF joint éventuel, texte SyLoc, puis signatures.
    Même fusion complément + signatures que pour le bail lorsqu’un PDF est joint.
    Retourne les octets du PDF.
    """
    parts: list[bytes] = []
    attached = getattr(inspection, "attached_file", None)
    if attached:
        parts.append(_read_attached_pdf_bytes(attached))
        if (inspection.notes or "").strip():
            parts.append(_build_inspection_supplement_and_signatures_pdf(inspection))
        else:
            sig = _build_signatures_pdf_bytes(inspection)
            if sig:
                parts.append(sig)
    else:
        parts.append(_build_inspection_body_rl_pdf(inspection, supplement_only=False))
        sig = _build_signatures_pdf_bytes(inspection)
        if sig:
            parts.append(sig)
    return _merge_pdf_parts(parts)


def lease_attachment_filename(lease) -> str:
    """Nom de fichier ASCII pour le téléchargement du PDF bail (évite en-têtes HTTP invalides)."""
    from django.utils.text import slugify

    part = slugify(lease.property.name) or "bien"
    return f"bail_{part}_{lease.start_date.isoformat()}.pdf"


def quittance_attachment_filename(invoice, tenant=None) -> str:
    """
    Nom de fichier ASCII pour le PDF quittance.
    Si tenant est fourni et colocation : suffixe locataire ; sinon quittance globale.
    """
    from django.utils.text import slugify

    prop = slugify(invoice.lease.property.name) or "bien"
    due = invoice.due_date
    ym = f"{due.year:04d}_{due.month:02d}"
    if tenant is not None and invoice.lease.tenants.count() > 1:
        tpart = slugify(tenant.last_name or tenant.first_name or "locataire") or "locataire"
        return f"quittance_{prop}_{ym}_{tpart}.pdf"
    return f"quittance_{prop}_{ym}.pdf"


def inspection_attachment_filename(inspection) -> str:
    """Nom de fichier ASCII pour le PDF état des lieux."""
    from django.utils.text import slugify

    prop = slugify(inspection.lease.property.name) or "bien"
    return f"etat_des_lieux_{prop}_{inspection.report_date.isoformat()}.pdf"
