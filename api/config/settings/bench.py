"""Réglages de mesure : le chemin de production, sans ce qui fausse le chronomètre.

`DEBUG=True` fait consigner chaque requête SQL par la connexion et rend le rendu de templates plus
verbeux : un banc lancé là-dessus affiche 40 ms de plancher et conclurait à un service lent. Ce
module ne sert qu'au banc (`make bench`, la cible `load` de la CI) et ne doit jamais recevoir de
trafic public — il désactive les garde-fous HTTPS et accepte n'importe quel `Host`.
"""

from __future__ import annotations

from .base import *  # noqa: F403
from .dev import *  # noqa: F403  : SQLite + LocMemCache, les services externes en moins

DEBUG = False
ALLOWED_HOSTS = ["*"]
SECURE_SSL_REDIRECT = False
SESSION_ENGINE = "django.contrib.sessions.backends.db"

# Tourner une ligne de log par requête coûte plus cher que la requête mesurée ;
# `runserver` écrit son accès sur stderr, ce qui domine la mesure sur un poste à 2 cœurs.
LOGGING.setdefault("loggers", {})["django.server"] = {
    "handlers": ["console"],
    "level": "CRITICAL",
    "propagate": False,
}
LOGGING["root"]["level"] = "ERROR"

# Le stream de scans n'existe pas dans ce contexte : le compteur de la vue doit rester nul, sinon on
# mesure aussi le temps pendant lequel la vue attend un Redis qui n'est pas là.
QR = {**QR, "SCAN_STREAM_ENABLED": False}
