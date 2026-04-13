import re

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Case, IntegerField, Q, Value, When
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm
from django.contrib.auth.password_validation import validate_password

from .plan_segments import (
    active_property_count_for_user,
    max_active_properties_for_volume_segment_key,
    message_active_property_quota_reached,
)
from .models import (
    BankAccount,
    InspectionReport,
    InspectionTemplate,
    Lease,
    LeaseAnnouncement,
    LeasePracticalInfo,
    LeaseTemplate,
    LetterTemplate,
    MaintenanceRequest,
    MeterReading,
    Organisation,
    OrganisationMember,
    Property,
    PropertyTag,
    PropertyWork,
    ReminderRule,
    StoredDocument,
    Tenant,
    UserSuggestion,
    properties_visible_to,
    tenants_visible_to,
    STANDARD_PROPERTY_TAG_NAMES,
    ensure_standard_property_tags,
)

User = get_user_model()


class AccountDeleteForm(forms.Form):
    """Confirmation de suppression définitive du compte bailleur SyLoc."""

    password = forms.CharField(
        label="Mot de passe actuel",
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    confirm_phrase = forms.CharField(
        label='Confirmation : tapez exactement SUPPRIMER (en majuscules)',
        max_length=20,
    )

    def clean_confirm_phrase(self):
        val = (self.cleaned_data.get("confirm_phrase") or "").strip()
        if val != "SUPPRIMER":
            raise ValidationError("Saisissez exactement le mot SUPPRIMER pour confirmer.")
        return val


class AccountProfileForm(forms.ModelForm):
    """Mise à jour du prénom et du nom (compte créateur SyLoc)."""

    class Meta:
        model = User
        fields = ("first_name", "last_name")
        labels = {
            "first_name": "Prénom",
            "last_name": "Nom",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("first_name", "last_name"):
            self.fields[name].widget.attrs.setdefault("autocomplete", "given-name" if name == "first_name" else "family-name")

    def clean_first_name(self):
        return (self.cleaned_data.get("first_name") or "").strip()

    def clean_last_name(self):
        return (self.cleaned_data.get("last_name") or "").strip()


class PasswordResetFormFR(PasswordResetForm):
    """Réinitialisation du mot de passe avec libellé en français."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].label = "Adresse email du compte"


class LoginFormFR(AuthenticationForm):
    """Formulaire de connexion avec libellés en français."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Email ou identifiant"
        self.fields["password"].label = "Mot de passe"


class UserRegistrationForm(forms.Form):
    """Création de compte gratuit : prénom, nom, email + mot de passe (formule choisie ultérieurement)."""

    first_name = forms.CharField(
        label="Prénom",
        max_length=150,
        required=True,
        strip=True,
        error_messages={"required": "Le prénom est obligatoire."},
        widget=forms.TextInput(attrs={"autocomplete": "given-name", "autofocus": True}),
    )
    last_name = forms.CharField(
        label="Nom",
        max_length=150,
        required=True,
        strip=True,
        error_messages={"required": "Le nom est obligatoire."},
        widget=forms.TextInput(attrs={"autocomplete": "family-name"}),
    )

    email = forms.EmailField(
        label="Adresse email",
        max_length=254,
        required=True,
        error_messages={
            "required": "L'adresse email est obligatoire.",
            "invalid": "Saisissez une adresse email valide.",
        },
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
        help_text="Elle servira aussi d'identifiant de connexion.",
    )
    password1 = forms.CharField(
        label="Mot de passe",
        required=True,
        strip=False,
        error_messages={"required": "Le mot de passe est obligatoire."},
        widget=forms.PasswordInput,
    )
    password2 = forms.CharField(
        label="Confirmation du mot de passe",
        required=True,
        strip=False,
        error_messages={"required": "La confirmation du mot de passe est obligatoire."},
        widget=forms.PasswordInput,
    )

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if not email:
            raise forms.ValidationError("L'adresse email est obligatoire.")
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Un compte existe déjà avec cette adresse email.")
        return email

    def clean_password1(self):
        password1 = self.cleaned_data.get("password1")
        if password1:
            validate_password(password1)
        return password1

    def clean(self):
        super().clean()
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError({"password2": "Les deux mots de passe ne correspondent pas."})
        return self.cleaned_data


class UsernameReminderForm(forms.Form):
    """Formulaire pour recevoir son identifiant par email."""
    email = forms.EmailField(
        label="Adresse email du compte",
        max_length=254,
        widget=forms.EmailInput(attrs={"autofocus": True}),
    )


class PropertyForm(forms.ModelForm):
    SCOPE_PERSONAL = "personal"
    SCOPE_TEAM = "team"
    SCOPE_CHOICES = [
        (SCOPE_PERSONAL, "Personnel (visible uniquement par vous)"),
        (SCOPE_TEAM, "Équipe (partagé avec votre organisation)"),
    ]

    sharing_scope = forms.ChoiceField(
        label="Portée des données",
        choices=SCOPE_CHOICES,
        initial=SCOPE_PERSONAL,
        widget=forms.RadioSelect,
        required=True,
        help_text="Choisissez si ce bien reste personnel ou partagé avec l'équipe.",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._form_user = user
        self._has_team_access = bool(
            user and OrganisationMember.objects.filter(user=user).exists()
        )
        if self.instance and self.instance.pk:
            self.fields["sharing_scope"].initial = (
                self.SCOPE_TEAM if self.instance.organisation_id else self.SCOPE_PERSONAL
            )
        elif not self._has_team_access:
            self.fields["sharing_scope"].widget = forms.HiddenInput()
            self.fields["sharing_scope"].initial = self.SCOPE_PERSONAL

        ensure_standard_property_tags()
        tag_q = Q(name__in=STANDARD_PROPERTY_TAG_NAMES)
        if self.instance.pk:
            extra_ids = list(self.instance.tags.values_list("pk", flat=True))
            if extra_ids:
                tag_q |= Q(pk__in=extra_ids)
        whens = [
            When(name=n, then=Value(i)) for i, n in enumerate(STANDARD_PROPERTY_TAG_NAMES)
        ]
        tags_qs = (
            PropertyTag.objects.filter(tag_q)
            .annotate(
                _ord=Case(
                    *whens,
                    default=Value(1000),
                    output_field=IntegerField(),
                )
            )
            .order_by("_ord", "name")
        )
        # Remplacer le widget avant d’assigner le queryset : sinon ModelChoiceField
        # remplit choices sur l’ancien widget et le nouveau CheckboxSelectMultiple reste vide.
        self.fields["tags"].widget = forms.CheckboxSelectMultiple(
            attrs={"class": "property-tags-checkbox"}
        )
        self.fields["tags"].queryset = tags_qs
        self.fields["tags"].required = False
        self.fields["tags"].help_text = "Optionnel — cochez chaque libellé qui s’applique au bien."

    def clean_sharing_scope(self):
        scope = self.cleaned_data.get("sharing_scope") or self.SCOPE_PERSONAL
        if scope == self.SCOPE_TEAM and not self._has_team_access:
            return self.SCOPE_PERSONAL
        return scope

    def clean(self):
        cleaned_data = super().clean()
        user = self._form_user
        if not user or not getattr(user, "is_authenticated", False):
            return cleaned_data
        if self.instance.pk:
            return cleaned_data
        profile = getattr(user, "profile", None)
        if not profile:
            return cleaned_data
        max_p = max_active_properties_for_volume_segment_key(profile.volume_segment_key)
        if max_p is None:
            return cleaned_data
        n = active_property_count_for_user(user)
        if n >= max_p:
            raise ValidationError(message_active_property_quota_reached(user))
        return cleaned_data

    class Meta:
        model = Property
        fields = [
            "name",
            "address",
            "city",
            "zip_code",
            "owner_type",
            "tags",
            "purchase_price",
            "notary_fees",
            "monthly_mortgage",
            "monthly_charges",
            "dpe_class",
            "dpe_valid_until",
        ]
        labels = {
            "name": "Nom du bien",
            "address": "Adresse",
            "city": "Ville",
            "zip_code": "Code postal",
            "owner_type": "Type de propriétaire",
            "tags": "Tags",
            "purchase_price": "Prix d'achat (€)",
            "notary_fees": "Frais de notaire (€)",
            "monthly_mortgage": "Mensualité de crédit (€)",
            "monthly_charges": "Charges mensuelles (€)",
            "dpe_class": "Classe DPE",
            "dpe_valid_until": "DPE valide jusqu'au",
        }
        widgets = {
            "dpe_valid_until": forms.DateInput(attrs={"type": "date"}),
        }


class PropertyWorkForm(forms.ModelForm):
    class Meta:
        model = PropertyWork
        fields = ["work_type", "title", "description", "work_date", "amount"]
        labels = {
            "work_type": "Type de travaux",
            "title": "Résumé",
            "description": "Détail",
            "work_date": "Date des travaux",
            "amount": "Montant TTC (€)",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "work_date": forms.DateInput(attrs={"type": "date"}),
        }


class TenantForm(forms.ModelForm):
    SCOPE_PERSONAL = "personal"
    SCOPE_TEAM = "team"
    SCOPE_CHOICES = [
        (SCOPE_PERSONAL, "Personnel (visible uniquement par vous)"),
        (SCOPE_TEAM, "Équipe (partagé avec votre organisation)"),
    ]

    sharing_scope = forms.ChoiceField(
        label="Portée des données",
        choices=SCOPE_CHOICES,
        initial=SCOPE_PERSONAL,
        widget=forms.RadioSelect,
        required=True,
        help_text="Choisissez si ce locataire reste personnel ou partagé avec l'équipe.",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._has_team_access = bool(
            user and OrganisationMember.objects.filter(user=user).exists()
        )
        if self.instance and self.instance.pk:
            self.fields["sharing_scope"].initial = (
                self.SCOPE_TEAM if self.instance.organisation_id else self.SCOPE_PERSONAL
            )
        elif not self._has_team_access:
            self.fields["sharing_scope"].widget = forms.HiddenInput()
            self.fields["sharing_scope"].initial = self.SCOPE_PERSONAL

    def clean_sharing_scope(self):
        scope = self.cleaned_data.get("sharing_scope") or self.SCOPE_PERSONAL
        if scope == self.SCOPE_TEAM and not self._has_team_access:
            return self.SCOPE_PERSONAL
        return scope

    class Meta:
        model = Tenant
        fields = ["first_name", "last_name", "email", "phone"]
        labels = {
            "first_name": "Prénom",
            "last_name": "Nom",
            "email": "Adresse email",
            "phone": "Téléphone",
        }


_MAX_PDF_BYTES = 10 * 1024 * 1024  # 10 Mo


def _iban_valid(iban: str) -> bool:
    """Contrôle de forme et clé IBAN (mod 97)."""
    compact = (iban or "").replace(" ", "").upper()
    if not (15 <= len(compact) <= 34):
        return False
    if not re.match(r"^[A-Z]{2}\d{2}[A-Z0-9]+$", compact):
        return False
    rearranged = compact[4:] + compact[:4]
    digits = []
    for c in rearranged:
        if c.isdigit():
            digits.append(c)
        else:
            digits.append(str(ord(c) - ord("A") + 10))
    try:
        return int("".join(digits)) % 97 == 1
    except ValueError:
        return False


def _bic_valid(bic: str) -> bool:
    s = (bic or "").replace(" ", "").upper()
    if not s:
        return True
    return bool(re.match(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$", s))


class LeaseForm(forms.ModelForm):
    class Meta:
        model = Lease
        fields = [
            "property",
            "tenants",
            "start_date",
            "end_date",
            "rent",
            "charges",
            "deposit",
            "lease_document",
            "attached_file",
            "rent_payment_iban",
            "rent_payment_bic",
            "rent_payment_holder",
            "tenant_insurance_valid_until",
            "lease_renewal_reminder_date",
        ]
        widgets = {
            "lease_document": forms.Textarea(attrs={"rows": 12}),
            "tenants": forms.CheckboxSelectMultiple(),
            "attached_file": forms.ClearableFileInput(
                attrs={"accept": "application/pdf,.pdf"}
            ),
            "tenant_insurance_valid_until": forms.DateInput(attrs={"type": "date"}),
            "lease_renewal_reminder_date": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "property": "Bien",
            "tenants": "Locataire(s)",
            "start_date": "Date de début",
            "end_date": "Date de fin",
            "rent": "Loyer mensuel (€)",
            "charges": "Charges (€)",
            "deposit": "Dépôt de garantie (€)",
            "lease_document": "Texte du bail",
            "attached_file": "Fichier PDF du bail (optionnel)",
            "rent_payment_iban": "IBAN pour le virement du loyer (portail locataire)",
            "rent_payment_bic": "BIC / SWIFT (optionnel)",
            "rent_payment_holder": "Titulaire du compte (optionnel)",
            "tenant_insurance_valid_until": "Fin de validité assurance locataire (rappel portail)",
            "lease_renewal_reminder_date": "Date à rappeler (reconduction, préavis…)",
        }
        help_texts = {
            "rent_payment_iban": "Affiché au locataire pour régler par virement. Laissez vide si vous communiquez le RIB autrement.",
            "rent_payment_bic": "Utile surtout pour un virement depuis l’étranger.",
        }

    def clean_tenants(self):
        value = self.cleaned_data.get("tenants")
        if value is not None and hasattr(value, "exists") and not value.exists():
            raise forms.ValidationError("Sélectionnez au moins un locataire.")
        return value

    def clean_attached_file(self):
        f = self.cleaned_data.get("attached_file")
        if f and getattr(f, "size", 0) > _MAX_PDF_BYTES:
            raise ValidationError("Le fichier PDF ne doit pas dépasser 10 Mo.")
        return f

    def clean_rent_payment_iban(self):
        raw = (self.cleaned_data.get("rent_payment_iban") or "").strip()
        if not raw:
            return ""
        compact = raw.replace(" ", "").upper()
        if not _iban_valid(compact):
            raise ValidationError("IBAN invalide (vérifiez le numéro et la clé).")
        return compact

    def clean_rent_payment_bic(self):
        raw = (self.cleaned_data.get("rent_payment_bic") or "").strip()
        if not raw:
            return ""
        compact = raw.replace(" ", "").upper()
        if not _bic_valid(compact):
            raise ValidationError("BIC invalide (8 ou 11 caractères).")
        return compact

    def clean_rent_payment_holder(self):
        return (self.cleaned_data.get("rent_payment_holder") or "").strip()

    def clean(self):
        data = super().clean()
        start = data.get("start_date")
        end = data.get("end_date")
        if start and end and end < start:
            raise forms.ValidationError(
                "La date de fin doit être postérieure à la date de début."
            )
        return data


class RentRevisionForm(forms.Form):
    """Révision du montant du prochain loyer à venir (un seul loyer)."""
    amount_rent = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=0,
        label="Nouveau loyer (€)",
    )
    amount_charges = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=0,
        initial=0,
        label="Nouvelles charges (€)",
    )


class InspectionReportForm(forms.ModelForm):
    class Meta:
        model = InspectionReport
        fields = ["lease", "report_type", "report_date", "notes", "attached_file", "shared_with_portal"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 6}),
            "attached_file": forms.ClearableFileInput(
                attrs={"accept": "application/pdf,.pdf"}
            ),
        }
        labels = {
            "lease": "Bail",
            "report_type": "Type d'état des lieux",
            "report_date": "Date",
            "notes": "Observations",
            "attached_file": "Fichier PDF (optionnel)",
            "shared_with_portal": "PDF visible sur le portail locataire",
        }

    def clean_attached_file(self):
        f = self.cleaned_data.get("attached_file")
        if f and getattr(f, "size", 0) > _MAX_PDF_BYTES:
            raise ValidationError("Le fichier PDF ne doit pas dépasser 10 Mo.")
        return f


class LeaseTemplateForm(forms.ModelForm):
    class Meta:
        model = LeaseTemplate
        fields = ["name", "content"]
        widgets = {
            "content": forms.Textarea(attrs={"rows": 18}),
        }
        labels = {
            "name": "Nom du modèle",
            "content": "Texte du bail (modèle)",
        }


class InspectionTemplateForm(forms.ModelForm):
    class Meta:
        model = InspectionTemplate
        fields = ["name", "content"]
        widgets = {
            "content": forms.Textarea(attrs={"rows": 14}),
        }
        labels = {
            "name": "Nom du modèle",
            "content": "Texte type (sections, points à vérifier)",
        }


class UserSuggestionForm(forms.Form):
    """Suggestion d’amélioration SyLoc (thème + message ; pièces jointes gérées dans la vue)."""

    contact_email = forms.EmailField(
        label="Votre email",
        help_text="Pour vous recontacter si besoin.",
    )
    theme = forms.ChoiceField(
        label="Thème",
        choices=UserSuggestion.THEME_CHOICES,
        widget=forms.Select,
    )
    message = forms.CharField(
        label="Votre message",
        widget=forms.Textarea(attrs={"rows": 6}),
        min_length=10,
    )


class ReminderRuleForm(forms.ModelForm):
    """Règle de relance impayé (J+N) — objet et corps du mail personnalisables."""

    class Meta:
        model = ReminderRule
        fields = ["email_subject", "email_body_template", "is_active"]
        widgets = {
            "email_subject": forms.TextInput(attrs={"maxlength": 200}),
            "email_body_template": forms.Textarea(attrs={"rows": 12}),
        }
        labels = {
            "email_subject": "Objet du mail",
            "email_body_template": "Corps du mail (optionnel)",
            "is_active": "Relance active",
        }
        help_texts = {
            "email_body_template": "Laissez vide pour utiliser le modèle SyLoc par défaut.",
        }


class LetterTemplateForm(forms.ModelForm):
    """Modèle de lettre type (Premium) — édition dans l’app, sans passer par l’admin."""

    class Meta:
        model = LetterTemplate
        fields = ["name", "letter_type", "content"]
        widgets = {
            "content": forms.Textarea(attrs={"rows": 20}),
        }
        labels = {
            "name": "Nom du modèle",
            "letter_type": "Type de lettre",
            "content": "Corps de la lettre",
        }
        help_texts = {
            "content": (
                "Les modèles types montrent comment insérer des données dynamiques (locataire, bien, adresse, loyer, échéance, bailleur)."
            ),
        }


class StoredDocumentForm(forms.ModelForm):
    """Ajout ou modification d’un document stocké (bien ou locataire)."""

    class Meta:
        model = StoredDocument
        fields = ["property", "tenant", "document_type", "title", "file", "notes", "shared_with_portal"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
            "file": forms.ClearableFileInput(
                attrs={"accept": ".pdf,.jpg,.jpeg,.png,.doc,.docx,application/pdf"}
            ),
        }
        labels = {
            "property": "Bien",
            "tenant": "Locataire",
            "document_type": "Type de document",
            "title": "Titre",
            "file": "Fichier",
            "notes": "Notes",
            "shared_with_portal": "Visible sur le portail locataire",
        }
        help_texts = {
            "property": "Optionnel si vous rattachez le document à un locataire ci-dessous.",
            "tenant": "Optionnel si vous rattachez le document à un bien ci-dessus. Au moins un des deux est requis.",
            "file": "PDF, images (JPEG, PNG) ou Word — max 15 Mo.",
            "shared_with_portal": "Le locataire pourra télécharger ce fichier depuis son lien portail.",
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user:
            self.fields["property"].queryset = properties_visible_to(user).filter(
                archived_at__isnull=True
            ).order_by("name")
            self.fields["tenant"].queryset = tenants_visible_to(user).filter(
                archived_at__isnull=True
            ).order_by("last_name", "first_name")
        self.fields["property"].required = False
        self.fields["tenant"].required = False
        self.fields["property"].empty_label = "— Choisir un bien —"
        self.fields["tenant"].empty_label = "— Choisir un locataire —"
        has_existing_file = bool(
            self.instance.pk and self.instance.file and getattr(self.instance.file, "name", None)
        )
        self.fields["file"].required = not has_existing_file

    def clean(self):
        cleaned = super().clean()
        prop = cleaned.get("property")
        ten = cleaned.get("tenant")
        if not prop and not ten:
            raise ValidationError("Indiquez au moins un bien ou un locataire.")
        return cleaned

    def clean_file(self):
        f = self.cleaned_data.get("file")
        if not f:
            return f
        if f.size > 15 * 1024 * 1024:
            raise ValidationError("Fichier trop volumineux (max 15 Mo).")
        name = (f.name or "").lower()
        allowed = (".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx")
        if not any(name.endswith(ext) for ext in allowed):
            raise ValidationError("Formats acceptés : PDF, JPEG, PNG, Word (.doc, .docx).")
        return f


class OrganisationNameForm(forms.ModelForm):
    """Modifier le nom de l'organisation (équipe) affiché sur la page Équipe."""

    class Meta:
        model = Organisation
        fields = ["name"]
        labels = {"name": "Nom de l'équipe"}
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "maxlength": "150",
                    "class": "team-org-name-input",
                }
            ),
        }


