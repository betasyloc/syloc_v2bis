from __future__ import annotations

import builtins
import hashlib
import secrets
import uuid
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import F, Q, Sum
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

_property = builtins.property  # évite conflit avec le champ Lease.property


class Plan(models.Model):
    """Offre d'abonnement : essai gratuit, basic (payant) ou Premium (payant)."""
    FREE = "free"
    BASE = "base"
    PREMIUM = "premium"
    SLUG_CHOICES = [
        (FREE, "Essai gratuit"),
        (BASE, "basic"),
        (PREMIUM, "Premium"),
    ]

    name = models.CharField(max_length=50)
    slug = models.SlugField(max_length=20, unique=True)
    description = models.TextField(blank=True)
    price_display = models.CharField(max_length=50, blank=True, help_text="Ex. 9 €/mois")
    stripe_price_id = models.CharField(max_length=100, blank=True, help_text="ID du prix Stripe mensuel (price_xxx)")
    stripe_price_id_annual = models.CharField(max_length=100, blank=True, help_text="ID du prix Stripe annuel (price_xxx)")

    class Meta:
        ordering = ["id"]
        verbose_name = "Offre"
        verbose_name_plural = "Offres"

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        m = (self.stripe_price_id or "").strip()
        a = (self.stripe_price_id_annual or "").strip()
        if m and a and m == a:
            raise ValidationError(
                {
                    "stripe_price_id_annual": (
                        "Le prix annuel doit être un autre ID Stripe (price_…) que le mensuel, "
                        "comme dans votre tableau de bord Stripe."
                    )
                }
            )

    @classmethod
    def get_free(cls):
        return cls.objects.get(slug=cls.FREE)

    @classmethod
    def get_base(cls):
        return cls.objects.get(slug=cls.BASE)

    @classmethod
    def get_premium(cls):
        return cls.objects.get(slug=cls.PREMIUM)


class UserProfile(models.Model):
    """Profil utilisateur : lien vers l'offre (basic / Premium)."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name="user_profiles",
    )
    stripe_customer_id = models.CharField(max_length=100, blank=True)
    stripe_subscription_id = models.CharField(max_length=100, blank=True)
    demo_seeded = models.BooleanField(
        default=False,
        help_text="Données de démonstration créées pour l'essai gratuit",
    )

    class Meta:
        verbose_name = "Profil utilisateur"
        verbose_name_plural = "Profils utilisateur"

    def __str__(self):
        return f"{self.user} – {self.plan.name}"

    @property
    def is_premium(self):
        return self.plan.slug == Plan.PREMIUM


class ApiKey(models.Model):
    """Clé API pour l'accès programmatique (Premium). Une clé par utilisateur ; stockée hachée."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_key",
    )
    key_hash = models.CharField(max_length=64, unique=True, help_text="SHA256 de la clé complète")
    key_prefix = models.CharField(max_length=20, help_text="Préfixe affiché (ex: syloc_abc1)")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Clé API"
        verbose_name_plural = "Clés API"

    def __str__(self):
        return f"{self.key_prefix}… ({self.user})"

    @classmethod
    def hash_key(cls, raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()

    @classmethod
    def create_key_for_user(cls, user):
        """Génère une nouvelle clé pour l'utilisateur (remplace l'ancienne si présente). Retourne (instance, raw_key)."""
        prefix = "syloc_" + secrets.token_hex(4)
        secret = secrets.token_urlsafe(24)
        raw_key = f"{prefix}_{secret}"
        key_hash = cls.hash_key(raw_key)
        instance, _ = cls.objects.update_or_create(
            user=user,
            defaults={"key_hash": key_hash, "key_prefix": prefix},
        )
        return instance, raw_key

    @classmethod
    def get_user_for_key(cls, raw_key: str):
        """Retourne l'utilisateur associé à la clé ou None."""
        if not raw_key or len(raw_key) < 20:
            return None
        key_hash = cls.hash_key(raw_key)
        try:
            return cls.objects.get(key_hash=key_hash).user
        except cls.DoesNotExist:
            return None


class Organisation(models.Model):
    """Organisation (compte partagé) : biens et membres avec rôles."""
    name = models.CharField(max_length=150, help_text="Nom de l'organisation (ex : SCI Dupont)")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Organisation"
        verbose_name_plural = "Organisations"

    def __str__(self):
        return self.name


class OrganisationMember(models.Model):
    """Appartenance d'un utilisateur à une organisation avec un rôle."""
    ROLE_OWNER = "owner"
    ROLE_MEMBER = "member"
    ROLE_CHOICES = [(ROLE_OWNER, "Propriétaire"), (ROLE_MEMBER, "Membre")]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organisation_members",
    )
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name="members",
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_MEMBER)

    class Meta:
        unique_together = [("user", "organisation")]
        verbose_name = "Membre d'organisation"
        verbose_name_plural = "Membres d'organisation"

    def __str__(self):
        return f"{self.user} – {self.organisation} ({self.get_role_display()})"

    @property
    def is_owner(self):
        return self.role == self.ROLE_OWNER


