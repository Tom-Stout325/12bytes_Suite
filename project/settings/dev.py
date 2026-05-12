# project/settings/dev.py
from .base import *  # noqa: F403
from .base import env  # noqa: F401

# ------------------------------------------------------------------------------
# Core dev toggles
# ------------------------------------------------------------------------------
DEBUG = True
SECRET_KEY = env("SECRET_KEY", default=SECRET_KEY)  # noqa: F405

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["127.0.0.1", "localhost"])

# ------------------------------------------------------------------------------
# Email
# ------------------------------------------------------------------------------
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)

# ------------------------------------------------------------------------------
# Security (relaxed for local dev)
# ------------------------------------------------------------------------------
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Helpful during local dev (avoid “CSRF origin checking failed” when testing)
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# ------------------------------------------------------------------------------
# CORS (only needed if your frontend is served from another origin)
# ------------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True


# ------------------------------------------------------------------------------
# Django Extensions / local conveniences (optional)
# ------------------------------------------------------------------------------
# If you add more dev-only packages later, add them here safely.

# ------------------------------------------------------------------------------
# Celery in dev (optional: run tasks eagerly without a broker)
# ------------------------------------------------------------------------------
if env.bool("CELERY_TASK_ALWAYS_EAGER", default=False):
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True



