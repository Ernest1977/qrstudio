"""Identifiants courts pour les URLs de redirect.

Un slug de 8 caractères en base62 = 62**8 ≈ 2,18e14 valeurs : on ne peut pas les parcourir
(anti-énumération), ils tiennent dans un QR de version basse, et ils sont lisibles à la dictée.
`secrets` et non `random` : un PRNG non semé correctement exposerait toute la collection.
"""

from __future__ import annotations

import secrets

# Algorithme de base62 sans 0/O/1/I/l : un QR recopié à la main depuis une capture d'écran ne doit
# pas pouvoir confondre un zero et une lettre. La perte d'entropie (59 au lieu de 62) est négligeable.
ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ALPHABET_SIZE = len(ALPHABET)


def new_token(length: int = 8) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def entropy_bits(length: int) -> float:
    import math

    return round(length * math.log2(ALPHABET_SIZE), 1)


def slug_is_safe(slug: str) -> bool:
    """Le slug vient de l'URL : on le valide avant de toucher la base."""
    return 4 <= len(slug) <= 32 and all(char in ALPHABET for char in slug)