class Property(models.Model):
    OWNER_TYPES = [
        ("PERSONNE_PHYSIQUE", "Personne physique"),
        ("LMNP", "LMNP"),
        ("SCI", "SCI"),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="properties",
        help_text="Utilisateur ayant créé le bien (conservé pour historique).",
    )
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name="properties",
        null=True,
        blank=True,
        help_text="Organisation à laquelle appartient le bien (accès multi-utilisateurs).",
    )
    name = models.CharField(max_length=255, help_text="Nom court du bien (ex : Studio Lyon 3)")
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    zip_code = models.CharField(max_length=10, blank=True)
    owner_type = models.CharField(max_length=32, choices=OWNER_TYPES, default="PERSONNE_PHYSIQUE")

    purchase_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    notary_fees = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    monthly_mortgage = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Mensualité de crédit",
    )
    monthly_charges = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Charges mensuelles (copro, assurances, etc.)",
    )

    tags = models.ManyToManyField(
        "PropertyTag",
        related_name="properties",
        blank=True,
        help_text="Tags pour filtrer (LMNP, meublé, etc.).",
    )

    # DPE (Diagnostic de performance énergétique)
    DPE_CLASS_CHOICES = [
        ("", "Non renseigné"),
        ("A", "A"),
        ("B", "B"),
        ("C", "C"),
        ("D", "D"),
        ("E", "E"),
        ("F", "F"),
        ("G", "G"),
    ]
    dpe_class = models.CharField(
        max_length=1,
        choices=DPE_CLASS_CHOICES,
        blank=True,
        help_text="Classe énergétique du DPE (A à G).",
    )
    dpe_valid_until = models.DateField(
        null=True,
        blank=True,
        help_text="Date de fin de validité du DPE (optionnel, ex. validité 10 ans).",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    archived_at = models.DateTimeField(null=True, blank=True, help_text="Si renseigné, le bien est archivé")

    class Meta:
        ordering = ["name"]
        verbose_name = "Bien"
        verbose_name_plural = "Biens"

    def __str__(self) -> str:
        return self.name

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class Tenant(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tenants",
        help_text="Utilisateur ayant créé le locataire.",
    )
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name="tenants",
        null=True,
        blank=True,
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name = "Locataire"
        verbose_name_plural = "Locataires"

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class Lease(models.Model):
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="leases")
    tenants = models.ManyToManyField(
        Tenant,
        related_name="leases",
        blank=True,
        help_text="Un ou plusieurs locataires (colocation possible).",
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)

    rent = models.DecimalField(max_digits=10, decimal_places=2)
    charges = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    deposit = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    lease_document = models.TextField(
        blank=True,
        help_text="Texte du bail (rédaction et mise à jour des clauses)",
    )
    attached_file = models.FileField(
        upload_to="leases/attachments/",
        blank=True,
        validators=[FileExtensionValidator(["pdf"])],
        help_text="Optionnel : joindre votre bail au format PDF (au lieu ou en complément du texte ci-dessus).",
    )
    is_active = models.BooleanField(default=True)

    rent_payment_iban = models.CharField(
        max_length=34,
        blank=True,
        verbose_name="IBAN",
        help_text="Coordonnées pour le virement du loyer, affichées sur le portail locataire (sans espaces ou avec espaces).",
    )
    rent_payment_bic = models.CharField(
        max_length=11,
        blank=True,
        verbose_name="BIC / SWIFT",
        help_text="Optionnel ; utile pour un virement international.",
    )
    rent_payment_holder = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Titulaire du compte",
        help_text="Nom affiché au locataire (optionnel).",
    )
    tenant_insurance_valid_until = models.DateField(
        null=True,
        blank=True,
        help_text="Fin de validité de l'assurance habitation du locataire (rappel dans le portail).",
    )
    lease_renewal_reminder_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date à rappeler au locataire (ex. échéance de reconduction ou fin de préavis).",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-start_date"]
        verbose_name = "Bail"
        verbose_name_plural = "Baux"

    def __str__(self) -> str:
        return f"{self.property} - {self.tenants_display}"

    @_property
    def tenants_display(self) -> str:
        """Affiche les noms des locataires (un ou plusieurs, colocation)."""
        qs = self.tenants.all()
        if qs.exists():
            return ", ".join(str(t) for t in qs)
        return "?"

    @_property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @_property
    def rent_payment_iban_display(self) -> str:
        """IBAN normalisé, groupé par blocs de 4 caractères pour affichage locataire."""
        raw = (self.rent_payment_iban or "").replace(" ", "").upper()
        if not raw:
            return ""
        return " ".join(raw[i : i + 4] for i in range(0, len(raw), 4))


class RentInvoice(models.Model):
    STATUS_CHOICES = [
        ("DUE", "À payer"),
        ("PAID", "Payé"),
        ("LATE", "En retard"),
    ]

    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, related_name="rent_invoices")
    due_date = models.DateField()
    amount_rent = models.DecimalField(max_digits=10, decimal_places=2)
    amount_charges = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="DUE")
    paid_date = models.DateField(null=True, blank=True)

    reminder_sent_at = models.DateTimeField(
        null=True, blank=True, help_text="Date d'envoi du rappel avant échéance"
    )
    last_late_reminder_sent_at = models.DateTimeField(
        null=True, blank=True, help_text="Dernière relance impayé envoyée"
    )
    late_reminder_last_days_after_due = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Dernier palier de relance envoyé (7, 15 ou 30 = J+N après échéance)",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-due_date"]
        unique_together = ("lease", "due_date")
        verbose_name = "Loyer"
        verbose_name_plural = "Loyers"

    def __str__(self) -> str:
        return f"{self.lease} - {self.due_date}"

    @property
    def total_amount(self):
        return (self.amount_rent or 0) + (self.amount_charges or 0)


