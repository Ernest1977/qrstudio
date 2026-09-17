"""IP du client et troncature RGPD.

Un VPS Hostinger derrière Caddy reçoit `X-Forwarded-For` : on ne lui fait confiance **que** si on
l'a explicitement autorisé (`TRUST_PROXY_HEADERS`), sinon un attaquant forge son IP et contourne la
limitation de débit — ou empoisonne les statistiques par pays.
"""

from __future__ import annotations

import ipaddress
from typing import Any

TRUSTED_LOOPBACK = {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")}


def parse_ip(raw: str | None) -> Any | None:
    if not raw:
        return None
    try:
        return ipaddress.ip_address(raw.strip())
    except ValueError:
        return None


def client_ip(request, *, trust_proxy: bool = True) -> str | None:
    """`X-Forwarded-For` (premier hop) si l'on fait confiance au proxy, sinon `REMOTE_ADDR`."""
    if trust_proxy:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            candidate = parse_ip(forwarded.split(",")[0])
            if candidate is not None:
                return str(candidate)
    return request.META.get("REMOTE_ADDR") or None


def truncate_ip(ip: str | None) -> tuple[str | None, int | None]:
    """Pseudonymisation : IPv4 → /24, IPv6 → /48.

    On stocke le réseau, pas l'adresse : suffisant pour dédupliquer un visiteur sur une journée,
    insuffisant pour ré-identifier une personne. Cf. ARCHITECTURE.md §9.
    """
    addr = parse_ip(ip)
    if addr is None:
        return None, None
    if addr.version == 4:
        network = ipaddress.ip_network(f"{addr}/24", strict=False)
    else:
        network = ipaddress.ip_network(f"{addr}/48", strict=False)
    return str(network.network_address), network.prefixlen


def is_private_or_reserved(ip: str) -> bool:
    """Vrai si l'adresse est boucle/privée/link-local/réservée/multicast — à refuser comme cible."""
    addr = parse_ip(ip)
    if addr is None:
        return True  # une adresse illisible est refusée, pas acceptée "au bénéfice du doute"
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )
