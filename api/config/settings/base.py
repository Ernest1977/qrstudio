# Réglages communs. Ce module ne doit jamais contenir de secret ni de compromis "pratique".
# Hiérarchie : base.py <- dev.py (localement, SQLite + cache mémoire) <- prod.py (Postgres + Redis
# + durcissement TLS/cookies). `test.py` hérite de dev avec des hachages rapides.
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from config import env

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV = env.get("DJANGO_ENV", "dev")

# --------------------------------------------------------------------- général
# SECRET_KEY : en dev on accepte une valeur fixe, en prod `env.get_secret` lève une erreur.
SECRET_KEY = env.get_secret("DJANGO_SECRET_KEY", "dev-inseguro-a-remplacer-0000000000000000000")
DEBUG = False
ALLOWED_HOSTS = env.get_list("DJANGO_ALLOWED_HOSTS", ("localhost", "127.0.0.1"))
CSRF_TRUSTED_ORIGINS = env.get_list("DJANGO_CSRF_TRUSTED_ORIGINS", ())
USE_X_FORWARDED_HOST = env.get_bool("DJANGO_USE_X_FORWARDED_HOST", True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# `qrstudio` n'a pas de `www` : le sous-domaine est fixé par le QR imprimé, il ne bouge plus.
ADMIN_PATH = env.get("DJANGO_ADMIN_PATH", "manage-9f2/").strip("/") or "manage-9f2"
ADMIN_URL = f"{ADMIN_PATH}/"

# Deuxième facteur exigé du personnel d'admin. Défini dans la base, et non seulement en prod : le
# middleware lit cette clé, une clé absente des réglages de test rendait le garde impossible à
# éprouver. `dev.py` le désactive pour le confort local.
ADMIN_REQUIRE_MFA = env.get_bool("DJANGO_ADMIN_REQUIRE_MFA", True)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.humanize",
    # tiers
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.mfa",  # second facteur du personnel, exigé par apps/common/urls_admin.py
    # projet
    "apps.common",
    "apps.accounts",
    "apps.qr",
    "apps.redirect",
    "apps.analytics",
    "apps.billing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # sert les statiques si le CDN est absent
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # les nôtres, en fin de chaîne pour voir request.user
    "apps.common.middleware.ClientIPMiddleware",
    "apps.common.middleware.MfaGateMiddleware",  # 2FA du personnel sur ADMIN_URL (voir staff_access)
    "apps.common.middleware.AdminHardeningMiddleware",
    "apps.common.middleware.CspMiddleware",  # politique de contenu sur les reponses HTML (voir apps.common.csp)
]

# --------------------------------------------------------------------- politique de contenu (CSP)
# `CSP_MODE` : `off` | `report-only` | `apply`. Laisse vide, le middleware choisit `off` en debug et
# `report-only` sinon — la page de traceback de Django pose ses propres scripts en ligne, une politique
# appliquee l'aveuglerait au moment precis ou on en a besoin.
CSP_MODE = env.get("CSP_MODE", "")
# Les rapports de violation partent sur `/csp-violation/` (view maison, journalise une fois par minute et
# par adresse). A couper si vous n'exploitez pas le journal : un rapport qu'on ne lit pas est un bruit.
CSP_SIGNALER = env.get_bool("CSP_SIGNALER", True)
# Echappement documente : si une mise a jour d'allauth ou de l'admin introduit un vrai script en ligne,
# l'operateur ajoute ici la directive supplementaire (hash ou `'unsafe-inline'` provisoire) sans patcher
# le code. Vide = `script-src 'self' 'nonce-…'`, la forme stricte.
CSP_SCRIPT_SRC_EXTRA = env.get("CSP_SCRIPT_SRC_EXTRA", "")

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
            ],
            "string_if_invalid": "TMPL_INVALID",  # un `{{ champ inexistant }}` doit se voir
        },
    },
]

# --------------------------------------------------------------------- base / cache
DATABASES = {
    "default": env.parse_db_url(
        env.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        conn_max_age=env.get_int("DB_CONN_MAX_AGE", 600),
    )
}
# L'app `analytics` écrit beaucoup : on la dirige vers la réplique de lecture si elle existe.
if env.get("DATABASE_URL_REPLICA"):
    DATABASES["analytics_replica"] = env.parse_db_url(env.get("DATABASE_URL_REPLICA"))