class InspectionReport(models.Model):
    """État des lieux d'entrée ou de sortie lié à un bail."""
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    TYPE_CHOICES = [
        (ENTRY, "Entrée"),
        (EXIT, "Sortie"),
    ]

    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, related_name="inspection_reports")
    report_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    report_date = models.DateField()
    notes = models.TextField(blank=True, help_text="Observations, état des pièces, etc.")
    attached_file = models.FileField(
        upload_to="inspections/attachments/",
        blank=True,
        validators=[FileExtensionValidator(["pdf"])],
        help_text="Optionnel : joindre un état des lieux au format PDF (au lieu ou en complément des observations).",
    )
    shared_with_portal = models.BooleanField(
        default=False,
        help_text="Si coché, le locataire peut télécharger ce PDF depuis son portail (lien sécurisé).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-report_date"]
        verbose_name = "État des lieux"
        verbose_name_plural = "États des lieux"

    def __str__(self) -> str:
        return f"{self.lease} - {self.get_report_type_display()} - {self.report_date}"


class LeaseTemplate(models.Model):
    """Modèle de bail réutilisable que le bailleur peut personnaliser."""
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="lease_templates",
    )
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name="lease_templates",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=200, help_text="Nom du modèle (ex : Bail type 3 ans)")
    content = models.TextField(
        blank=True,
        help_text="Texte type du bail. Vous pouvez le modifier selon vos besoins.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Modèle de bail"
        verbose_name_plural = "Modèles de bail"

    def __str__(self) -> str:
        return self.name


class InspectionTemplate(models.Model):
    """Modèle d'état des lieux réutilisable que le bailleur peut personnaliser."""
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="inspection_templates",
    )
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name="inspection_templates",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=200, help_text="Nom du modèle (ex : État des lieux type)")
    content = models.TextField(
        blank=True,
        help_text="Texte type (sections, points à vérifier). Vous pouvez le modifier.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Modèle d'état des lieux"
        verbose_name_plural = "Modèles d'état des lieux"

    def __str__(self) -> str:
        return self.name


class Announcement(models.Model):
    """Annonce ou message affiché dans l'onglet Notifications (nouveautés, maintenance, etc.)."""
    LEVEL_INFO = "info"
    LEVEL_WARNING = "warning"
    LEVEL_CHOICES = [(LEVEL_INFO, "Information"), (LEVEL_WARNING, "Attention")]

    title = models.CharField(max_length=200, verbose_name="Titre")
    message = models.TextField(verbose_name="Message", help_text="Contenu affiché aux utilisateurs.")
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default=LEVEL_INFO)
    is_published = models.BooleanField(default=True, verbose_name="Publié")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Annonce"
        verbose_name_plural = "Annonces"

    def __str__(self):
        return self.title


class AdminActionLog(models.Model):
    """Journal des actions effectuées dans l'admin Django (date/heure + commentaire explicite)."""
    ACTION_ADD = "add"
    ACTION_CHANGE = "change"
    ACTION_DELETE = "delete"
    ACTION_CHOICES = [
        (ACTION_ADD, "Ajout"),
        (ACTION_CHANGE, "Modification"),
        (ACTION_DELETE, "Suppression"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="admin_action_logs",
    )
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    content_type = models.ForeignKey(
        "contenttypes.ContentType",
        on_delete=models.SET_NULL,
        null=True,
    )
    object_id = models.PositiveIntegerField(null=True, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    comment = models.TextField(blank=True, verbose_name="Commentaire explicite de l'action")

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Action admin"
        verbose_name_plural = "Actions admin"

    def __str__(self):
        return f"{self.get_action_display()} – {self.object_repr or '(objet)'} – {self.timestamp}"


# --- Signature électronique (baux et états des lieux) ---


def signature_upload_path(instance, filename):
    """Stocke les images de signature dans signatures/année/mois/uuid.png"""
    ext = "png" if filename.lower().endswith(".png") else "jpg"
    return f"signatures/{timezone.now().year}/{timezone.now().month:02d}/{uuid.uuid4().hex}.{ext}"


class SigningInvitation(models.Model):
    """Invitation à signer un document (bail ou état des lieux) via un lien unique."""
    ROLE_LANDLORD = "landlord"
    ROLE_TENANT = "tenant"
    ROLE_CHOICES = [(ROLE_LANDLORD, "Bailleur"), (ROLE_TENANT, "Locataire")]

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    document = GenericForeignKey("content_type", "object_id")

    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="signing_invitations",
        help_text="Locataire concerné (pour rôle locataire).",
    )
    email = models.EmailField(help_text="Email auquel le lien de signature est envoyé.")
    token = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    signed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Invitation à signer"
        verbose_name_plural = "Invitations à signer"
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self):
        return f"{self.get_role_display()} – {self.email} – {self.content_type.model} #{self.object_id}"

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def is_signed(self):
        return self.signed_at is not None

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = uuid.uuid4().hex
        super().save(*args, **kwargs)


class DocumentSignature(models.Model):
    """Signature électronique : document signé par le signataire (nom + date, pas d'image)."""
    invitation = models.OneToOneField(
        SigningInvitation,
        on_delete=models.CASCADE,
        related_name="signature",
    )
    signatory_name = models.CharField(max_length=200, verbose_name="Nom du signataire")
    signature_image = models.ImageField(
        upload_to=signature_upload_path,
        verbose_name="Signature (image)",
        blank=True,
        null=True,
        help_text="Optionnel ; non utilisé en mode validation simple.",
    )
    signed_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True, unpack_ipv4=True)

    class Meta:
        ordering = ["-signed_at"]
        verbose_name = "Signature de document"
        verbose_name_plural = "Signatures de documents"

    def __str__(self):
        return f"{self.signatory_name} – {self.signed_at}"


