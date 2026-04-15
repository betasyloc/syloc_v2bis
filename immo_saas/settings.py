from pathlib import Path
import os

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-secret-key-change-me",
)

DEBUG = os.environ.get("DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = os.environ.get(
    "ALLOWED_HOSTS",
    "127.0.0.1,localhost,.ngrok-free.dev,.ngrok-free.app",
).split(",")

# Cache (requis pour django-ratelimit ; en prod multi-worker, préférer Redis)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "syloc-cache",
    }
}
if os.environ.get("REDIS_URL"):
    CACHES["default"] = {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ["REDIS_URL"],
    }

# Limitation des tentatives de connexion (anti brute-force)
RATELIMIT_ENABLE = True
RATELIMIT_LOGIN = "5/5m"  # 5 tentatives par 5 minutes par IP
RATELIMIT_VIEW = "rental.views.ratelimit_blocked"

# HTTPS / CSRF : autoriser les origines de confiance (Render, ngrok, etc.)
if url := os.environ.get("RENDER_EXTERNAL_URL"):
    CSRF_TRUSTED_ORIGINS = [url.rstrip("/")]
elif os.environ.get("CSRF_TRUSTED_ORIGINS"):
    CSRF_TRUSTED_ORIGINS = [
        s.strip() for s in os.environ["CSRF_TRUSTED_ORIGINS"].split(",")
    ]
else:
    # Développement local avec ngrok (domaines gratuits .app / .dev selon la version)
    CSRF_TRUSTED_ORIGINS = [
        "https://*.ngrok-free.dev",
        "https://*.ngrok-free.app",
    ]

# Reverse proxy (Render, Railway, Fly, etc.) : le client est en HTTPS mais Gunicorn/Uvicorn reçoit du HTTP.
# Sans cela, request.is_secure() est faux → cookies « Secure », redirections et CSRF peuvent casser la connexion.
_use_fwd_proto = os.environ.get("USE_X_FORWARDED_PROTO", "").lower() in ("true", "1", "yes")
if os.environ.get("RENDER") or _use_fwd_proto:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rental",
]


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "rental.middleware.TenantPortalIsolationMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "rental.middleware.DiscoveryReadOnlyMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_ratelimit.middleware.RatelimitMiddleware",
]


ROOT_URLCONF = "immo_saas.urls"


TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "rental.context_processors.subscription",
                "rental.context_processors.contextual_tips",
                "rental.context_processors.static_asset_version",
                "rental.context_processors.tenant_portal_shell",
            ],
        },
    },
]


WSGI_APPLICATION = "immo_saas.wsgi.application"


if os.environ.get("DATABASE_URL"):
    DATABASES = {"default": dj_database_url.config(conn_max_age=600)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


LANGUAGE_CODE = "fr"

TIME_ZONE = "Europe/Paris"

USE_I18N = True

USE_TZ = True


STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
# Incrémente ou définit STATIC_ASSET_VERSION=… en env pour forcer le rechargement CSS/JS (cache navigateur).
STATIC_ASSET_VERSION = os.environ.get("STATIC_ASSET_VERSION", "71")
# En dev, fichiers servis directement depuis STATICFILES_DIRS (pas besoin de collectstatic à chaque nouvel asset).
# En prod, manifest + compression WhiteNoise pour le cache longue durée.
if DEBUG:
    STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"
else:
    STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTHENTICATION_BACKENDS = [
    "rental.auth_backends.EmailAuthBackend",
    "django.contrib.auth.backends.ModelBackend",
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "home"

# Session utilisateur : sécurité renforcée (local + Render)
# - SESSION_EXPIRE_AT_BROWSER_CLOSE=True : déconnecte à la fermeture du navigateur
# - SESSION_COOKIE_AGE : durée max de session en secondes (défaut 12h)
SESSION_COOKIE_AGE = int(os.environ.get("SESSION_COOKIE_AGE", "43200"))
SESSION_EXPIRE_AT_BROWSER_CLOSE = (
    os.environ.get("SESSION_EXPIRE_AT_BROWSER_CLOSE", "true").lower()
    in ("true", "1", "yes", "on")
)

# Email (signatures électroniques, quittances, relances, rappels, identifiant oublié)
# Si EMAIL_HOST est défini dans .env → envoi de vrais mails via SMTP.
# Sinon → backend console (mails affichés dans le terminal uniquement).
_email_host = (os.environ.get("EMAIL_HOST") or "").strip()
if _email_host:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = _email_host
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "false").lower() in ("true", "1", "yes")
    EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "true").lower() in ("true", "1", "yes")
    if EMAIL_USE_SSL:
        # TLS implicite (465) et STARTTLS (587) sont exclusifs.
        EMAIL_USE_TLS = False
    EMAIL_TIMEOUT = int(os.environ.get("EMAIL_TIMEOUT", "10"))
    EMAIL_HOST_USER = (os.environ.get("EMAIL_HOST_USER") or "").strip()
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD") or ""
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
# Expéditeur des mails : si non défini et SMTP configuré, utiliser le compte SMTP pour cohérence
_default_from = os.environ.get("DEFAULT_FROM_EMAIL", "").strip()
if _email_host and EMAIL_HOST_USER and not _default_from:
    DEFAULT_FROM_EMAIL = EMAIL_HOST_USER