REDIS_URL = env.get("REDIS_URL", "")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "qrstudio",
    }
}
SESSION_ENGINE = "django.contrib.sessions.backends.db"
if REDIS_URL:
    CACHES["default"] = {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "qrs",
        "TIMEOUT": 300,
    }
    # `cached_db` : lecture Redis (rapide), persistance Postgres (les sessions survivent à un
    # purge de Redis — sinon 1 M d'utilisateurs se font déconnecter d'un coup).
    SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

# --------------------------------------------------------------------- auth
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/manage/"
LOGOUT_REDIRECT_URL = "/"

# --------------------------------------------------------------------- facturation
# Le SDK Stripe n'est pas requis : trois routes REST et une signature HMAC (voir
# `apps/billing/stripe_api.py`). Ce qui ne doit surtout pas vivre ici : les **prix**. Ils sont dans
# `apps/accounts/plans.py`, et seuls les `price_id` du compte Stripe viennent par l'environnement — la
# garde monetaire de `services.verifier_montant` compare les deux a chaque webhook.
STRIPE_SECRET_KEY = env.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = env.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = env.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_IDS = {
    "standard": env.get("STRIPE_PRICE_STANDARD", ""),
    "premium": env.get("STRIPE_PRICE_PREMIUM", ""),
    "business": env.get("STRIPE_PRICE_BUSINESS", ""),
}
BILLING_DEVISE = env.get("BILLING_DEVISE", "EUR")
# Un paiement qui echoue ne coupe pas le service seance tenante : la banque repond lundi, le client
# scanne son QR dimanche. Le sursis est la duree de cette politesse, pas une periode de grace facturable.
BILLING_GRACE_DAYS = env.get_int("BILLING_GRACE_DAYS", 3)
# Les portefeuilles du telephone, actifs sur la meme session Checkout : c'est le « paiement mobile »
# qui ne coute ni contrat ni secret supplementaire. Vider la liste desactive Apple/Google Pay.
BILLING_WALLETS = [w for w in env.get("BILLING_WALLETS", "apple_pay,google_pay").split(",") if w]
# TVA francaise/italienne appliquee aux prix affiches TTC : le taux sert aux ecritures et a la
# reconstruction du hors-taxe sur la facture, pas au calcul du debit.
BILLING_TVA_TAUX = float(env.get("BILLING_TVA_TAUX", "22"))

# Agrегateur de paiement mobile (Satispay, Orange Money, MTN MoMo, CinetPay...). `none` = route
# volontairement en 503 ; `demo` = filament de simulation, autorise seulement si on l'assume.
MOBILE_MONEY_PROVIDER = env.get("MOBILE_MONEY_PROVIDER", "none")
MOBILE_MONEY_API_KEY = env.get("MOBILE_MONEY_API_KEY", "")
MOBILE_MONEY_WEBHOOK_SECRET = env.get("MOBILE_MONEY_WEBHOOK_SECRET", "")
MOBILE_MONEY_ALLOW_DEMO = env.get_bool("MOBILE_MONEY_ALLOW_DEMO", False)