# --- Synchronisation bancaire (Premium) ---


class BankAccount(models.Model):
    """Compte bancaire pour le rapprochement des encaissements (relevés importés ou future API)."""
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name="bank_accounts",
        null=True,
        blank=True,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bank_accounts",
        help_text="Propriétaire du compte (si pas d'organisation).",
    )
    name = models.CharField(max_length=150, help_text="Ex. Compte courant BNP")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Compte bancaire"
        verbose_name_plural = "Comptes bancaires"

    def __str__(self):
        return self.name


class BankTransaction(models.Model):
    """Opération bancaire importée (CSV) ; peut être rapprochée avec un loyer."""
    account = models.ForeignKey(
        BankAccount,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text="Montant (positif = entrée, négatif = sortie)")
    label = models.CharField(max_length=500, blank=True)
    rent_invoice = models.ForeignKey(
        RentInvoice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bank_transactions",
        help_text="Loyer rapproché avec cette opération.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "Opération bancaire"
        verbose_name_plural = "Opérations bancaires"

    def __str__(self):
        return f"{self.date} – {self.amount} € – {self.label[:50]}"

    @property
    def is_matched(self):
        return self.rent_invoice_id is not None


def bank_accounts_visible_to(user):
    """Comptes bancaires visibles par l'utilisateur (owner ou membre de l'organisation)."""
    return BankAccount.objects.filter(
        Q(owner=user)
        | Q(
            organisation__isnull=False,
            organisation__members__user=user,
        )
    ).distinct()


def get_organisations_for_user(user):
    """Retourne les organisations dont l'utilisateur est membre."""
    return Organisation.objects.filter(members__user=user)


def properties_visible_to(user):
    """Biens visibles : les siens (y compris personnel sans org.) + biens d'équipe (organisation renseignée)."""
    return Property.objects.filter(
        Q(owner=user)
        | Q(
            organisation__isnull=False,
            organisation__members__user=user,
        )
    ).distinct()


def tenants_visible_to(user):
    """Locataires visibles : les siens (perso) + locataires rattachés à une org. dont l'utilisateur est membre."""
    return Tenant.objects.filter(
        Q(owner=user)
        | Q(
            organisation__isnull=False,
            organisation__members__user=user,
        )
    ).distinct()


def leases_visible_to(user):
    """Baux visibles via le bien (perso du propriétaire ou bien d'équipe avec org. renseignée)."""
    return Lease.objects.filter(
        Q(property__owner=user)
        | Q(
            property__organisation__isnull=False,
            property__organisation__members__user=user,
        )
    ).distinct()


def lease_templates_visible_to(user):
    """Modèles de bail visibles par l'utilisateur."""
    return LeaseTemplate.objects.filter(
        Q(owner=user)
        | Q(
            organisation__isnull=False,
            organisation__members__user=user,
        )
    ).distinct()


def inspection_templates_visible_to(user):
    """Modèles d'état des lieux visibles par l'utilisateur."""
    return InspectionTemplate.objects.filter(
        Q(owner=user)
        | Q(
            organisation__isnull=False,
            organisation__members__user=user,
        )
    ).distinct()


def letter_templates_visible_to(user):
    """Modèles de lettre (Premium) créés par l'utilisateur."""
    return LetterTemplate.objects.filter(owner=user)


def get_or_create_default_organisation(user):
    """Retourne l'organisation « par défaut » de l'utilisateur (celle dont il est owner), ou la crée."""
    from django.db.models import Q
    member = OrganisationMember.objects.filter(user=user, role=OrganisationMember.ROLE_OWNER).first()
    if member:
        return member.organisation
    name = user.get_username() or getattr(user, "email", "") or "Mon organisation"
    org = Organisation.objects.create(name=name[:150])
    OrganisationMember.objects.create(user=user, organisation=org, role=OrganisationMember.ROLE_OWNER)
    return org


def compute_global_cashflow_for_user(user):
    """Calcule un cashflow mensuel simple pour l'utilisateur courant."""
    today = timezone.now().date()
    month_start = today.replace(day=1)
    visible_property_ids = properties_visible_to(user).filter(archived_at__isnull=True).values_list("pk", flat=True)
    rents = (
        RentInvoice.objects.filter(
            lease__property_id__in=visible_property_ids,
            due_date__gte=month_start,
            due_date__lte=today,
            status="PAID",
        ).aggregate(total=Sum(F("amount_rent") + F("amount_charges")))["total"]
        or 0
    )
    # S'assurer que rents est numérique (Decimal/int) pour la soustraction
    if rents is None:
        rents = 0
    fixed_costs = 0
    properties = Property.objects.filter(pk__in=visible_property_ids)
    for prop in properties:
        if prop.monthly_mortgage:
            fixed_costs += float(prop.monthly_mortgage)
        if prop.monthly_charges:
            fixed_costs += float(prop.monthly_charges)

    return (float(rents) if rents else 0) - fixed_costs


# --- Rapports, rappels, documents, conformité, portail, intégrations, audit ---


class PropertyTag(models.Model):
    """Tag ou catégorie pour filtrer les biens (ex : LMNP, meublé, ville)."""
    name = models.CharField(max_length=50, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Tag bien"
        verbose_name_plural = "Tags biens"

    def __str__(self):
        return self.name


# Libellés proposés par défaut (migration + formulaire bien). Garder la migration alignée si vous modifiez cette liste.
STANDARD_PROPERTY_TAG_NAMES = (
    "Meublé",
    "Nu",
    "Coloc",
    "Copro",
    "Parking",
    "Travaux",
    "Vacant",
    "Saison",
    "Mandat",
    "Etudiants",
)


def ensure_standard_property_tags():
    """Crée les tags standards s'ils manquent (formulaire bien, env sans migration données)."""
    for name in STANDARD_PROPERTY_TAG_NAMES:
        PropertyTag.objects.get_or_create(name=name)


class PropertyDiagnostic(models.Model):
    """Diagnostic ou DPE : suivi des dates de validité (Premium)."""
    TYPE_DPE = "DPE"
    TYPE_AMIANTE = "AMIANTE"
    TYPE_ELECTRICITE = "ELECTRICITE"
    TYPE_GAZ = "GAZ"
    TYPE_ERGONOMIE = "ERGONOMIE"
    TYPE_CHOICES = [
        (TYPE_DPE, "DPE"),
        (TYPE_AMIANTE, "Amiante"),
        (TYPE_ELECTRICITE, "Électricité"),
        (TYPE_GAZ, "Gaz"),
        (TYPE_ERGONOMIE, "Ergonomie"),
    ]
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="diagnostics")
    diagnostic_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    done_date = models.DateField(help_text="Date du diagnostic")
    expiry_date = models.DateField(null=True, blank=True, help_text="Date de fin de validité")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-expiry_date", "-done_date"]
        verbose_name = "Diagnostic"
        verbose_name_plural = "Diagnostics"

    def __str__(self):
        return f"{self.property} – {self.get_diagnostic_type_display()}"


class ReminderRule(models.Model):
    """Règle de relance pour loyers en retard (J+7, J+15, J+30) (Premium)."""
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name="reminder_rules",
        null=True,
        blank=True,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reminder_rules",
    )
    days_after_due = models.PositiveSmallIntegerField(help_text="Jours après l'échéance (ex : 7, 15, 30)")
    email_subject = models.CharField(max_length=200, default="Rappel – loyer à régler")
    email_body_template = models.TextField(
        blank=True,
        help_text="Corps du mail. Variables : {{tenant}}, {{amount}}, {{due_date}}, {{property}}",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["days_after_due"]
        verbose_name = "Règle de relance"
        verbose_name_plural = "Règles de relance"
        unique_together = [("owner", "days_after_due")]

    def __str__(self):
        return f"Relance J+{self.days_after_due}"


class LetterTemplate(models.Model):
    """Modèle de lettre type (mise en demeure, résiliation, etc.) (Premium)."""
    MISE_EN_DEMEURE = "MISE_EN_DEMEURE"
    RESILIATION = "RESILIATION"
    AVENANT = "AVENANT"
    PREAVIS = "PREAVIS"
    TYPE_CHOICES = [
        (MISE_EN_DEMEURE, "Mise en demeure"),
        (RESILIATION, "Résiliation"),
        (AVENANT, "Avenant"),
        (PREAVIS, "Préavis"),
    ]
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="letter_templates",
    )
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name="letter_templates",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=200)
    letter_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    content = models.TextField(
        help_text=(
            "Texte du modèle ; les modèles types illustrent les champs dynamiques disponibles."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Modèle de lettre"
        verbose_name_plural = "Modèles de lettre"

    def __str__(self):
        return self.name


class StoredDocument(models.Model):
    """Document attaché à un bien ou un locataire (historique) (Premium)."""
    DOC_BAIL = "BAIL"
    DOC_QUITTANCE = "QUITTANCE"
    DOC_ETAT_LIEUX = "ETAT_LIEUX"
    DOC_AVENANT = "AVENANT"
    DOC_REGLEMENT_COPRO = "REGLEMENT_COPRO"
    DOC_ASSURANCE = "ASSURANCE"
    DOC_CONVOCATION_AG = "CONVOCATION_AG"
    DOC_LETTRE = "LETTRE"
    DOC_OTHER = "OTHER"
    TYPE_CHOICES = [
        (DOC_BAIL, "Bail"),
        (DOC_QUITTANCE, "Quittance"),
        (DOC_ETAT_LIEUX, "État des lieux"),
        (DOC_AVENANT, "Avenant"),
        (DOC_REGLEMENT_COPRO, "Règlement de copropriété"),
        (DOC_ASSURANCE, "Notice / attestation d'assurance"),
        (DOC_CONVOCATION_AG, "Convocation AG"),
        (DOC_LETTRE, "Lettre"),
        (DOC_OTHER, "Autre"),
    ]
    property = models.ForeignKey(
        Property,
        on_delete=models.CASCADE,
        related_name="stored_documents",
        null=True,
        blank=True,
    )
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="stored_documents",
        null=True,
        blank=True,
    )
    document_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to="documents/%Y/%m/", blank=True, null=True)
    notes = models.TextField(blank=True)
    shared_with_portal = models.BooleanField(
        default=False,
        verbose_name="Visible sur le portail locataire",
        help_text="Le locataire concerné (ou lié au bien du bail) pourra télécharger ce fichier depuis son portail.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Document stocké"
        verbose_name_plural = "Documents stockés"

    def __str__(self):
        return self.title


def stored_documents_visible_to(user):
    """Documents stockés liés à un bien ou locataire visibles par l'utilisateur."""
    return StoredDocument.objects.filter(
        Q(property__in=properties_visible_to(user)) | Q(tenant__in=tenants_visible_to(user))
    ).distinct()


class Webhook(models.Model):
    """Webhook pour notifier une URL externe sur des événements (Premium)."""
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="webhooks",
    )
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.CASCADE,
        related_name="webhooks",
        null=True,
        blank=True,
    )
    url = models.URLField(max_length=500, help_text="URL qui recevra les POST")
    event_types = models.CharField(
        max_length=500,
        help_text="Événements (séparés par des virgules) : rent_paid, lease_ended, invoice_created, etc.",
    )
    secret = models.CharField(max_length=64, blank=True, help_text="Clé pour signer le payload")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Webhook"
        verbose_name_plural = "Webhooks"

    def __str__(self):
        return self.url[:50]