class InviteMemberForm(forms.Form):
    """Inviter un utilisateur existant dans l'organisation par son email."""
    email = forms.EmailField(
        label="Adresse email du futur membre",
        widget=forms.EmailInput(attrs={"placeholder": "email@exemple.com"}),
    )

    def __init__(self, organisation, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.organisation = organisation

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if not email:
            return email
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            raise forms.ValidationError("Aucun compte avec cette adresse email. La personne doit d'abord créer un compte SyLoc.")
        if self.organisation.members.filter(user=user).exists():
            raise forms.ValidationError("Cette personne fait déjà partie de l'organisation.")
        return email


class TeamMemberEmailForm(forms.Form):
    """Mise à jour de l'email d'un membre (compte Django) par le propriétaire de l'organisation."""

    member_id = forms.IntegerField(widget=forms.HiddenInput())
    email = forms.EmailField(
        label="Adresse email",
        widget=forms.EmailInput(attrs={"placeholder": "email@exemple.com"}),
    )

    def __init__(self, organisation, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.organisation = organisation
        self.member: OrganisationMember | None = None

    def clean(self):
        data = super().clean()
        member_id = data.get("member_id")
        email = (data.get("email") or "").strip().lower()
        if member_id is None:
            raise ValidationError("Membre invalide.")
        try:
            member = OrganisationMember.objects.select_related("user").get(
                pk=int(member_id),
                organisation=self.organisation,
            )
        except (OrganisationMember.DoesNotExist, ValueError, TypeError):
            raise ValidationError("Ce membre n'existe pas dans cette organisation.")
        self.member = member
        if User.objects.filter(email__iexact=email).exclude(pk=member.user_id).exists():
            raise ValidationError("Un autre compte utilise déjà cette adresse email.")
        data["email"] = email
        return data


class DocumentSignatureForm(forms.Form):
    """Formulaire de signature électronique : confirmation sans dessin (nom + date de signature)."""
    signatory_name = forms.CharField(
        max_length=200,
        label="Nom du signataire",
        widget=forms.TextInput(attrs={"placeholder": "Prénom Nom", "autocomplete": "name"}),
    )


class BankAccountForm(forms.ModelForm):
    """Création / édition d'un compte bancaire."""
    class Meta:
        model = BankAccount
        fields = ["name"]
        labels = {"name": "Nom du compte"}


class BankStatementUploadForm(forms.Form):
    """Import d'un relevé bancaire au format CSV."""
    file = forms.FileField(
        label="Fichier CSV",
        help_text="CSV avec colonnes : Date (jj/mm/aaaa ou aaaa-mm-jj), Montant (nombre décimal, positif=entrée), Libellé (optionnel). Séparateur ; ou ,",
    )

    def clean_file(self):
        f = self.cleaned_data.get("file")
        if not f:
            return f
        if not f.name.lower().endswith(".csv"):
            raise forms.ValidationError("Le fichier doit être au format CSV.")
        if f.size > 5 * 1024 * 1024:  # 5 Mo
            raise forms.ValidationError("Fichier trop volumineux (max 5 Mo).")
        return f


class TenantPortalMaintenanceForm(forms.Form):
    lease = forms.ModelChoiceField(label="Bail / logement", queryset=Lease.objects.none())
    category = forms.ChoiceField(label="Type", choices=MaintenanceRequest.CATEGORY_CHOICES)
    title = forms.CharField(label="Objet", max_length=200)
    description = forms.CharField(
        label="Description",
        widget=forms.Textarea(attrs={"rows": 5}),
        min_length=10,
    )

    def __init__(self, *args, leases=None, **kwargs):
        super().__init__(*args, **kwargs)
        if leases is not None:
            self.fields["lease"].queryset = leases


class TenantPortalMeterForm(forms.Form):
    lease = forms.ModelChoiceField(label="Bail / logement", queryset=Lease.objects.none())
    meter_type = forms.ChoiceField(label="Compteur", choices=MeterReading.METER_CHOICES)
    reading_date = forms.DateField(
        label="Date du relevé de compteurs",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    value = forms.CharField(label="Index ou valeur", max_length=80)
    unit = forms.CharField(label="Unité (optionnel)", max_length=30, required=False)
    notes = forms.CharField(
        label="Remarque (optionnel)",
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def __init__(self, *args, leases=None, **kwargs):
        super().__init__(*args, **kwargs)
        if leases is not None:
            self.fields["lease"].queryset = leases

