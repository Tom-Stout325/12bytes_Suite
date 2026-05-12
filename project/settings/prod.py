# project/settings/prod.py
from .base import *  # noqa: F403
from .base import env

# ------------------------------------------------------------------------------
# Core production toggles
# ------------------------------------------------------------------------------
DEBUG = False

# MUST be provided in env vars
SECRET_KEY = env("SECRET_KEY")

# Hostnames + your custom domains go here (comma-separated list in env)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])

# ------------------------------------------------------------------------------
# HTTPS / proxy
# ------------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# Force HTTPS
SECURE_SSL_REDIRECT = True

# Cookies
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Recommended: mitigate CSRF token leakage
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SAMESITE = "Lax"

# Your app likely needs these for allauth flows / PWA
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# ------------------------------------------------------------------------------
# HSTS
# ------------------------------------------------------------------------------
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 30)  # 30 days
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True)
SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)

# Security headers
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

# ------------------------------------------------------------------------------
# Database
# ------------------------------------------------------------------------------
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=120)
DATABASES["default"]["OPTIONS"] = DATABASES["default"].get("OPTIONS", {})
DATABASES["default"]["OPTIONS"]["sslmode"] = env("DB_SSLMODE", default="require")

# ------------------------------------------------------------------------------
# Static files
# WhiteNoise is configured in base.py via STORAGES.
# Keep staticfiles on WhiteNoise/STATIC_ROOT for Heroku deploys.
# ------------------------------------------------------------------------------
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# Preserve base.py STORAGES so staticfiles continues to use WhiteNoise.
STORAGES = STORAGES.copy()  # noqa: F405

# ------------------------------------------------------------------------------
# Media / Uploads (optional: S3). Leave local filesystem unless you set USE_S3=True.
# ------------------------------------------------------------------------------
USE_S3 = env.bool("USE_S3", default=False)

if USE_S3:
    # AWS credentials + bucket must be provided via env vars
    AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default=None)

    AWS_S3_SIGNATURE_VERSION = "s3v4"
    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = None

    # Cache headers for S3 objects (tweak as desired)
    AWS_S3_OBJECT_PARAMETERS = {
        "CacheControl": "max-age=86400",
    }

    # Use distinct locations for static and media
    AWS_LOCATION_STATIC = env("AWS_LOCATION_STATIC", default="static")
    AWS_LOCATION_MEDIA = env("AWS_LOCATION_MEDIA", default="media")

    # Override only media storage.
    # Do NOT override staticfiles storage here; let WhiteNoise handle static assets.
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        "OPTIONS": {
            "location": AWS_LOCATION_MEDIA,
        },
    }

# ------------------------------------------------------------------------------
# Email (SendGrid SMTP in production)
# ------------------------------------------------------------------------------
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", default="smtp.sendgrid.net")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="apikey")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default=env("SENDGRID_API_KEY", default=""))
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@moneypro.12bytes.net")
REPLY_TO_EMAIL = env("REPLY_TO_EMAIL", default=DEFAULT_FROM_EMAIL)

# ------------------------------------------------------------------------------
# Allauth safety: ensure correct scheme behind proxy
# ------------------------------------------------------------------------------
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https"

# ------------------------------------------------------------------------------
# CORS (keep closed unless you explicitly need it)
# ------------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

# ------------------------------------------------------------------------------
# Axes (tighten in prod)
# ------------------------------------------------------------------------------
AXES_FAILURE_LIMIT = env.int("AXES_FAILURE_LIMIT", default=5)
AXES_COOLOFF_TIME = env.int("AXES_COOLOFF_TIME", default=24)  # hours
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]

# ------------------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------------------
LOG_LEVEL = env("LOG_LEVEL", default="INFO")
LOGGING = {  # noqa: F405
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
}

# ------------------------------------------------------------------------------
# Optional: tighten DRF in prod (keep if you use APIs)
# ------------------------------------------------------------------------------
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = (  # noqa: F405
    "rest_framework.renderers.JSONRenderer",
)

# ------------------------------------------------------------------------------
# Optional: protect admin
# ------------------------------------------------------------------------------
# Example:
# ADMIN_IP_WHITELIST = env.list("ADMIN_IP_WHITELIST", default=[])

ROOT_URLCONF = "project.urls"
WSGI_APPLICATION = "project.wsgi.application"

