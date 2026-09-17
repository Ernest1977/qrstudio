"""Validation des destinations — le seul endroit où une URL fournie par un utilisateur devient confiance.

Un QR dont la destination est une URL du client **transforme notre service en relais** :

* open redirect   : `//evil.com`, `javascript:alert(1)`, `http:/ /x` — le lien court blanchit le vrai
                    lien (le nom `qrstudio.kamcofarm.com` inspire confiance au scanner du téléphone) ;
* SSRF            : la destination est tapée par les scanners de sécurité des *clients* de nos
                    utilisateurs, mais aussi par nos propres sondeurs (lien cassé, aperçu). Une cible
                    `169.254.169.254` (métadonnées cloud) ou `127.0.0.1:5432` ne doit donc jamais
                    être enregistrable, même si l'application, elle, ne fait pas la requête.

D'où, dans l'ordre : schéma, absence de credentials, nom de domaine résolvable, puis refus de toute
adresse privée/boucleuse/link-local/réservée — y compris derrière un nom (résolution au moment de
l'écriture : c'est le choix tracé en §10, il accepte le rébinding au prix d'une revalidation à la
modification, ce qui est le comportement attendu d'un service d'URL courtes).
"""

from __future__ import annotations

import logging
import re
import socket
from urllib.parse import urlsplit

from django.conf import settings

from apps.common.exceptions import ApiError

ALLOWED_SCHEMES = {"http", "https"}
# Un seul caractère d'écart avec l'URL : on n'essaie pas d'être plus malin que le parseur.
MAX_LEN = 2048
logger = logging.getLogger(__name__)

CONTROL_CHARS = re.compile(r"[\x00-\x20\x7f]")


def _allow_private() -> bool:
    return bool((settings.QR or {}).get("ALLOW_PRIVATE_TARGETS", False))


def _dns_check() -> bool:
    return bool((settings.QR or {}).get("DNS_CHECK_ON_WRITE", True))


def validate_target_url(raw: str, *, field: str = "target_url") -> str:
    """Retourne l'URL normalisée, ou lève `ApiError` avec un code machine compris par le front."""
    value = (raw or "").strip()
    if not value:
        raise ApiError("target_required", "Une URL de destination est requise.")
    if len(value) > MAX_LEN:
        raise ApiError("target_too_long", f"URL trop longue ({len(value)} caractères, max {MAX_LEN}).")
    if CONTROL_CHARS.search(value):
        raise ApiError("target_invalid", "L'URL contient des caractères de contrôle.")

    parts = urlsplit(value)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise ApiError(
            "target_scheme",
            "Utilisez une adresse commençant par http:// ou https://.",
            details={"scheme": parts.scheme or "(vide)"},
        )
    if parts.username or parts.password:
        raise ApiError("target_invalid", "Les identifiants dans l'URL ne sont pas autorisés.")
    host = parts.hostname
    if not host:
        raise ApiError("target_invalid", "URL sans nom de domaine.")
    looks_internal = host == "localhost" or host.endswith((".local", ".internal", ".localhost"))
    if looks_internal and not _allow_private():
        raise ApiError("target_private", "Cette destination est une adresse interne.")
    if parts.fragment:
        # Un `Location:` avec fragment ne ferait rien du tout côté scanner : on exige qu'il soit retiré.
        raise ApiError("target_invalid", "L'ancre (#) doit être supprimée de la destination.")

    # Le fragment ci-dessus est toléré par certains scanners mais casse le `Location:` ; on refuse.
    from apps.common.ip import parse_ip

    # Une IP littérale se juge tout de suite : ne pas la vérifier quand la résolution DNS est
    # désactivée laisserait passer `http://169.254.169.254/`, exactement la cible qu'on pourchasse.
    literal = parse_ip(host)
    ips = [str(literal)] if literal is not None else _resolve(host)
    for ip in ips:
        if _is_blocked_ip(ip) and not _allow_private():
            raise ApiError(
                "target_private",
                "Cette destination pointe vers une adresse interne ou réservée.",
                details={"resolved": ip},
            )
    if not parts.path:
        value = f"{parts.scheme}://{parts.netloc}/"
    return value


def _is_blocked_ip(text: str) -> bool:
    from apps.common.ip import is_private_or_reserved

    return is_private_or_reserved(text)


def _resolve(host: str) -> list[str]:
    """Adresse(s) du nom. En dessous de `DNS_CHECK_ON_WRITE=False` (tests), on ne résout pas."""
    if not _dns_check():
        return []
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ApiError("target_unresolved", f"Le nom {host} ne répond pas : {exc.strerror or 'DNS'}") from exc
    except Exception as exc:  # noqa: BLE001 - un timeout DNS ne doit pas bloquer un enregistrement
        logger.warning("dns en échec pour %s: %s", host, exc)
        return []
    return sorted({str(info[4][0]) for info in infos})