# --- Flutterwave (MTN MoMo, Airtel Money, M-PESA, Orange MoMo) -------------------------------
# Un seul dict : la devise facturee, le type de charge et le taux forment un reglage coherent, les
# morceler en variables independantes laisserait pointer `type=mpesa` avec `devise=UGX`.
# `taux_change` = nombre d'unites de la devise du reseau pour 1 EUR. Vide = aucune conversion admise
# (l'adaptateur refuse plutot que de deviner), et le recoupement du rappel se fait alors en euros.
MOBILE_MONEY_FLUTTERWAVE = {
    "base_url": env.get("MOBILE_MONEY_FLUTTERWAVE_BASE_URL", "https://api.flutterwave.com"),
    "secret_key": env.get("MOBILE_MONEY_FLUTTERWAVE_SECRET_KEY", ""),
    # Le « secret hash » saisi dans Settings > Webhooks du tableau de bord Flutterwave. Sans lui,
    # aucun rappel ne peut etre verifie : on garde donc la cle, sinon rien n'est jamais accorde.
    "secret_hash": env.get("MOBILE_MONEY_FLUTTERWAVE_SECRET_HASH", ""),
    "devise": env.get("MOBILE_MONEY_FLUTTERWAVE_DEVISE", BILLING_DEVISE),
    "type": env.get("MOBILE_MONEY_FLUTTERWAVE_TYPE", "mobile_money_uganda"),
    "reseau": env.get("MOBILE_MONEY_FLUTTERWAVE_RESEAU", ""),
    "pays": env.get("MOBILE_MONEY_FLUTTERWAVE_PAYS", ""),
    "taux_change": env.get("MOBILE_MONEY_FLUTTERWAVE_TAUX", ""),
    "arrondi": env.get("MOBILE_MONEY_FLUTTERWAVE_ARRONDI", "0.01"),
    "timeout": env.get("MOBILE_MONEY_FLUTTERWAVE_TIMEOUT", "12"),
}

# --------------------------------------------------------------------- allauth

# Note d'environnement: `allauth.mfa.stages` importe les flux webauthn meme si le type n'est pas
# active (chemin d'import de la bibliotheque, pas le notre). `cryptography` doit donc etre installee
# correctement — une extension binaire manquante fait planter le demarrage sur `include("allauth.urls")`,
# loin de toute idee recue. Les types actifs sont bornes plus bas dans ce fichier.
SITE_ID = 1
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "optional"  # on gère le code à 6 chiffres côté API (apps.accounts)
ACCOUNT_SESSION_REMEMBER = False
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_ADAPTER = "apps.accounts.adapters.StudioAccountAdapter"
ACCOUNT_FORMS = {}
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes"]
MFA_TOTP_ISSUER = env.get("MFA_TOTP_ISSUER", "QR Studio")
MFA_ALLOWED_TYPES = ["totp", "recovery_codes"]
SOCIALACCOUNT_PROVIDERS = {}
_google_client = env.get("GOOGLE_OAUTH2_CLIENT_ID")
if _google_client:
    SOCIALACCOUNT_PROVIDERS["google"] = {
        "APPS": [
            {
                "client_id": _google_client,
                "secret": env.get("GOOGLE_OAUTH2_CLIENT_SECRET"),
                "key": "",
            }
        ],
        "SCOPE": ["openid", "email", "profile"],
        # `prompt=select_account` : éviter le compte Google "collé" sur un poste partagé.
        "AUTH_PARAMS": {"access_type": "online", "prompt": "select_account"},
        # Vérification stricte de l'e-mail : on ne lie un compte que si Google l'a confirmé.
        "VERIFIED_EMAIL": True,
    }
    SOCIALACCOUNT_EMAIL_VERIFICATION = "none"  # Google a déjà vérifié
    SOCIALACCOUNT_ADAPTORS = {}

# --------------------------------------------------------------------- DRF
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",  # cookie same-site + CSRF
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.CursorById",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "apps.common.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "login": env.get("THROTTLE_LOGIN", "10/min"),
        "register": env.get("THROTTLE_REGISTER", "5/hour"),
        "password_reset": "5/hour",
        "verify": "10/min",
        "qr_write": env.get("THROTTLE_QR_WRITE", "120/min"),
        "stats": "60/min",
        # Le rendu d'image est le seul endpoint qui fabrique du CPU par requete: il doit etre borne
        # independamment des lectures de liste, sinon un front qui recharge 40 apercuts par seconde
        # transforme une page de gestion en attaque par déni gratuite.
        "qr_image": env.get("THROTTLE_QR_IMAGE", "60/min"),
        # Le rendu verifie (art anime) est le seul endpoint qui fait du dessin **et** de la vision par
        # ordinateur par requete. 30/min par compte suffit largement a un studio, et protege les workers.
        "qr_art": env.get("THROTTLE_QR_ART", "30/min"),
    },
    "EXCEPTION_HANDLER": "apps.common.exceptions.api_exception_handler",
    "UNAUTHENTICATED_USER": None,
}
SPECTACULAR_SETTINGS = {
    "TITLE": "QR Studio API",
    "DESCRIPTION": "Comptes, QR statiques/dynamiques, statistiques de scan. Cookies de session + CSRF.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "CAMELIZE_NAMES": False,
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
    ],
}