else:
    DEFAULT_FROM_EMAIL = _default_from or "noreply@immo-saas.local"
SERVER_EMAIL = DEFAULT_FROM_EMAIL
# Stripe (abonnements)
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
# ID du prix Stripe pour Premium (créer dans le dashboard Stripe) ; peut aussi être défini sur le modèle Plan
STRIPE_PRICE_ID_PREMIUM = os.environ.get("STRIPE_PRICE_ID_PREMIUM", "")
# Laisser vide par défaut : sinon d’anciens ID « exemple » pouvaient s’appliquer et provoquer
# l’erreur « mensuel vs annuel » si ce ne sont pas vos vrais Price Stripe.
STRIPE_PRICE_ID_PREMIUM_ANNUAL = os.environ.get("STRIPE_PRICE_ID_PREMIUM_ANNUAL", "")
# Produit Stripe du Premium annuel (référence ; l’API utilise le Price ci-dessus)
STRIPE_PRODUCT_ID_PREMIUM_ANNUAL = os.environ.get(
    "STRIPE_PRODUCT_ID_PREMIUM_ANNUAL",
    "prod_UEL187XGgiUIrX",
)
# Abonnement Basic (surchargeable via STRIPE_PRICE_ID_BASE / STRIPE_PRICE_ID_BASE_ANNUAL)
STRIPE_PRODUCT_ID_BASE = os.environ.get("STRIPE_PRODUCT_ID_BASE", "")
STRIPE_PRICE_ID_BASE = os.environ.get("STRIPE_PRICE_ID_BASE", "")
STRIPE_PRICE_ID_BASE_ANNUAL = os.environ.get("STRIPE_PRICE_ID_BASE_ANNUAL", "")
# Produit Stripe du Basic annuel (référence ; l’API d’abonnement utilise le Price ci-dessus)
STRIPE_PRODUCT_ID_BASE_ANNUAL = os.environ.get(
    "STRIPE_PRODUCT_ID_BASE_ANNUAL",
    "prod_UEL2E1ZGD7DSAv",
)

# Abonnement : mode test sans Stripe (formule + palier volume modifiables sur la page Abonnement).
# SYLOC_SUBSCRIPTION_SANDBOX=1 ou 0 surcharge ; si non défini → activé quand DEBUG=True.
_syloc_sbx = (os.environ.get("SYLOC_SUBSCRIPTION_SANDBOX") or "").strip().lower()
if _syloc_sbx in ("1", "true", "yes", "on"):
    SYLOC_SUBSCRIPTION_SANDBOX = True
elif _syloc_sbx in ("0", "false", "no", "off"):
    SYLOC_SUBSCRIPTION_SANDBOX = False
else:
    SYLOC_SUBSCRIPTION_SANDBOX = bool(DEBUG)

# IA analyse rentabilité (Premium) – optionnel
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# Suggestions tableau de bord : emails staff (séparés par des virgules). Si vide → tous les comptes staff avec email.
_SUGGESTION_EMAILS_RAW = (os.environ.get("SUGGESTION_STAFF_EMAILS") or "").strip()
SUGGESTION_STAFF_EMAILS = [e.strip() for e in _SUGGESTION_EMAILS_RAW.split(",") if e.strip()]

# Pages légales (mentions / confidentialité) : personnaliser en production
SYLOC_LEGAL_PUBLISHER = (os.environ.get("SYLOC_LEGAL_PUBLISHER") or "").strip()
SYLOC_LEGAL_SIRET = (os.environ.get("SYLOC_LEGAL_SIRET") or "").strip()
SYLOC_LEGAL_ADDRESS = (os.environ.get("SYLOC_LEGAL_ADDRESS") or "").strip()
SYLOC_LEGAL_CONTACT_EMAIL = (os.environ.get("SYLOC_LEGAL_CONTACT_EMAIL") or "").strip()
SYLOC_LEGAL_HOSTING_PROVIDER = (os.environ.get("SYLOC_LEGAL_HOSTING_PROVIDER") or "Render").strip()

# --- Sécurité renforcée en production (DEBUG=False) ---
# Ne forcer HTTPS que si on n'est pas en local (évite de bloquer l'accès en dev)
_allow_redirect_ssl = not DEBUG and "127.0.0.1" not in ALLOWED_HOSTS and "localhost" not in ALLOWED_HOSTS

if not DEBUG:
    SECURE_SSL_REDIRECT = _allow_redirect_ssl
    SESSION_COOKIE_SECURE = _allow_redirect_ssl
    CSRF_COOKIE_SECURE = _allow_redirect_ssl
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
    # HSTS (optionnel, décommenter quand HTTPS stable)
    # SECURE_HSTS_SECONDS = 31536000
    # SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    # SECURE_HSTS_PRELOAD = True

