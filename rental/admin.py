from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

from .models import (
    Property,
    Tenant,
    Lease,
    RentInvoice,
    RentPayment,
    InspectionReport,
    Plan,
    UserProfile,
    ApiKey,
    Organisation,
    OrganisationMember,
    Announcement,
    AdminActionLog,
    SigningInvitation,
    DocumentSignature,
    BankAccount,
    BankTransaction,
    PropertyTag,
    PropertyDiagnostic,
    ReminderRule,
    UserSuggestion,
    UserSuggestionAttachment,
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
)

User = get_user_model()


class AdminActionLogMixin:
    """Enregistre date/heure et action (ajout, modification, suppression) dans AdminActionLog."""

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        AdminActionLog.objects.create(
            user=request.user,
            action=AdminActionLog.ACTION_CHANGE if change else AdminActionLog.ACTION_ADD,
            content_type=ContentType.objects.get_for_model(obj),
            object_id=obj.pk,
            object_repr=str(obj)[:255],
        )

    def delete_model(self, request, obj):
        ct = ContentType.objects.get_for_model(type(obj))
        object_repr = str(obj)[:255]
        object_id = obj.pk
        super().delete_model(request, obj)
        AdminActionLog.objects.create(
            user=request.user,
            action=AdminActionLog.ACTION_DELETE,
            content_type=ct,
            object_id=object_id,
            object_repr=object_repr,
        )


@admin.register(AdminActionLog)
class AdminActionLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "user", "action", "object_repr", "comment")
    list_filter = ("action", "timestamp")
    search_fields = ("object_repr", "comment")
    readonly_fields = ("user", "action", "content_type", "object_id", "object_repr", "timestamp")
    list_editable = ("comment",)

    def has_add_permission(self, request):
        return False  # Les entrées sont créées automatiquement


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "level", "is_published", "created_at", "updated_at")
    list_filter = ("level", "is_published")
    list_editable = ("is_published",)
    search_fields = ("title", "message")


@admin.register(SigningInvitation)
class SigningInvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "role", "content_type", "object_id", "signed_at", "expires_at", "created_at")
    list_filter = ("role", "content_type")
    search_fields = ("email", "token")
    readonly_fields = ("token", "created_at")


@admin.register(DocumentSignature)
class DocumentSignatureAdmin(admin.ModelAdmin):
    list_display = ("signatory_name", "invitation", "signed_at", "ip_address")
    list_filter = ("signed_at",)
    search_fields = ("signatory_name",)
    readonly_fields = ("invitation", "signed_at", "ip_address")


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "organisation", "created_at")
    list_filter = ("organisation",)
    search_fields = ("name",)


@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = ("date", "account", "amount", "label_short", "rent_invoice")
    list_filter = ("account", "date")
    search_fields = ("label",)

    def label_short(self, obj):
        return (obj.label or "")[:50] if obj.label else ""
    label_short.short_description = "Libellé"


@admin.register(Organisation)
class OrganisationAdmin(AdminActionLogMixin, admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)


@admin.register(OrganisationMember)
class OrganisationMemberAdmin(AdminActionLogMixin, admin.ModelAdmin):
    list_display = ("user", "organisation", "role")
    list_filter = ("role", "organisation")
    search_fields = ("user__email", "user__username", "organisation__name")


@admin.register(Property)
class PropertyAdmin(AdminActionLogMixin, admin.ModelAdmin):
    list_display = ("name", "organisation", "city", "owner_type", "monthly_mortgage", "monthly_charges")
    list_filter = ("organisation",)
    search_fields = ("name", "city", "owner__email")
    filter_horizontal = ("tags",)


@admin.register(Tenant)
class TenantAdmin(AdminActionLogMixin, admin.ModelAdmin):
    list_display = ("first_name", "last_name", "email", "phone")
    search_fields = ("first_name", "last_name", "email")


@admin.register(Lease)
class LeaseAdmin(AdminActionLogMixin, admin.ModelAdmin):
    list_display = ("property", "tenants_display", "start_date", "end_date", "rent", "charges", "is_active")
    list_filter = ("is_active",)


@admin.register(RentInvoice)
class RentInvoiceAdmin(AdminActionLogMixin, admin.ModelAdmin):
    list_display = ("lease", "due_date", "amount_rent", "amount_charges", "status", "paid_date")
    list_filter = ("status", "due_date")


@admin.register(InspectionReport)
class InspectionReportAdmin(AdminActionLogMixin, admin.ModelAdmin):
    list_display = ("lease", "report_type", "report_date", "shared_with_portal")
    list_filter = ("report_type", "shared_with_portal")


@admin.register(Plan)
class PlanAdmin(AdminActionLogMixin, admin.ModelAdmin):
    list_display = ("name", "slug", "price_display", "stripe_price_id", "stripe_price_id_annual")
    search_fields = ("name", "slug")

    def save_model(self, request, obj, form, change):
        obj.full_clean()
        super().save_model(request, obj, form, change)

    fieldsets = (
        (None, {
            "fields": ("name", "slug", "description"),
        }),
        ("Affichage", {
            "fields": ("price_display",),
            "description": "Texte visible sur le site (ex. « 15 € / mois » ou « 45 € / mois »).",
        }),
        ("Stripe – abonnements", {
            "fields": ("stripe_price_id", "stripe_price_id_annual"),
            "description": (
                "Renseignez ici les IDs de prix Stripe (price_xxx) pour cette formule : "
                "mensuel dans « stripe_price_id », annuel dans « stripe_price_id_annual »."
            ),
        }),
    )


