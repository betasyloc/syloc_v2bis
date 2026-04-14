from django.conf import settings
from django.contrib import admin
from django.urls import path, include, reverse_lazy
from django.contrib.auth import views as auth_views

from rental import views as rental_views
from rental import premium_views as rental_premium_views
from rental.forms import LoginFormFR, PasswordResetFormFR

admin.site.site_header = "SyLoc – Staff"
admin.site.site_title = "SyLoc"


def get_login_view():
    view = auth_views.LoginView.as_view(
        template_name="auth/login.html",
        form_class=LoginFormFR,
    )
    if getattr(settings, "RATELIMIT_ENABLE", True):
        try:
            from django_ratelimit.decorators import ratelimit
            rate = getattr(settings, "RATELIMIT_LOGIN", "5/5m")
            view = ratelimit(
                key="ip",
                rate=rate,
                method="POST",
                block=True,
            )(view)
        except Exception:
            pass  # fallback: login sans limite si ratelimit indisponible
    return view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", get_login_view(), name="login"),
    path("creer-compte/", rental_views.register, name="register"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("mot-de-passe-oublie/", auth_views.PasswordResetView.as_view(
        template_name="registration/password_reset_form.html",
        email_template_name="registration/password_reset_email.html",
        subject_template_name="registration/password_reset_subject.txt",
        success_url=reverse_lazy("password_reset_done"),
        form_class=PasswordResetFormFR,
    ), name="password_reset"),
    path("mot-de-passe-oublie/envoye/", auth_views.PasswordResetDoneView.as_view(
        template_name="registration/password_reset_done.html",
    ), name="password_reset_done"),
    path("reinitialiser/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="registration/password_reset_confirm.html",
        success_url=reverse_lazy("password_reset_complete"),
    ), name="password_reset_confirm"),
    path("reinitialiser/termine/", auth_views.PasswordResetCompleteView.as_view(
        template_name="registration/password_reset_complete.html",
    ), name="password_reset_complete"),
    path("identifiant-oublie/", rental_views.username_reminder, name="username_reminder"),
    path("identifiant-oublie/envoye/", rental_views.username_reminder_done, name="username_reminder_done"),
    path("administration/", rental_views.admin_dashboard, name="admin_dashboard"),
    path("premium/", rental_views.premium_page, name="premium"),
    path("abonnement/", rental_views.subscription_page, name="subscription"),
    path(
        "abonnement/mode-test/",
        rental_views.subscription_sandbox_set,
        name="subscription_sandbox_set",
    ),
    path("abonnement/<str:plan_slug>/", rental_views.plan_choice, name="plan_choice"),
    path("premium/checkout/", rental_views.create_checkout_session, name="create_checkout_session"),
    path(
        "premium/changer-formule/",
        rental_views.change_subscription_plan,
        name="change_subscription_plan",
    ),
    path("premium/portal/", rental_views.create_billing_portal_session, name="create_billing_portal_session"),
    path("premium/success/", rental_views.checkout_success, name="checkout_success"),
    path("premium/cancel/", rental_views.checkout_cancel, name="checkout_cancel"),
    path("webhooks/stripe/", rental_views.stripe_webhook, name="stripe_webhook"),
    path("compte/profil/", rental_views.account_profile, name="account_profile"),
    path(
        "compte/profil/deconnecter-autres-appareils/",
        rental_views.account_logout_other_sessions,
        name="account_logout_other_sessions",
    ),
    path("compte/suppression/", rental_views.account_delete_confirm, name="account_delete"),
    path("mentions-legales/", rental_views.legal_mentions, name="legal_mentions"),
    path("confidentialite/", rental_views.legal_privacy, name="legal_privacy"),
    path("cookies/", rental_views.legal_cookies, name="legal_cookies"),
    path(
        "api/ui/maintenance-badge/",
        rental_views.ui_maintenance_badge_count,
        name="ui_maintenance_badge_count",
    ),
    path("notifications/", rental_views.notifications_page, name="notifications"),
    path("suggestions/", rental_views.suggestions_page, name="suggestions"),
    path("sign/<str:token>/", rental_views.sign_document, name="sign_document"),
    path(
        "portal/<str:token>/bail/<int:lease_pk>/telecharger/",
        rental_premium_views.tenant_portal_lease_pdf,
        name="tenant_portal_lease_pdf",
    ),
    path(
        "portal/<str:token>/bail/<int:lease_pk>.pdf",
        rental_premium_views.tenant_portal_lease_pdf_redirect,
    ),
    path(
        "portal/<str:token>/quittance/<int:invoice_pk>/telecharger/",
        rental_premium_views.tenant_portal_quittance_pdf,
        name="tenant_portal_quittance_pdf",
    ),
    path(
        "portal/<str:token>/quittance/<int:invoice_pk>.pdf",
        rental_premium_views.tenant_portal_quittance_pdf_redirect,
    ),
    path("portal/quitter-mode/", rental_premium_views.tenant_portal_exit_mode, name="tenant_portal_exit_mode"),
    path(
        "portal/<str:token>/demande/<int:pk>/message/",
        rental_premium_views.tenant_portal_maintenance_reply,
        name="tenant_portal_maintenance_reply",
    ),
    path(
        "portal/<str:token>/demande/",
        rental_premium_views.tenant_portal_maintenance_create,
        name="tenant_portal_maintenance_create",
    ),
    path(
        "portal/<str:token>/releve/",
        rental_premium_views.tenant_portal_meter_submit,
        name="tenant_portal_meter_submit",
    ),
    path(
        "portal/<str:token>/document/<int:pk>/",
        rental_premium_views.tenant_portal_document_download,
        name="tenant_portal_document_download",
    ),
    path(
        "portal/<str:token>/inspection/<int:pk>/fichier/",
        rental_premium_views.tenant_portal_inspection_download,
        name="tenant_portal_inspection_download",
    ),
    path(
        "portal/<str:token>/piece/<int:att_pk>/",
        rental_premium_views.tenant_portal_maintenance_attachment_download,
        name="tenant_portal_maintenance_attachment_download",
    ),
    path("portal/<str:token>/", rental_premium_views.tenant_portal, name="tenant_portal"),
    path("offres/", rental_views.public_offers_page, name="public_offers"),
    path("", rental_views.home, name="home"),
    path("dashboard/", rental_views.dashboard, name="dashboard"),
    path("suggestions/soumettre/", rental_views.suggestion_submit, name="suggestion_submit"),
    path("rental/", include("rental.urls", namespace="rental")),
    path("api/v1/", include("rental.api_urls")),
]

if settings.DEBUG:
    from django.conf.urls.static import static
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


