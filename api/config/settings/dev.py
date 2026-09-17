"""Réglages de développement : tout doit tourner sans Docker, sans Redis, sans Postgres."""

from __future__ import annotations

from .base import *  # noqa: F403
from .base import BASE_DIR, QR, env

DEBUG = env.get_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = ["*"] if DEBUG else ALLOWED_HOSTS

# En dev on tape SQLite : `manage.py runserver` suffit, aucune conteneurisation requise.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": env.get("DEV_SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
    }
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Le banc de tests du front (`qr-coding-react/scripts/facturation.api.test.mjs`) pilote un vrai navigateur
# depuis **une seule IP** : avec les taux de production (`register` 5/h, `login` 10/min), le deuxieme run de
# la meme heure repond 429 avant meme que le test puisse regarder ce qu'il est cense regarder — et l'avis
# « requête ralentie » ressemble alors a une regression de la caisse. En dev, le bridge ne prouve rien : on
# l'ouvre. Les taux reellement servis restent ceux de `base.py`/`prod.py`, et les 429 sont couverts par les
# tests d'API (`tests/test_rate_limit.py`), qui posent leurs propres reglages.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {
        **REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
        "login": env.get("THROTTLE_LOGIN", "1000/min"),
        "register": env.get("THROTTLE_REGISTER", "1000/hour"),
        "verify": env.get("THROTTLE_VERIFY", "1000/min"),
        "password_reset": env.get("THROTTLE_PASSWORD_RESET", "1000/hour"),
    },
}

# Le dev local n'est pas en HTTPS : les cookies `Secure` rendraient le flux inutilisable.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
# Le front relit la jeton après un rechargement: `login` fait `rotate_token()`, donc le cookie est
# émis avec le jeton neuf, et sans ce drapeau le front n'y toucherait plus une fois la page
# rechargée — chaque POST authentifié partirait sans jeton et reviendrait en 403 CSRF. `prod.py`
# lève déjà cette interdiction pour la même raison; en le gardant ici, le dev ne change pas de
# comportement au déploiement.
CSRF_COOKIE_HTTPONLY = False
# Vite proxifie /api avec `changeOrigin: true`: Django voit donc un `Host: 127.0.0.1:8000` alors que
# l'`Origin`/`Referer` du navigateur porte l'origine du front (ou celle du proxy d'aperçu). Sans ces
# origines de confiance, toute écriture authentifiée depuis le front est rejetée « Referer checking
# failed » — un 403 qui ne ressemble en rien à une erreur de saisie.
CSRF_TRUSTED_ORIGINS = [
    *CSRF_TRUSTED_ORIGINS,
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    # Derrière l'aperçu, l'hôte est `https://<port>-<id>.e2b.app`: un seul motif, tous les ports.
    "https://*.e2b.app",
]
SECURE_SSL_REDIRECT = False
ADMIN_REQUIRE_MFA = False  # pas de TOTP en local
SECURE_HSTS_SECONDS = 0

INSTALLED_APPS = [*INSTALLED_APPS]
if env.get_bool("DJANGO_EXTENSIONS", True):
    try:
        import django_extensions

        INSTALLED_APPS.append("django_extensions")
    except ImportError:
        pass

# Les QR vers 127.0.0.1 (le front Vite, un serveur de test) sont légitimes en dev.
QR = {**QR, "ALLOW_PRIVATE_TARGETS": True, "SCAN_STREAM_ENABLED": env.get_bool("QR_SCAN_STREAM_ENABLED", False)}
