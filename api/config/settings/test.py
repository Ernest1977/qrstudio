"""Réglages de test : SQLite en mémoire, cache en mémoire, e-mails en boîte, Celery en direct.

Tout ce qui dépend d'un service externe (Redis, Postgres, MaxMind, Google) est soit remplacé par un
bouchon dans les tests, soit désactivé ici — la suite doit tourner sur une machine nue.
"""

from __future__ import annotations

from .base import *  # noqa: F403
from .base import QR

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:", "OPTIONS": {"timeout": 30}}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "tests"}}
SESSION_ENGINE = "django.contrib.sessions.backends.db"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",  # vitesse : on teste la logique, pas Argon2
]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Pas de DNS, pas de stream Redis, pas de garde anti-SSRF active : les tests qui veulent ces
# comportements les déclenchent explicitement via @override_settings.
QR = {
    **QR,
    "SHORT_BASE_URL": "http://testserver",
    "ALLOW_PRIVATE_TARGETS": True,
    "DNS_CHECK_ON_WRITE": False,
    "SCAN_STREAM_ENABLED": False,
    "GEO_ENABLED": False,
    "SCAN_RATE_PER_IP": 100_000,
}

THROTTLE_LOGIN = "10/min"

# Hachage rapide, assumé par ce module et par lui seul. `base.py` ne doit rien deviner de la façon
# dont il est lancé : l'ancienne heuristique (« pytest est dans sys.modules ») écrasait les réglages
# endurcis de `prod.py` et faisait passer Argon2 pour MD5 sans que personne ne le voie.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