# --------------------------------------------------------------------- Celery
CELERY_BROKER_URL = env.get("CELERY_BROKER_URL", REDIS_URL or "memory://")
CELERY_RESULT_BACKEND = env.get("CELERY_RESULT_BACKEND", "django-db" if False else "cache+memory://")
CELERY_TASK_ALWAYS_EAGER = env.get_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TASK_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1000
CELERY_BEAT_SCHEDULE = {
    "garde-partitions": {
        "task": "apps.analytics.tasks.ensure_scan_partitions",
        "schedule": 60 * 60 * 6,  # 6 h
    },
    "agrege-stats": {
        "task": "apps.analytics.tasks.rebuild_daily_stats",
        "schedule": 60 * 5,  # 5 min
    },
    "purge-ip": {
        "task": "apps.analytics.tasks.purge_expired_ips",
        "schedule": 60 * 10,
    },
    "sync-compteurs": {
        "task": "apps.analytics.tasks.flush_scan_counters",
        "schedule": 60,
    },
}

# --------------------------------------------------------------------- projet
# `Any` assumé : c'est un objet de configuration hétérogène (entiers, tables de quotas,
# booléens). Sans annotation, le plugin mypy-Django le déduit `dict[str, object]` et chaque
# `settings.QR[...]` devient une erreur en cascade — ce qui pousse a ajouter des `type: ignore`
# partout, c'est-a-dire a désacturer le controle la ou il sert.
QR: dict[str, Any] = {
    "ENV": ENV,
    "SHORT_BASE_URL": env.get("SHORT_BASE_URL", "http://localhost:8000").rstrip("/"),
    "SLUG_LENGTH": env.get_int("QR_SLUG_LENGTH", 8),
    # Quotas par plan : QR dynamiques créés par période glissante de 30 jours.
    # Les plafonds par palier vivent dans `apps/accounts/plans.py` (une seule source de verite, testee
    # par `tests/test_plans.py`) : les laisser aussi ici donnerait deux endroits a mettre a jour le jour
    # ou la grille bouge — et un `settings.QR["QUOTA_DYNAMIC"]` lu par une vue ne dirait plus rien de la
    # facture reelle.
    "QUOTA_DYNAMIC": {
        "free": env.get_int("QUOTA_FREE_DYNAMIC", 25),
        "pro": env.get_int("QUOTA_PRO_DYNAMIC", 5000),
        "team": env.get_int("QUOTA_TEAM_DYNAMIC", 50000),
    },
    "MAX_PAYLOAD_BYTES": env.get_int("QR_MAX_PAYLOAD_BYTES", 2953),  # version 40 / ecc L (mesuré)
    "DESIGN_MAX_BYTES": env.get_int("QR_DESIGN_MAX_BYTES", 16_384),
    "REDIRECT_CACHE_TTL": env.get_int("QR_REDIRECT_CACHE_TTL", 60),
    "SCAN_RATE_PER_IP": env.get_int("QR_SCAN_RATE_PER_IP", 300),  # /minute, puis 429
    "SCAN_STREAM": env.get("QR_SCAN_STREAM", "stream:scans"),
    "SCAN_STREAM_ENABLED": env.get_bool("QR_SCAN_STREAM_ENABLED", bool(REDIS_URL)),
    "SCAN_STREAM_MAXLEN": env.get_int("QR_SCAN_STREAM_MAXLEN", 500_000),
    # RGPD (§9 de ARCHITECTURE.md) — les valeurs par défaut sont restrictive, pas pratiques.
    "GEO_ENABLED": env.get_bool("QR_GEO_ENABLED", True),
    "GEO_CITY_ENABLED": env.get_bool("QR_GEO_CITY", False),  # opt-in explicite
    "GEO_DB_PATH": env.get("MAXMIND_CITY_DB", ""),
    "IP_RETENTION_HOURS": env.get_int("QR_IP_RETENTION_HOURS", 24),
    "RAW_RETENTION_DAYS": env.get_int("QR_RAW_RETENTION_DAYS", 397),  # 13 mois
    "ALLOW_PRIVATE_TARGETS": env.get_bool("QR_ALLOW_PRIVATE_TARGETS", ENV != "prod"),
    "DNS_CHECK_ON_WRITE": env.get_bool("QR_DNS_CHECK", True),
}

