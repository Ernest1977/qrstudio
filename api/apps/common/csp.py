"""Politique de contenu (CSP) des pages servies par Django, avec un nonce par requête.

Trois décisions, **mesurées** avant d'être écrites :

* Aucun script en ligne exécutable n'existe dans nos gabarits, dans ceux d'`django.contrib.admin`
  (50 gabarits installés, zéro `<script>` sans `src`) ni dans ceux de `django-allauth` — lequel pose des
  `<script type="application/json" data-allauth-onload=…>`, des îlots de données inertes que `script-src`
  ne regarde pas : ils ne sont ni compilés ni exécutés. `script-src` peut donc être strict sans casser
  l'authentification à deux facteurs, qui est pourtant la porte d'entrée de l'admin. Le test
  `test_aucun_script_executable_en_ligne_sur_les_pages_fournisseurs` garde la main dessus : si une mise à
  jour d'allauth ou de Django introduit un vrai script en ligne, il rougit au lieu que l'admin tombe.
* Les styles en ligne, eux, existent (`manage/_style.html`, `qr/promo.html`, `qr/unavailable.html`) : ils
  portent le nonce. Les attributs `style="…"` — trois notes de mise en page et les graphiques SVG construits
  serveur — restent permis par `style-src-attr 'unsafe-inline'` : une feuille de style ne fait pas tourner
  de code, elle peut au mieux fuir une valeur par sélecteur.
* Le bloc `<style>` de `admin/change_list.html` est chez Django et ne peut pas être noncé. Sa règle unique
  (`#changelist table thead th:first-child { width: inherit }`) est décorative et n'est émise que quand les
  actions de liste sont désactivées : c'est le prix assumé d'un `style-src` strict, écrit ici pour qu'on le
  retrouve plutôt qu'on le découvre.

Le mode par défaut est `report-only` hors debug : la politique est publiée, les violations sont journalisées
sur `/csp-violation/`, et **rien n'est bloqué** tant que l'exploitant n'a pas posé `CSP_MODE=apply`.
"""

from __future__ import annotations

import logging
import secrets
from typing import TYPE_CHECKING

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

if TYPE_CHECKING:  # pragma: no cover
    from django.http import HttpRequest, HttpResponseBase

logger = logging.getLogger(__name__)

TAILLE_NONCE = 16  # -> 22 caracteres en base64 URL-safe
MODES = ("off", "report-only", "apply")
ENTETE_APPLIQUEE = "Content-Security-Policy"
ENTETE_RAPPORT = "Content-Security-Policy-Report-Only"
CHEMIN_RAPPORT = "/csp-violation/"
GROUPE_RAPPORT = "qrcsp"


class Nonce:
    """Un nonce **paresseux** : le chemin chaud `GET /r/{slug}` ne rend pas de HTML et ne doit rien payer.

    L'objet est mis sur `request` par le middleware et lu par le gabarit ; `str()` engendre la valeur à la
    première utilisation et la fige, pour que deux balises d'une même page partagent le même nonce.
    """

    __slots__ = ("_valeur",)

    def __init__(self) -> None:
        self._valeur: str | None = None

    @property
    def valeur(self) -> str:
        if self._valeur is None:
            self._valeur = secrets.token_urlsafe(TAILLE_NONCE)
        return self._valeur

    def __str__(self) -> str:
        return self.valeur

    def __repr__(self) -> str:  # jamais la valeur dans un log ou un traceback
        return "<Nonce>"


class ConfigurationCsp(Exception):
    """`CSP_MODE` contient une valeur inconnue : on refuse de démarrer, on n'ouvre pas en silence."""


STATUTS_SANS_CORPS = frozenset({204, 205, 304})
STATUTS_DE_REDIRECTION = frozenset({301, 302, 303, 307, 308})