class UserActivityLog(models.Model):
    """Journal d'activité utilisateur (création, modification, suppression) pour audit (Premium)."""
    ACTION_CREATE = "create"
    ACTION_UPDATE = "update"
    ACTION_DELETE = "delete"
    ACTION_VIEW = "view"
    ACTION_CHOICES = [
        (ACTION_CREATE, "Création"),
        (ACTION_UPDATE, "Modification"),
        (ACTION_DELETE, "Suppression"),
        (ACTION_VIEW, "Consultation"),
    ]
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="activity_logs",
    )
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
    )
    object_id = models.PositiveIntegerField(null=True, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)
    details = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True, unpack_ipv4=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Activité utilisateur"
        verbose_name_plural = "Activités utilisateur"

    def __str__(self):
        return f"{self.user} – {self.get_action_display()} – {self.object_repr or '?'}"


class UserSuggestion(models.Model):
    """Suggestion utilisateur (UX, perf, contenu, SEO) depuis le tableau de bord."""

    THEME_UX = "ux"
    THEME_PERFORMANCE = "performance"
    THEME_CONTENT = "content"
    THEME_SEO = "seo"
    THEME_OTHER = "other"
    THEME_CHOICES = [
        (THEME_UX, "Expérience utilisateur (UX)"),
        (THEME_PERFORMANCE, "Performance technique"),
        (THEME_CONTENT, "Contenu & conversion"),
        (THEME_SEO, "Référencement (SEO)"),
        (THEME_OTHER, "Autres"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="suggestions",
    )
    contact_email = models.EmailField(help_text="Email de réponse pour le staff")
    theme = models.CharField(max_length=20, choices=THEME_CHOICES)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Suggestion utilisateur"
        verbose_name_plural = "Suggestions utilisateur"

    def __str__(self) -> str:
        return f"{self.get_theme_display()} – {self.contact_email} – {self.created_at:%Y-%m-%d %H:%M}"


def user_suggestion_attachment_upload(instance, filename):
    """Chemin de stockage des pièces jointes (photos / fichiers)."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    safe = f"{uuid.uuid4().hex}.{ext}"
    return f"user_suggestions/{timezone.now().year}/{timezone.now().month:02d}/{safe}"


class UserSuggestionAttachment(models.Model):
    """Fichier ou photo joint à une suggestion."""

    suggestion = models.ForeignKey(
        UserSuggestion,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.FileField(
        upload_to=user_suggestion_attachment_upload,
        validators=[
            FileExtensionValidator(
                allowed_extensions=["jpg", "jpeg", "png", "gif", "webp", "pdf", "doc", "docx", "txt"]
            )
        ],
    )
    original_name = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "Pièce jointe (suggestion)"
        verbose_name_plural = "Pièces jointes (suggestions)"


class TenantPortalAccess(models.Model):
    """Accès temporaire au portail locataire par token (Premium)."""
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="portal_accesses")
    token = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Accès portail locataire"
        verbose_name_plural = "Accès portail locataire"

    def __str__(self):
        return f"{self.tenant} – {self.token[:8]}…"

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = uuid.uuid4().hex
        super().save(*args, **kwargs)


def _portal_maintenance_attachment_upload(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    safe = f"{uuid.uuid4().hex}.{ext}"
    return f"portal/maintenance/{timezone.now().year}/{timezone.now().month:02d}/{safe}"


class LeasePracticalInfo(models.Model):
    """Infos pratiques affichées aux locataires (digicode, syndic, poubelles, urgences)."""

    lease = models.ForeignKey(
        Lease,
        on_delete=models.CASCADE,
        related_name="practical_infos",
    )
    sort_order = models.PositiveSmallIntegerField(default=0)
    title = models.CharField(max_length=200)
    body = models.TextField(help_text="Texte libre (contacts, codes, horaires, consignes).")

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "Info pratique (portail)"
        verbose_name_plural = "Infos pratiques (portail)"

    def __str__(self):
        return f"{self.lease_id} – {self.title}"


class LeaseAnnouncement(models.Model):
    """Annonce du bailleur pour les locataires d'un bail (travaux, coupures, visites)."""

    lease = models.ForeignKey(
        Lease,
        on_delete=models.CASCADE,
        related_name="lease_announcements",
    )
    title = models.CharField(max_length=200)
    body = models.TextField()
    is_active = models.BooleanField(default=True)
    published_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Laisser vide pour ne pas expirer automatiquement.",
    )

    class Meta:
        ordering = ["-published_at"]
        verbose_name = "Annonce (portail locataire)"
        verbose_name_plural = "Annonces (portail locataire)"

    def __str__(self):
        return self.title

    def is_visible_now(self) -> bool:
        if not self.is_active:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True


