# project/settings/base.py
from __future__ import annotations
from pathlib import Path
import environ

# ------------------------------------------------------------------------------
# Paths
# ------------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # .../project/

# ------------------------------------------------------------------------------
# Environment
# ------------------------------------------------------------------------------
env = environ.Env(
    DEBUG=(bool, False),
    SECRET_KEY=(str, "x#7%9A!tR@3vC^$pE2&Jm6W+K=Zs)8H0YwL4F_dqU*QG1B"),
    ALLOWED_HOSTS=(list, []),
)

ENV_FILE = BASE_DIR / ".env"
ENV_LOCAL_FILE = BASE_DIR / ".env.local"

if ENV_FILE.exists():
    environ.Env.read_env(str(ENV_FILE))

if ENV_LOCAL_FILE.exists():
    environ.Env.read_env(str(ENV_LOCAL_FILE), overwrite=True)

# ------------------------------------------------------------------------------
# Core Security / Debug
# ------------------------------------------------------------------------------
SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])

# ------------------------------------------------------------------------------
# App / invitation email defaults
# ------------------------------------------------------------------------------
APP_NAME = env("APP_NAME", default="MoneyPro")
DEFAULT_COMPANY_NAME = env("DEFAULT_COMPANY_NAME", default="Your Business")
SITE_URL = env("SITE_URL", default="")
REPLY_TO_EMAIL = env("REPLY_TO_EMAIL", default="")
SENDGRID_API_KEY = env("SENDGRID_API_KEY", default="")


# ------------------------------------------------------------------------------
# Application Definition
# ------------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.sites",    
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",  
]

THIRD_PARTY_APPS = [
    # Forms/UI
    "crispy_forms",
    "crispy_bootstrap5",
    "widget_tweaks",

    # Auth / security
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "axes",

    # API
    "rest_framework",
    "rest_framework_simplejwt",

    # Filtering / CORS (only needed if you serve API to a different origin)
    "django_filters",
    "corsheaders",

    # Object permissions / auditing
    "simple_history",

    # Admin import/export
    "import_export",

    # Scheduling
    "django_celery_beat",

    # Storage helpers (S3, etc.)
    "storages",

    # Timezone helpers (optional)
    "timezone_field",
]

LOCAL_APPS = [
    "accounts",
    "dashboard",
    "ledger",
    "reports",
    "core",
    "vehicles",
    "invoices",
    "contractor",
    "assets",
    "documents",
    "flightlogs",
    "operations",
    "pilot",
    "helpcenter",
]

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS + THIRD_PARTY_APPS

# ------------------------------------------------------------------------------
# Middleware
# ------------------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",

    "corsheaders.middleware.CorsMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",

    "axes.middleware.AxesMiddleware",

    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "core.middleware.ActiveBusinessMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",

    "django.middleware.clickjacking.XFrameOptionsMiddleware",

    "simple_history.middleware.HistoryRequestMiddleware",
    
    
    
]


ROOT_URLCONF = "project.urls"

# ------------------------------------------------------------------------------
# Templates
# ------------------------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"], 
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.company_context",
            ],
        },
    }
]

WSGI_APPLICATION = "project.wsgi.application"
ASGI_APPLICATION = "project.asgi.application"

# ------------------------------------------------------------------------------
# Database (default: Postgres; keep sqlite option for quick local runs)
# ------------------------------------------------------------------------------
DATABASE_URL_DEFAULT = f"sqlite:///{(BASE_DIR / 'db.sqlite3')}"
DATABASES = {
    "default": env.db("DATABASE_URL", default=DATABASE_URL_DEFAULT),
}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)

# ------------------------------------------------------------------------------
# Passwords / Auth
# ------------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Use Argon2 by default (you installed argon2-cffi)
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

AUTHENTICATION_BACKENDS = [
    # Axes must come first
    "axes.backends.AxesStandaloneBackend",

    # Default
    "django.contrib.auth.backends.ModelBackend",

    # allauth
    "allauth.account.auth_backends.AuthenticationBackend",
]

LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"
LOGIN_URL = "account_login"


# allauth (v65+)
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_UNIQUE_EMAIL = True

ACCOUNT_SIGNUP_FIELDS = [
    "email*",
    "password1*",
    "password2*",
]