# --------------------------------------------------------------------- i18n / divers
LANGUAGE_CODE = "fr"
LANGUAGES = [("fr", "Français"), ("en", "Anglais"), ("it", "Italien")]
TIME_ZONE = "UTC"  # les ts sont en UTC ; le fuseau d'affichage est un réglage utilisateur
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").is_dir() else []
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # `Manifest` seulement en prod : en dev/test, `collectstatic` n'a pas tourné et leverait un
    # `ValueError` sur la moindre page d'admin (qui référence /static/admin/...).
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
EMAIL_HOST = env.get("EMAIL_HOST", "")

# Selecteurs DKIM attends par `check_email_dns`. Hostinger en publie trois (CNAME vers sa zone) ; un
# changement de relais se declare ici, sinon le controle irait chercher les selecteurs de l'ancien
# hebergeur et conclurait « domaine non signe » pour une configuration parfaitement valide.
EMAIL_DKIM_SELECTEURS = env.get("EMAIL_DKIM_SELECTEURS", "hostingermail-a,hostingermail-b,hostingermail-c").split(",")
# L'adresse qui figure dans le `From:` des e-mails du service (codes de verification,
# reinitialisation). Ce n'est pas un no-reply: les clients repondent a cetite, et un message
# automatise sans boite de retour legible finit en ticket introuvable. L'alias doit exister cote
# messagerie ET etre autorise par le SPF/DKIM du domaine d'envoi (cf. README, check-list).
DEFAULT_FROM_EMAIL = env.get("DEFAULT_FROM_EMAIL", "QR Studio <itsupport@kamcofarm.com>")
# Adresse citee dans les pieds de message. Vide = la boite qui envoie vraiment, c'est-a-dire
# `DEFAULT_FROM_EMAIL` : un pied qui pointe une autre adresse que l'expediteur reel fabrique des rebonds
# et casse l'alignement que les filtres verifient (le domaine d'envoi signe est `kamcofarm.com`).
SUPPORT_EMAIL = env.get("SUPPORT_EMAIL", "")

SERVER_EMAIL = DEFAULT_FROM_EMAIL
EMAIL_SUBJECT_PREFIX = "[QR Studio] "

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "keyvalue": {"()": "apps.common.logging.KeyValueFormatter"},
        "plain": {"format": "{levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "keyvalue" if ENV == "prod" else "plain",
        },
    },
    "root": {"handlers": ["console"], "level": env.get("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "apps": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}


# --- Connexion Google -------------------------------------------------------------------------
# Le flux est ecrit (views + allauth + PKCE) et testable sans credentiels ; il ne devient visible du
# public que lorsque les deux cles existent. Un bouton qui mene a une page `redirect_uri_mismatch`
# fait plus de degat qu'un bouton absent : le client conclut au service casse, pas a un champ non rempli.
# Fermer le registre est une decision d'exploitation (incident, attente de migration, capacite) ;
# `/api/v1/auth/config` l'annonce au front et `RegisterView` la verifie. Un drapeau lu par un seul des
# deux ne sert a rien.
ALLOW_REGISTRATION = env.get_bool("ALLOW_REGISTRATION", True)

GOOGLE_CLIENT_ID = env.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = env.get("GOOGLE_CLIENT_SECRET", "")
# `None` = « on decide d apres les cles ». Un simple booleen ne pourrait pas etre ecrase dans les
# reglages de test (ou l'on pose des faux identifiants pour verifier le flux) ; un tri-state le peut.
GOOGLE_LOGIN_ENABLED = None if "GOOGLE_LOGIN_ENABLED" not in os.environ else env.get_bool("GOOGLE_LOGIN_ENABLED", True)