class MaintenanceRequest(models.Model):
    """Demande d'intervention déposée par le locataire depuis le portail."""

    STATUS_PENDING = "PENDING"
    STATUS_IN_PROGRESS = "IN_PROGRESS"
    STATUS_DONE = "DONE"
    STATUS_CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Envoyée"),
        (STATUS_IN_PROGRESS, "En cours"),
        (STATUS_DONE, "Terminée"),
        (STATUS_CANCELLED, "Annulée"),
    ]
    CLOSED_STATUSES = (STATUS_DONE, STATUS_CANCELLED)

    CAT_PLUMBING = "plumbing"
    CAT_HEATING = "heating"
    CAT_KEYS = "keys"
    CAT_OTHER = "other"
    CATEGORY_CHOICES = [
        (CAT_PLUMBING, "Fuite / plomberie"),
        (CAT_HEATING, "Chauffage / eau chaude"),
        (CAT_KEYS, "Clés / accès"),
        (CAT_OTHER, "Autre"),
    ]

    lease = models.ForeignKey(
        Lease,
        on_delete=models.CASCADE,
        related_name="maintenance_requests",
    )
    submitted_by = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="maintenance_requests",
    )
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default=CAT_OTHER)
    title = models.CharField(max_length=200)
    description = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    landlord_reply = models.TextField(
        blank=True,
        help_text="Réponse visible par le locataire sur le portail.",
    )
    acknowledged_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Date à laquelle le bailleur a accusé réception (visible par le locataire).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Demande d'intervention"
        verbose_name_plural = "Demandes d'intervention"

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

    @property
    def is_closed(self) -> bool:
        """Terminée ou annulée : même règles métier (hors listes actives, non modifiable)."""
        return self.status in self.CLOSED_STATUSES

    def _tenant_portal_ack_only_phase(self) -> bool:
        """True si accusé de réception enregistré mais aucune réponse textuelle bailleur (champ fil ou message)."""
        if not self.acknowledged_at:
            return False
        if (self.landlord_reply or "").strip():
            return False
        return not self.thread_messages.filter(
            sender=MaintenanceThreadMessage.SENDER_LANDLORD
        ).exists()

    def maintenance_visual_key(self) -> str:
        """Clé couleur partagée (portail locataire + bailleur) : pending | ack | active | done | cancelled."""
        if self.status == self.STATUS_CANCELLED:
            return "cancelled"
        if self.status == self.STATUS_DONE:
            return "done"
        if self.status == self.STATUS_PENDING:
            return "pending"
        if self.status == self.STATUS_IN_PROGRESS:
            if self._tenant_portal_ack_only_phase():
                return "ack"
            return "active"
        return "pending"

    def tenant_portal_color_key(self) -> str:
        """Alias historique : identique à maintenance_visual_key()."""
        return self.maintenance_visual_key()

    def tenant_portal_status_label(self) -> str:
        """Libellé court affiché au locataire."""
        return {
            "pending": "En attente",
            "ack": "Accusé de réception",
            "active": "En cours",
            "done": "Terminée",
            "cancelled": "Annulée",
        }[self.maintenance_visual_key()]

    def landlord_maintenance_status_label(self) -> str:
        """Libellé bailleur (même découpage visuel que maintenance_visual_key)."""
        return {
            "pending": "Envoyée",
            "ack": "Accusé de réception",
            "active": "En cours",
            "done": "Terminée",
            "cancelled": "Annulée",
        }[self.maintenance_visual_key()]

    @property
    def stale_without_landlord_reply(self) -> bool:
        """True si la demande est encore ouverte, sans réponse texte ni accusé réception, depuis au moins 48 h."""
        if self.is_closed:
            return False
        if (self.landlord_reply or "").strip():
            return False
        if self.acknowledged_at:
            return False
        if self.thread_messages.filter(
            sender=MaintenanceThreadMessage.SENDER_LANDLORD
        ).exists():
            return False
        return self.created_at <= timezone.now() - timedelta(hours=48)