ACCOUNT_EMAIL_VERIFICATION = env("ACCOUNT_EMAIL_VERIFICATION", default="optional")
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True

# Process A: invite-only
ACCOUNT_ALLOW_REGISTRATION = False

# Invite-only registration plumbing (accounts app)
ACCOUNT_ADAPTER = "accounts.adapters.InviteOnlyAccountAdapter"
ACCOUNT_FORMS = {"signup": "accounts.forms.InviteSignupForm"}

SITE_ID = env.int("SITE_ID", default=1)

# ------------------------------------------------------------------------------
# Internationalization
# ------------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", default="America/Indiana/Indianapolis")
USE_I18N = True
USE_TZ = True

# ------------------------------------------------------------------------------
# Static & Media (Render-friendly; S3 can override in prod)
# ------------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

STORAGES = {
    "default": {
        # local media by default; override in prod for S3
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        # WhiteNoise compressed manifest
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ------------------------------------------------------------------------------
# Default primary key
# ------------------------------------------------------------------------------
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ------------------------------------------------------------------------------
# Messages (Bootstrap-friendly)
# ------------------------------------------------------------------------------
from django.contrib.messages import constants as messages  # noqa: E402

MESSAGE_TAGS = {
    messages.DEBUG: "debug",
    messages.INFO: "info",
    messages.SUCCESS: "success",
    messages.WARNING: "warning",
    messages.ERROR: "danger",
}

# crispy-forms
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

# ------------------------------------------------------------------------------
# Django REST Framework + JWT
# ------------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
}

# ------------------------------------------------------------------------------
# CORS (keep locked down; open only as needed)
# ------------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True

# ------------------------------------------------------------------------------
# Axes (basic sane defaults; tighten further in prod.py)
# ------------------------------------------------------------------------------
AXES_FAILURE_LIMIT = env.int("AXES_FAILURE_LIMIT", default=5)
AXES_COOLOFF_TIME = env.int("AXES_COOLOFF_TIME", default=1)  # hours
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_PARAMETERS = ["ip_address", "username"]

# -----------------------------------------------------------------------------
# Simple History
# ------------------------------------------------------------------------------
SIMPLE_HISTORY_HISTORY_ID_USE_UUID = True

# ------------------------------------------------------------------------------
# Celery / Redis (Render often uses Redis add-on; safe defaults)
# ------------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=CELERY_BROKER_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# ------------------------------------------------------------------------------
# Logging (safe baseline; prod.py can push to JSON/log drain)
# ------------------------------------------------------------------------------
LOG_LEVEL = env("LOG_LEVEL", default="INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
}

# ------------------------------------------------------------------------------
# Email (override in dev/prod as needed)
# ------------------------------------------------------------------------------
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", default="smtp.placeholder.local")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="placeholder_user")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="placeholder_password")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@example.test")
REPLY_TO_EMAIL = env("REPLY_TO_EMAIL", default=DEFAULT_FROM_EMAIL)

# ------------------------------------------------------------------------------
# Security headers (baseline; prod.py will enforce HTTPS/HSTS)
# ------------------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_HTTPONLY = True

CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SAMESITE = "Lax"

CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# Behind proxies (Render). Doesn't force HTTPS by itself.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# If you end up embedding as a PWA only, keep DENY; if you need framing, adjust.


DEFAULT_COMPANY_NAME = env("DEFAULT_COMPANY_NAME", default="")


DATE_FORMAT = "m-d-Y"
SHORT_DATE_FORMAT = "m-d-Y"
DATETIME_FORMAT = "m-d-Y P"
SHORT_DATETIME_FORMAT = "m-d-Y P"

AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]

BUSINESS_EMAIL_PLATFORM_DOMAIN = env("BUSINESS_EMAIL_PLATFORM_DOMAIN", default="moneypro.12bytes.net")
BUSINESS_EMAIL_LOCALPART = env("BUSINESS_EMAIL_LOCALPART", default="noreply")

# ------------------------------------------------------------------------------
# Business backup retention
# ------------------------------------------------------------------------------
MONEYPRO_BACKUP_RETENTION_DAYS = env.int("MONEYPRO_BACKUP_RETENTION_DAYS", default=7)
