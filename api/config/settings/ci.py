"""Réglages de la CI : la prod, mais avec les secrets de test et sans services externes requis.

Ce module existe pour une raison précise : `config.settings.test` (SQLite + LocMemCache) ne dit rien
des chemins SQL écrits pour PostgreSQL — partitionnement, `INSERT … ON CONFLICT`, index partiels. En
CI on veut que ces requêtes soient réellement exécutées, sur le moteur de la production, tout en
gardant la vitesse d'exécution (hachages rapides, pas de HTTP réel).
"""

from __future__ import annotations

from .base import *  # noqa: F403
from .prod import *  # noqa: F403  : on veut les garde-fous de prod (HSTS, cookies sûrs, argon2…)

DEBUG = False
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
CSRF_TRUSTED_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000"]

# Le test d'énumération d'e-mails et la vue de vérification écrivent dans la boîte d'envoi : en CI
# comme en test, rien ne sort du réseau.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Hachages rapides : les tests créent des centaines de comptes, Argon2 noierait le temps de CI dans
# du calcul sans rien prouver. La valeur *de prod* est testée par `tests/test_security.py`.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Le cache de résolution des slugs doit être *partagé* entre les workers de la CI pour que le test
# « le deuxième scan ne touche pas la base » reste vrai ; Redis est fourni comme service.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": __import__("os").environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
        "KEY_PREFIX": "ci",
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.db"  # pas de dépendance au cache pour les tests de session
SCAN_STREAM_ENABLED = False  # la file est éprouvée par ses propres tests, pas par les détours
MAINTENANCE_MODE = False
QR = {**QR, "SCAN_STREAM_ENABLED": False, "SHORT_BASE_URL": "http://localhost:8000"}