class MaintenanceThreadMessage(models.Model):
    """Message du fil de discussion bailleur ↔ locataire sur une demande d'intervention."""

    SENDER_LANDLORD = "landlord"
    SENDER_TENANT = "tenant"
    SENDER_CHOICES = [
        (SENDER_LANDLORD, "Bailleur"),
        (SENDER_TENANT, "Locataire"),
    ]

    maintenance_request = models.ForeignKey(
        MaintenanceRequest,
        on_delete=models.CASCADE,
        related_name="thread_messages",
    )
    sender = models.CharField(max_length=10, choices=SENDER_CHOICES)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "Message (fil intervention)"
        verbose_name_plural = "Messages (fil intervention)"

    def __str__(self) -> str:
        return f"{self.get_sender_display()} @ {self.created_at:%Y-%m-%d %H:%M}"


def maintenance_requests_pending_landlord_action_qs(user):
    """
    Demandes ouvertes pour lesquelles le bailleur doit réagir, pour badges / rappels.

    - **Premier contact** : statut « Envoyée », pas d’accusé, aucun message bailleur
      sur le fil (comme une demande toute neuve).
    - **Relance locataire** : au moins un message bailleur a déjà été posté, mais le
      **dernier** message du fil est du locataire (nouvelle réponse à traiter).

    Un accusé sans message bailleur fait passer en « En cours » : on ne compte pas
    tant que le locataire n’a pas écrit après un message bailleur (évite un badge
    bloqué alors que le bailleur n’a fait qu’accuser réception).
    """
    from django.db.models import Exists, OuterRef, Q, Subquery

    landlord_posted = MaintenanceThreadMessage.objects.filter(
        maintenance_request_id=OuterRef("pk"),
        sender=MaintenanceThreadMessage.SENDER_LANDLORD,
    )
    latest_sender_sq = (
        MaintenanceThreadMessage.objects.filter(
            maintenance_request_id=OuterRef("pk"),
        )
        .order_by("-created_at", "-id")
        .values("sender")[:1]
    )
    open_mr = MaintenanceRequest.objects.filter(
        lease__in=leases_visible_to(user),
    ).exclude(status__in=MaintenanceRequest.CLOSED_STATUSES)
    return open_mr.annotate(_last_thread_sender=Subquery(latest_sender_sq)).filter(
        (
            Q(
                status=MaintenanceRequest.STATUS_PENDING,
                acknowledged_at__isnull=True,
            )
            & ~Q(Exists(landlord_posted))
        )
        | (
            Q(Exists(landlord_posted))
            & Q(_last_thread_sender=MaintenanceThreadMessage.SENDER_TENANT)
        ),
    )