@admin.register(UserProfile)
class UserProfileAdmin(AdminActionLogMixin, admin.ModelAdmin):
    list_display = ("user", "plan")
    list_display_links = ("user",)
    list_editable = ("plan",)
    list_filter = ("plan",)
    search_fields = ("user__email", "user__username")


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ("user", "key_prefix", "created_at")
    list_filter = ("created_at",)
    search_fields = ("user__email", "user__username", "key_prefix")
    readonly_fields = ("key_hash", "key_prefix", "created_at")


@admin.register(PropertyTag)
class PropertyTagAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(PropertyDiagnostic)
class PropertyDiagnosticAdmin(admin.ModelAdmin):
    list_display = ("property", "diagnostic_type", "done_date", "expiry_date", "created_at")
    list_filter = ("diagnostic_type",)
    search_fields = ("property__name", "notes")
    list_select_related = ("property",)
    autocomplete_fields = ("property",)
    date_hierarchy = "expiry_date"
    fieldsets = (
        (None, {
            "fields": ("property", "diagnostic_type", "done_date", "expiry_date"),
        }),
        ("Notes", {
            "fields": ("notes",),
            "classes": ("collapse",),
        }),
    )


class UserSuggestionAttachmentInline(admin.TabularInline):
    model = UserSuggestionAttachment
    extra = 0
    readonly_fields = ("file", "original_name")


@admin.register(UserSuggestion)
class UserSuggestionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "theme", "contact_email", "user", "short_message")
    list_filter = ("theme", "created_at")
    search_fields = ("message", "contact_email", "user__email", "user__username")
    readonly_fields = ("created_at", "user")
    inlines = [UserSuggestionAttachmentInline]
    ordering = ("-created_at",)

    @admin.display(description="Message")
    def short_message(self, obj):
        return (obj.message[:100] + "…") if len(obj.message) > 100 else obj.message


@admin.register(ReminderRule)
class ReminderRuleAdmin(admin.ModelAdmin):
    list_display = ("owner", "days_after_due", "email_subject", "is_active", "created_at")
    list_filter = ("is_active", "owner")
    search_fields = ("owner__email", "owner__username", "email_subject")
    list_editable = ("is_active",)
    autocomplete_fields = ("owner", "organisation")
    fieldsets = (
        (None, {
            "fields": ("owner", "organisation", "days_after_due", "is_active"),
        }),
        ("Email de relance", {
            "fields": ("email_subject", "email_body_template"),
            "description": "Variables disponibles : {{tenant}}, {{amount}}, {{due_date}}, {{property}}",
        }),
    )


@admin.register(LetterTemplate)
class LetterTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "letter_type", "owner", "organisation", "updated_at")
    list_filter = ("letter_type",)
    search_fields = ("name", "owner__email", "owner__username")
    autocomplete_fields = ("owner", "organisation")
    fieldsets = (
        (None, {
            "fields": ("name", "letter_type", "owner", "organisation"),
        }),
        ("Contenu", {
            "fields": ("content",),
            "description": "Les modèles types illustrent les champs dynamiques (locataire, bien, adresse, loyer, échéance, bailleur).",
        }),
    )


@admin.register(StoredDocument)
class StoredDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "document_type", "property", "tenant", "shared_with_portal", "created_at")
    list_filter = ("document_type", "shared_with_portal")


@admin.register(Webhook)
class WebhookAdmin(admin.ModelAdmin):
    list_display = ("url", "owner", "is_active", "created_at")
    list_filter = ("is_active",)


@admin.register(UserActivityLog)
class UserActivityLogAdmin(admin.ModelAdmin):
    list_display = ("user", "action", "object_repr", "timestamp")
    list_filter = ("action", "timestamp")
    readonly_fields = ("user", "action", "content_type", "object_id", "object_repr", "details", "timestamp")


@admin.register(TenantPortalAccess)
class TenantPortalAccessAdmin(admin.ModelAdmin):
    list_display = ("tenant", "token", "expires_at", "created_at")
    readonly_fields = ("token",)


@admin.register(LeasePracticalInfo)
class LeasePracticalInfoAdmin(admin.ModelAdmin):
    list_display = ("lease", "title", "sort_order")
    search_fields = ("title", "body")


@admin.register(LeaseAnnouncement)
class LeaseAnnouncementAdmin(admin.ModelAdmin):
    list_display = ("lease", "title", "is_active", "published_at", "expires_at")
    list_filter = ("is_active",)


class MaintenanceThreadMessageInline(admin.TabularInline):
    model = MaintenanceThreadMessage
    extra = 0
    readonly_fields = ("sender", "created_at")
    ordering = ("created_at", "id")


@admin.register(MaintenanceRequest)
class MaintenanceRequestAdmin(admin.ModelAdmin):
    list_display = ("title", "lease", "submitted_by", "status", "category", "acknowledged_at", "created_at")
    list_filter = ("status", "category")
    search_fields = ("title", "description")
    inlines = (MaintenanceThreadMessageInline,)


@admin.register(MaintenanceRequestAttachment)
class MaintenanceRequestAttachmentAdmin(admin.ModelAdmin):
    list_display = ("request", "original_name", "created_at")


@admin.register(MeterReading)
class MeterReadingAdmin(admin.ModelAdmin):
    list_display = ("lease", "meter_type", "reading_date", "value", "submitted_by", "created_at")


@admin.register(RentPayment)
class RentPaymentAdmin(admin.ModelAdmin):
    list_display = ("rent_invoice", "amount", "paid_at", "method", "created_at")
    list_filter = ("method",)

