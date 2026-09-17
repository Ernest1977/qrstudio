"""Réglages de production (VPS Hostinger derrière Caddy). `manage.py check --deploy` doit passer."""

from __future__ import annotations

from .base import *  # noqa: F403
from .base import CACHES, EMAIL_HOST, QR, REDIS_URL, env

DEBUG = False

ALLOWED_HOSTS = env.get_list(
    "DJANGO_ALLOWED_HOSTS",
    ("qrstudio.kamcofarm.com",),
)
CSRF_TRUSTED_ORIGINS = env.get_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    ("https://qrstudio.kamcofarm.com",),
)

# --------------------------------------------------------------- transport
SECURE_SSL_REDIRECT = env.get_bool("SECURE_SSL_REDIRECT", True)

# Les sondes de santé arrivent en HTTP *à l'intérieur* du réseau du conteneur (healthcheck Docker,
# `health_uri` de Caddy, sonde du charge balancer Hostinger). Sans exemption, la redirection TLS
# renvoie un 301, la sonde échoue, et l'orchestrateur redémarre une application parfaitement saine.
SECURE_REDIRECT_EXEMPT = [r"^healthz/?$"]
SECURE_SSL_HOST = env.get("SECURE_SSL_HOST", "qrstudio.kamcofarm.com")
SECURE_HSTS_SECONDS = env.get_int("SECURE_HSTS_SECONDS", 60 * 60 * 24 * 365)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# Le `security.W021` de Django reussirait a faire croire qu'il faut activer le preload : l'inclusion
# dans la liste des navigateurs est quasi irreversible (elle survit au retrait du site) et concerne
# tout kamcofarm.com, pas seulement ce sous-domaine. On l'assume desactive, et on le met en liste
# noire plutot que de le laisser casser une CI qui refuse tout avertissement.
SILENCED_SYSTEM_CHECKS = ["security.W021"]
SECURE_HSTS_PRELOAD = env.get_bool("SECURE_HSTS_PRELOAD", False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False  # le front React doit pouvoir relire la jeton CSRF
CSRF_USE_SESSIONS = False
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# --------------------------------------------------------------- base / cache
DATABASES = {
    "default": env.parse_db_url(env.get_secret("DATABASE_URL"), conn_max_age=env.get_int("DB_CONN_MAX_AGE", 0)),
}
# En prod, la *connexion* de l'application va dans PgBouncer (port 6432) : CONN_MAX_AGE=0 parce
# que c'est PgBouncer qui pooling, pas Django. Les doubler épuise le serveur Postgres.
if "analytics_replica" in DATABASES:
    pass

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL or env.get_secret("REDIS_URL"),
        "KEY_PREFIX": "qrs",
        "TIMEOUT": 300,
        "OPTIONS": {
            # Une coupure de cache ne doit jamais casser un scan : la vue redirect a un repli DB.
            "IGNORE_EXCEPTIONS": False,
            "SOCKET_CONNECT_TIMEOUT": 1,
            "SOCKET_TIMEOUT": 1,
        },
    }
}
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

# --------------------------------------------------------------- e-mails
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = EMAIL_HOST or env.get("EMAIL_HOST", "smtp.kamcofarm.com")
EMAIL_PORT = env.get_int("EMAIL_PORT", 587)
EMAIL_USE_TLS = env.get_bool("EMAIL_USE_TLS", True)
EMAIL_HOST_USER = env.get("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = env.get("EMAIL_HOST_PASSWORD")
DEFAULT_FROM_EMAIL = env.get("DEFAULT_FROM_EMAIL", "QR Studio <itsupport@kamcofarm.com>")

# --------------------------------------------------------------- fichiers
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
# Les logos restent côté navigateur en prod (le front les encode en data URL) : aucun stockage
# utilisateur n'est requis au sprint 1. Si on en ajoute un, S3 compatible + URL signées 15 min.

QR = {
    **QR,
    "SHORT_BASE_URL": env.get("SHORT_BASE_URL", "https://qrstudio.kamcofarm.com").rstrip("/"),
    "ALLOW_PRIVATE_TARGETS": env.get_bool("QR_ALLOW_PRIVATE_TARGETS", False),  # anti-SSRF
    "DNS_CHECK_ON_WRITE": env.get_bool("QR_DNS_CHECK", True),
    "SCAN_STREAM_ENABLED": env.get_bool("QR_SCAN_STREAM_ENABLED", True),
}

# --------------------------------------------------------------- limites
DATA_UPLOAD_MAX_MEMORY_SIZE = env.get_int("DATA_UPLOAD_MAX_MEMORY_SIZE", 512 * 1024)
FILE_UPLOAD_MAX_MEMORY_SIZE = env.get_int("FILE_UPLOAD_MAX_MEMORY_SIZE", 512 * 1024)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"keyvalue": {"()": "apps.common.logging.KeyValueFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "keyvalue"}},
    "root": {"handlers": ["console"], "level": env.get("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.security.csrf": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.db.backends": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "apps": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