def maintenance_requests_needing_tenant_attention_qs(tenant, leases_qs):
    """
    Demandes d’intervention ouvertes pour lesquelles le locataire doit consulter le portail :
    dernier message du bailleur sur le fil, ou accusé de réception sans message bailleur encore.
    """
    from django.db.models import Exists, OuterRef, Q, Subquery

    landlord_posted = MaintenanceThreadMessage.objects.filter(
        maintenance_request_id=OuterRef("pk"),
        sender=MaintenanceThreadMessage.SENDER_LANDLORD,
    )
    latest_sender_sq = (
        MaintenanceThreadMessage.objects.filter(
            maintenance_request_id=OuterRef("pk"),
        )
        .order_by("-created_at", "-id")
        .values("sender")[:1]
    )
    open_mr = MaintenanceRequest.objects.filter(
        submitted_by=tenant,
        lease__in=leases_qs,
    ).exclude(status__in=MaintenanceRequest.CLOSED_STATUSES)
    return open_mr.annotate(_last_thread_sender=Subquery(latest_sender_sq)).filter(
        (
            Q(Exists(landlord_posted))
            & Q(_last_thread_sender=MaintenanceThreadMessage.SENDER_LANDLORD)
        )
        | (Q(acknowledged_at__isnull=False) & ~Q(Exists(landlord_posted))),
    )


@receiver(post_save, sender=MaintenanceRequest)
def _seed_maintenance_initial_thread_message(
    sender, instance: MaintenanceRequest, created: bool, **kwargs
) -> None:
    """Crée le premier message locataire (contenu initial) si le fil est vide."""
    if not created:
        return
    if MaintenanceThreadMessage.objects.filter(maintenance_request_id=instance.pk).exists():
        return
    body = (f"« {instance.title} »\n\n" if instance.title else "") + (instance.description or "")
    MaintenanceThreadMessage.objects.create(
        maintenance_request=instance,
        sender=MaintenanceThreadMessage.SENDER_TENANT,
        body=body.strip() or "(Pas de description.)",
    )


class MaintenanceRequestAttachment(models.Model):
    """Photo ou fichier joint à une demande d'intervention."""

    request = models.ForeignKey(
        MaintenanceRequest,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.FileField(
        upload_to=_portal_maintenance_attachment_upload,
        validators=[
            FileExtensionValidator(
                allowed_extensions=["jpg", "jpeg", "png", "gif", "webp", "pdf"]
            )
        ],
    )
    original_name = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Pièce jointe (intervention)"
        verbose_name_plural = "Pièces jointes (intervention)"


class MeterReading(models.Model):
    """Relevé de compteur transmis par le locataire (eau, électricité, gaz)."""

    METER_WATER = "water"
    METER_ELECTRICITY = "electricity"
    METER_GAS = "gas"
    METER_OTHER = "other"
    METER_CHOICES = [
        (METER_WATER, "Eau"),
        (METER_ELECTRICITY, "Électricité"),
        (METER_GAS, "Gaz"),
        (METER_OTHER, "Autre"),
    ]

    lease = models.ForeignKey(
        Lease,
        on_delete=models.CASCADE,
        related_name="meter_readings",
    )
    submitted_by = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="meter_readings",
    )
    meter_type = models.CharField(max_length=20, choices=METER_CHOICES)
    reading_date = models.DateField()
    value = models.CharField(max_length=80, help_text="Index ou valeur relevée")
    unit = models.CharField(max_length=30, blank=True, help_text="Ex. m³, kWh")
    notes = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-reading_date", "-created_at"]
        verbose_name = "Relevé de compteur"
        verbose_name_plural = "Relevés de compteurs"

    def __str__(self):
        return f"{self.get_meter_type_display()} – {self.reading_date}"


class RentPayment(models.Model):
    """Historise un encaissement sur une échéance (complément au statut Payé du loyer)."""

    METHOD_TRANSFER = "TRANSFER"
    METHOD_CARD = "CARD"
    METHOD_SEPA = "SEPA"
    METHOD_CASH = "CASH"
    METHOD_OTHER = "OTHER"
    METHOD_CHOICES = [
        (METHOD_TRANSFER, "Virement"),
        (METHOD_CARD, "Carte bancaire"),
        (METHOD_SEPA, "Prélèvement"),
        (METHOD_CASH, "Espèces"),
        (METHOD_OTHER, "Autre"),
    ]

    rent_invoice = models.ForeignKey(
        RentInvoice,
        on_delete=models.CASCADE,
        related_name="payments",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    paid_at = models.DateField()
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default=METHOD_TRANSFER)
    reference = models.CharField(max_length=200, blank=True, help_text="Référence bancaire ou commentaire")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_at", "-id"]
        verbose_name = "Paiement de loyer"
        verbose_name_plural = "Paiements de loyer"

    def __str__(self):
        return f"{self.amount} € – {self.paid_at}"

