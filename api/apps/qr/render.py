"""Rendu serveur du QR (PNG / SVG).

Pourquoi côté serveur alors que le navigateur sait déjà dessiner : deux cas le justifient. (1) Un QR
**imprimé** doit être re-téléchargeable des mois plus tard, sans dépendre de l'état du studio en
local ; (2) le PNG généré par `qrcode` avec logo (ecc relevée en `H`) n'est pas reproductible à
l'identique par le front — or un flyer engage une dimension. La source de vérité du dessin est donc
serveur, et le front garde son aperçu instantané.

Propriété exploitable : pour un QR **dynamique**, l'image encode `SHORT_BASE_URL/r/{slug}`, et le slug
ne change jamais. L'image d'un QR dynamique est donc *stable à vie* même quand la destination bouge —
c'est exactement ce qui rend un flyer re-imprimable inutile. Les clés de cache en tiennent compte.
"""

from __future__ import annotations

import hashlib
import logging

from apps.common.exceptions import ApiError

logger = logging.getLogger(__name__)

FORMATS = {"png": "image/png", "svg": "image/svg+xml", "pdf": "application/pdf"}
ECC = {"L": 1, "M": 0, "Q": 3, "H": 2}  # correspondances qrcode.constants
TAILLE_MIN, TAILLE_MAX = 64, 2048
MARGE_MIN, MARGE_MAX = 0, 16


def _borne(valeur, *, mini: int, maxi: int, nom: str) -> int:
    try:
        n = int(valeur)
    except (TypeError, ValueError):
        raise ApiError("invalid_size", f"`{nom}` doit être un entier.") from None
    if not mini <= n <= maxi:
        raise ApiError("invalid_size", f"`{nom}` doit être entre {mini} et {maxi}.")
    return n


def cache_key(*, contenu: str, fmt: str, size: int, ecc: str, margin: int, dark: str, light: str) -> str:
    """Clé dérivée du **contenu encodé** et du dessin — pas de l'`id` ni du `updated_at`.

    Un QR dynamique dont on change la destination garde la même image : inclure l'identifiant ou un
    horodatage ferait croire qu'il faut regénérer, et surtout ferait rater le cas où l'image est
    exactement la même. Hacher le contenu rend la clé correcte dans les deux sens.
    """
    empreinte = hashlib.sha256(f"{contenu}|{fmt}|{size}|{ecc}|{margin}|{dark}|{light}".encode()).hexdigest()[:32]
    return f"qrs:qrimg:{empreinte}"


def code_source(contenu: str, *, ecc: str = "M", margin: int = 2):
    """Le `qrcode.QRCode` déjà `make()`-é — la grille partagée par le rendu classique, l'art et l'animation.

    Une seule fabrique de matrice dans le projet : si l'art construisait son propre QRCode (version, ECC,
    marge choisis ailleurs), on pourrait livrer une image dont la grille ne correspond plus à celle que
    l'API a validée, et le décodage de preuve ne prouverait plus le même objet.
    """
    import qrcode
    from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_L, ERROR_CORRECT_M, ERROR_CORRECT_Q

    if ecc not in ECC:
        raise ApiError("invalid_ecc", "Correction d'erreur non supportée : L, M, Q ou H.")
    constantes = {"L": ERROR_CORRECT_L, "M": ERROR_CORRECT_M, "Q": ERROR_CORRECT_Q, "H": ERROR_CORRECT_H}
    code = qrcode.QRCode(error_correction=constantes[ecc], box_size=8, border=margin)
    code.add_data(contenu)
    # `fit=True` choisit la version (taille) minimale. La borne de capacité est vérifiée à l'écriture
    # (`QrSerializer`), pas ici : un contenu accepté ne doit jamais devenir incapable d'être dessiné.
    code.make(fit=True)
    return code


def rendu(qr, *, fmt: str = "png", size: int = 512, design: dict | None = None) -> tuple[bytes, str, str]:
    """Retourne `(octets, type_mime, nom_de_fichier)`.

    Le contenu encodé est celui que l'utilisateur verrait scanné : l'URL courte pour un QR dynamique,
    le texte brut pour un QR statique. On ne le redérive pas depuis le front : ce serait un second
    moteur de rendu à garder synchronisé.
    """
    design = design or {}
    fmt = (fmt or "png").lower()
    if fmt not in FORMATS:
        raise ApiError(
            "invalid_format", "Format non supporté : `png` ou `svg`.", details={"supportes": sorted(FORMATS)}
        )
    size = _borne(size, mini=TAILLE_MIN, maxi=TAILLE_MAX, nom="size")
    ecc = str(design.get("ecc") or "M").upper()
    margin = _borne(design.get("margin", 2), mini=MARGE_MIN, maxi=MARGE_MAX, nom="margin")
    dark = _couleur(design.get("dark"), "#000000")
    light = _couleur(design.get("light"), "#ffffff")

    # Aiguillage par `kind`, pas par « la destination est-elle remplie ? » : un QR statique peut porter
    # une note de destination, et un dynamique en pause a encore son URL courte — les deux liraient
    # le mauvais champ avec une expression courte du style `a and b or c`.
    from apps.qr.models import Kind  # (Kind est module-level, pas imbrique dans QrCode)

    contenu = qr.short_url if qr.kind == Kind.DYNAMIC else qr.payload
    if not contenu:
        raise ApiError("empty_payload", "Ce QR n'a rien à encoder.")

    code = code_source(contenu, ecc=ecc, margin=margin)
    modules = code.modules_count + 2 * margin
    code.box_size = max(1, size // modules)  # visée: taille demandée, arrondie au module entier

    if fmt == "svg":
        from qrcode.image.svg import SvgPathImage

        image = code.make_image(image_factory=SvgPathImage, fill_color=dark, back_color=light)
    else:
        image = code.make_image(fill_color=dark, back_color=light)
    if fmt == "pdf":
        # Pillow ecrit un PDF a partir d'une image bitmap ; la resolution demandee devient la
        # resolution d'impression. C'est le seul « export PDF » facture en standard : il ne necessite
        # aucune library de mise en page, donc aucun binaire ni service externe.
        image = image.convert("RGB") if hasattr(image, "convert") else image

    import io

    tampon = io.BytesIO()
    if fmt == "pdf":
        image.save(tampon, format="PDF", resolution=min(600.0, size / 21.0 * 25.4))
    else:
        image.save(tampon)
    octets = tampon.getvalue()
    nom = f"qr-{qr.slug or qr.pk}-{size}.{fmt}"
    logger.info("qr rendu id=%s fmt=%s taille=%s octets=%s", qr.pk, fmt, size, len(octets))
    return octets, FORMATS[fmt], nom


def _couleur(valeur, defaut: str) -> str:
    """`#rgb` / `#rrggbb` uniquement, sans le passer tel quel à Pillow.

    Une couleur non validée part dans le constructeur d'image et, selon la bibliothèque, finit soit en
    exception 500 chez l'utilisateur, soit en valeur interprétée côté rendeur. Le coût du contrôle est
    nul, celui de l'absence ne l'est pas.
    """
    v = str(valeur or defaut).strip()
    if len(v) == 4 and v[0] == "#":
        v = "#" + "".join(c * 2 for c in v[1:])
    if len(v) != 7 or v[0] != "#" or any(c not in "0123456789abcdefABCDEF" for c in v[1:]):
        raise ApiError("invalid_color", "Couleur attendue en `#rgb` ou `#rrggbb`.")
    return v.lower()
