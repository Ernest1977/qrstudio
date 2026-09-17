"""Stripe, sans SDK : trois appels REST et une signature vérifiée à la main.

Pourquoi pas la bibliothèque officielle : elle pèse (un pin de plus, une surface d'update), et ce dont
le service a besoin tient en trois routes. Le seul morceau *délicat* est la vérification de signature
du webhook, et la réimplémenter est précisément ce qui permet de la tester ici (tolérance d'horodatage,
multi-signatures, comparaison en temps constant) — avec le SDK, on aurait testé le SDK.

Contrepartie assumée : si l'on fait un jour du remboursement, du proration ou de la facture PDF côté
serveur, il vaudra mieux passer par le SDK plutôt que d'empiler des appels à la main.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import time
from typing import Any

API_BASE = "https://api.stripe.com"
TOLERANCE_DEFAUT = 300  # secondes — la tolérance recommandée par Stripe


class ErreurStripe(Exception):
    """Erreur remontée par l'API Stripe (ou par son absence de configuration)."""

    def __init__(self, message: str, *, code: str = "stripe_error", statut: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.statut = statut


def _client():
    import requests

    return requests


def appeler(methode: str, chemin: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Un seul point de sortie HTTP — ce qui rend le reste testable sans réseau.

    Les paramètres sont passés en *form encoding* à la manière de Stripe (`a[b]=1`, `a[]=2`) : le JSON
    n'est pas accepté sur les routes d'écriture de cette API, et c'est le piège classique de la
    première intégration.
    """
    from django.conf import settings

    cle = settings.STRIPE_SECRET_KEY
    if not cle:
        raise ErreurStripe(
            "Stripe n'est pas configuré sur cette instance (STRIPE_SECRET_KEY absent).",
            code="stripe_non_configure",
            statut=503,
        )
    donnees = {k: v for k, v in (params or {}).items() if v is not None}
    reponse = _client().request(
        methode,
        f"{API_BASE}{chemin}",
        data=_aplatir(donnees),
        auth=(cle, ""),
        timeout=15,
        headers={"Accept": "application/json", "User-Agent": "qrstudio-api/1.0"},
    )
    if reponse.status_code >= 400:
        corps = {}
        # Une erreur 5xx de passerelle renvoie du HTML, pas du JSON : le corps est inexploitable, on
        # remonte le code HTTP nu plutot que de masquer la panne derivee par une exception de parsing.
        with contextlib.suppress(ValueError):
            corps = reponse.json()
        message = (corps.get("error") or {}).get("message") or f"Stripe a repondu {reponse.status_code}"
        raise ErreurStripe(message, code=(corps.get("error") or {}).get("code") or "stripe_http", statut=502)
    try:
        return reponse.json()
    except ValueError as exc:
        raise ErreurStripe("Réponse de Stripe illisible.", code="stripe_bad_json") from exc


def _aplatir(dictionnaire: dict[str, Any], prefixe: str = "") -> dict[str, str]:
    sortie: dict[str, str] = {}
    for cle, valeur in dictionnaire.items():
        nom = f"{prefixe}[{cle}]" if prefixe else cle
        if isinstance(valeur, dict):
            sortie.update(_aplatir(valeur, nom))
        elif isinstance(valeur, (list, tuple)):
            for index, element in enumerate(valeur):
                if isinstance(element, dict):
                    sortie.update(_aplatir(element, f"{nom}[{index}]"))
                else:
                    sortie[f"{nom}[{index}]"] = str(element)
        elif valeur is not None:
            sortie[nom] = str(valeur) if not isinstance(valeur, bool) else ("true" if valeur else "false")
    return sortie


def session_checkout(**parametres) -> dict[str, Any]:
    return appeler("POST", "/v1/checkout/sessions", parametres)


def session_portail(**parametres) -> dict[str, Any]:
    return appeler("POST", "/v1/billing_portal/sessions", parametres)


def prix_actif(prix_id: str) -> dict[str, Any]:
    return appeler("GET", f"/v1/prices/{prix_id}")


def value_is_digit(valeur: str) -> bool:
    return valeur.isdigit()


def verifier_signature(
    corps: bytes, entete: str | None, secret: str, *, tolerance: int = TOLERANCE_DEFAUT, maintenant: float | None = None
) -> tuple[bool, str]:
    """`(vrai, raison)` — jamais une exception : l'appelant doit pouvoir repondre 400 avec le motif.

    Trois garde-fous, et chacun a deja ete la cause reelle d'un incident public :

    * comparer en **temps constant** (`hmac.compare_digest`) : un `==` sur une signature rend une
      attaque par minutage theoriquement possible, et le cout du garde-fou est nul ;
    * verifier l'**horodatage** contre la tolerance : sans ca, une capture rejeuee des mois plus tard
      reste une commande valide a vie ;
    * accepter **plusieurs signatures** separées par des espaces : c'est ce que Stripe emet apres une
      rotation de secret, et un parseur qui ne lit que la premiere la rend inoperante.
    """
    if not secret:
        return False, "secret_de_webhook_absent"
    if not entete:
        return False, "entete_absent"

    horodatage = None
    candidats: list[bytes] = []
    # Le format reel est `t=<epoch>,v1=<hex>[,v1=<hex>]` : une horodatage, N signatures (N = 2 apres
    # une rotation de secret). On parse champ par champ au lieu de splitter sur `=` : une signature ne
    # contient jamais de `=`, mais un parseur naive casse des qu'un element surprise apparait.
    for morceau in entete.split(","):
        cle, _, valeur = morceau.strip().partition("=")
        cle = cle.strip()
        valeur = valeur.strip()
        if cle == "t" and value_is_digit(valeur):
            horodatage = int(valeur)
        elif cle == "v1" and valeur:
            candidats.append(valeur.encode())
    if horodatage is None or not candidats:
        return False, "entete_malforme"

    instant = maintenant if maintenant is not None else time.time()
    if abs(instant - horodatage) > tolerance:
        return False, "horodatage_hors_tolerance"

    calcule = (
        hmac.new(secret.encode(), f"{horodatage}.{corps.decode('utf-8')}".encode(), hashlib.sha256).hexdigest().encode()
    )
    if not any(hmac.compare_digest(calcule, c) for c in candidats):
        return False, "signature_invalide"
    return True, ""


def charger_evenement(corps: bytes) -> dict[str, Any]:
    try:
        return json.loads(corps.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ErreurStripe("Charge du webhook non parsable.", code="webhook_invalide", statut=400) from exc