def est_html(reponse: HttpResponseBase) -> bool:
    """Seules les pages rendues méritent la politique.

    Deux rejets, et le second n'est pas un détail de style : `HttpResponseRedirect` — donc **tout** le chemin
    chaud `GET /r/{slug}` — sort avec un `Content-Type: text/html` par défaut de Django et un corps vide. Lui
    publier une politique coûterait un `secrets.token_urlsafe` et ~400 octets d'en-tête par scan, pour
    protéger un document que le navigateur ne rend pas. Un JSON d'API et un PNG sont écartés de la même façon.
    """
    if reponse.status_code in STATUTS_SANS_CORPS:
        return False
    if reponse.status_code in STATUTS_DE_REDIRECTION and reponse.headers.get("Location"):
        return False
    type_contenu = (reponse.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    return type_contenu == "text/html"


def directives(
    nonce: str,
    *,
    script_extra: str = "",
    montee_insegure: bool = False,
    signaler: bool = False,
) -> list[str]:
    """La politique, en listes de directives pour qu'un test puisse lire chaque ligne séparément."""
    script = f"script-src 'self' 'nonce-{nonce}'"
    if script_extra:
        script = f"{script} {script_extra.strip()}"
    lignes = [
        "default-src 'self'",
        script,
        f"style-src 'self' 'nonce-{nonce}'",
        "style-src-attr 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "object-src 'none'",
    ]
    if montee_insegure:
        lignes.append("upgrade-insecure-requests")
    if signaler:
        lignes.append(f'report-to "{GROUPE_RAPPORT}"')
        lignes.append(f'report-uri "{CHEMIN_RAPPORT}"')
    return lignes


def politique(
    nonce: str,
    *,
    mode: str,
    script_extra: str = "",
    montee_insegure: bool = False,
    signaler: bool = False,
) -> tuple[str, str]:
    """`(nom_de_l_en-tete, valeur)` pour le mode demandé. `mode="off"` n'est pas appelé : le middleware trie."""
    entete = ENTETE_APPLIQUEE if mode == "apply" else ENTETE_RAPPORT
    return entete, "; ".join(
        directives(nonce, script_extra=script_extra, montee_insegure=montee_insegure, signaler=signaler)
    )


def resoudre_mode(valeur: str | None, *, debug: bool) -> str:
    """`CSP_MODE` vide = choisir selon `DEBUG`. Une valeur inconnue est une erreur de démarrage."""
    mode = (valeur or ("off" if debug else "report-only")).strip().lower()
    if mode not in MODES:
        raise ConfigurationCsp(f"CSP_MODE={mode!r} : valeurs admises {', '.join(MODES)}.")
    return mode


# ------------------------------------------------------------------------- bout de gabarit
def valeur_du_nonce(requete):
    """Le nonce de la requete, ou None hors requete. La balise `{% csp_nonce %}` s'en contente.

    Le `None` est volontaire : les gabarits d'e-mail et les rendus hors requete ne doivent pas emettre un
    `nonce=""` qui serait une politique cassee plutot qu'une politique absente.
    """
    nonce = getattr(requete, "csp_nonce", None) if requete is not None else None
    return None if nonce is None else str(nonce) or None


# ------------------------------------------------------------------------- point de chute des rapports
def analyser_rapport(corps: dict) -> dict:
    """Ramène les deux formes de rapport au même dictionnaire.

    `report-uri` poste `{"csp-report": {...}}` ; `report-to` poste une enveloppe de l'API Reporting
    `{"type": "csp-violation", "body": {...}}`, où le bloc s'appelle `blocked-url` et non `blocked-uri`.
    Les deux se lisent ici, sinon on ne voit jamais les violations du groupe moderne.
    """
    rapport = corps.get("csp-report") or corps.get("body") or {}
    if not rapport or not isinstance(rapport, dict):
        return {}
    return {
        "directive": rapport.get("violated-directive") or rapport.get("effective-directive") or "?",
        "bloque": rapport.get("blocked-uri") or rapport.get("blocked-url") or "?",
        "document": rapport.get("document-uri") or "?",
        "ligne": rapport.get("line-number"),
    }


@csrf_exempt
def signaler_violation(request: HttpRequest) -> HttpResponse:
    """Là où les navigateurs déclarent une violation. Il journalise, il ne juge pas, il ne lève jamais.

    Un 204 et rien d'autre : une réponse explicite dirait à un serveur malveillant quelles politiques sont
    en place. Le corps est tronqué à 8 ko et le même émetteur ne peut marquer le journal qu'une fois par
    minute — sans ce garde-fou, un site vérolé inonderait `journalctl` de rapports.
    """
    import json

    from django.core.cache import cache

    if request.method != "POST":
        return HttpResponse(status=405)
    try:
        corps = json.loads(request.body[:8192] or b"{}")
        details = analyser_rapport(corps if isinstance(corps, dict) else {})
    except Exception:  # noqa: BLE001 - un rapport malforme n'est pas une erreur de l'application
        details = {}
    if details:
        cle = f"qrs:csp:journal:{request.META.get('REMOTE_ADDR', '?')}"
        if cache.add(cle, 1, 60):
            logger.warning(
                "CSP: %s bloque %s (document %s%s)",
                details["directive"],
                details["bloque"],
                details["document"],
                f", ligne {details['ligne']}" if details.get("ligne") else "",
            )
    return HttpResponse(status=204)
