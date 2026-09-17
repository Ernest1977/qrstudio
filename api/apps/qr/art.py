"""QR « artistiques » : un dessin qui reste un QR, avec la preuve qu'il se décode.

Pourquoi ce module est séparé de `render.py` : le rendu classique dessine des modules carrés en deux
couleurs et ne peut pas se tromper. Dès qu'on arrondit des modules, qu'on perce un logo au centre et
qu'on remplace le noir par une couleur de marque, on mange la redondance du code — et une image moche mais
lisible se vend mieux qu'une belle image qui ne scanne plus chez un tiers des clients. La seule défense
honnête est de **re-lire l'image produite** avant de la servir : ici `cv2.QRCodeDetector` décode le PNG,
et si le contenu n'en ressort pas à l'octet près, la génération échoue au lieu d'être livrée.

Coûts assumés : l'art est réservé au palier qui le paie (`qr_artistique_ia`), il est calculé à la demande
et non à l'écriture du QR, et `opencv-python-headless` devient nécessaire sur l'hôte qui le sert — sans
lui, on refuse le format (503) au lieu de servir une image « probablement bonne ».
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from apps.common.exceptions import ApiError

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    # Pillow n'est importe qu'a l'usage dans ce module (le chargement de `PIL` coute ~40 ms, et l'API de
    # redirection ne dessine jamais) : le typeur, lui, doit voir les types pour annoter les helpers.
    from PIL import Image

#: Palettes et formes. Un style n'entre ici que s'il passe la porte du test : image décodée à 256 px et
#: à 1024 px, contenu exact retrouvé, contraste >= 4.5:1.
STYLES: dict[str, dict] = {
    "naples": {"nom": "Naples", "sombre": "#123a2e", "claire": "#f6efe2", "module": "arrondi"},
    "halles": {"nom": "Halles", "sombre": "#1f2937", "claire": "#eaf2f8", "module": "cercle"},
    "nuit": {"nom": "Nuit d'encre", "sombre": "#0b1020", "claire": "#e8ecf8", "module": "losange"},
    "vitrine": {"nom": "Vitrine", "sombre": "#7a1f2b", "claire": "#fdf6f3", "module": "carre"},
    # Le plus prudent, et le dernier repli quand un style décoratif ne se relit pas :
    "encre": {"nom": "Encre (repli)", "sombre": "#000000", "claire": "#ffffff", "module": "carre"},
}
REPLIS = ("encre",)

#: Couverture max du logo, **mesurée** (OpenCV 4.11, contenu de 900 octets en ECC H) : 0.14 échoue,
#: 0.10 passe. La regle usuelle des « 20 % » vaut pour les scanners de telephone, pas pour un detecteur
#: de reference : ici on prend la valeur qui passe chez le plus strict, et on reduit progressivement.
LOGO_SURFACE_MAX = 0.10
LOGO_REDUGRADATIONS = (0.10, 0.07, 0.05)  # puis sans logo, dans cet ordre
MODULE_MIN_PX = 6  # en dessous, un telephone moyen ne distingue plus les modules
ZONE_SILENCE = 2  # modules de marge claire : la RFC l'exige, les scanners encore plus
CONTRASTE_MIN = 4.5  # seuil WCAG AA, parce qu'un flyer sous un auvent n'est pas un ecran Retina


@dataclass(frozen=True)
class Score:
    """Ce qui rend l'image scannable, en nombres. Le front l'affiche, le test l'assermente."""

    style: str
    modules: int
    module_px: int
    taille_px: int
    zone_silence_modules: int
    taux_contraste: float
    couverture_logo: float
    ecc: str
    decode: bool
    contenu_attendu: bool
    contenu_recupere: str
    epreuve: str = ""
    repli: bool = False
    logo_reduit: bool = False
    logo_retire: bool = False

    @property
    def lisible(self) -> bool:
        return self.decode and self.contenu_attendu

    def pour_api(self) -> dict:
        return {**asdict(self), "lisible": self.lisible}


def _luminance(couleur: str) -> float:
    v = couleur.lstrip("#")
    r, g, b = (int(v[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def lineaire(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lineaire(c) for c in (r, g, b))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contraste(sombre: str, claire: str) -> float:
    a, b = sorted((_luminance(sombre), _luminance(claire)))
    return round((b + 0.05) / (a + 0.05), 2)


def styles_disponibles() -> list[dict]:
    """Ce que le front propose dans son selecteur, avec le contraste deja calcule (source unique)."""
    return [
        {
            "code": code,
            "nom": reglages["nom"],
            "module": reglages["module"],
            "sombre": reglages["sombre"],
            "claire": reglages["claire"],
            "contraste": contraste(reglages["sombre"], reglages["claire"]),
        }
        for code, reglages in STYLES.items()
    ]


def _detecteurs() -> list[tuple[str, Callable[[Any], list[str]]]]:
    """Les décodeurs disponibles, du plus fiable au moins fiable : `[(nom, lire), …]`.

    L'ordre est une mesure, pas une preférence : sur nos propres images, OpenCV refuse un cadre entoure
    d'un liseré clair (toutes tailles essayees, 6 a 48 px) la ou zxing-cpp le lit, et refuse aussi le PNG
    de reference de la lib `qrcode` a 410 px. Un verifieur plus strict que le produceur ne rend pas le
    produit plus sur : il le rend indisponible. zxing (le decodeur de reference, celui dont les regles de
    seuillage sont celles du terrain) passe donc en premier, OpenCV reste le repli des hotes qui n'ont pas
    la premiere dependance.
    """

    trouves: list[tuple[str, Callable[[Any], list[str]]]] = []
    try:
        import numpy as np
        import zxingcpp

        def _lire_zxing(image: Image.Image) -> list[str]:
            resultats = zxingcpp.read_barcodes(np.array(image.convert("L")))
            return [str(r.text) for r in resultats if getattr(r, "text", "")]

        trouves.append(("zxing", _lire_zxing))
    except ImportError:
        pass
    try:
        import cv2
        import numpy as np

        def _lire_opencv(image: Image.Image) -> list[str]:
            grille = np.array(image.convert("L"))
            lecture = cv2.QRCodeDetector()
            # Signature mesuree (OpenCV 4.11) : `detectAndDecode` renvoie **la chaine en premier**, pas un
            # booleen — la lire comme un booleen ferait passer un code lisible pour une absence.
            lu, _points, _redresse = lecture.detectAndDecode(grille)
            if lu:
                return [str(lu)]
            trouve, textes, _pts, _rect = lecture.detectAndDecodeMulti(grille)
            return [str(t) for t in (textes or []) if t] if trouve else []

        trouves.append(("opencv", _lire_opencv))
    except ImportError:  # pragma: no cover - instance sans decodeur
        pass
    return trouves


def relire(octets: bytes, *, contenu: str) -> tuple[bool, bool, int, str, str]:
    """Relit l'image produite → `(décodé, contenu_exact, largeur_px, texte_lu, épreuve)`.

    Chaque décodeur disponible est tente sur l'image telle quelle, puis sur la meme image entouree de
    4 px de clair : cette deuxieme epreuve n'est pas une faveur, c'est ce que fait un scanner (il
    recadre, il seuille) — mais elle est nommee dans le score, pour que « verifie » ne veuille jamais
    dire « verifie en trichant ».

    Si aucun décodeur n'est installé sur l'hôte, on **refuse** le format (503) au lieu de servir une
    image non prouvée : la garantie est la seule chose que ce palier vend.
    """
    from PIL import Image, ImageOps

    detecteurs = _detecteurs()
    if not detecteurs:
        raise ApiError(
            "art_verifieur_absent",
            "Ce serveur ne peut pas prouver la lisibilité d'un QR artistique : ni `zxing-cpp` ni "
            "`opencv-python-headless` n'est installé. Le format est refusé plutôt que servi sans vérification.",
            status_code=503,
        )
    with Image.open(io.BytesIO(octets)) as image:
        base = image.convert("L")
        largeur = int(base.size[0])
        bord = ImageOps.expand(base, border=4, fill=255)
        for presentation, image_test in (("direct", base), ("marge", bord)):
            for nom, lire in detecteurs:
                textes = lire(image_test)
                if textes:
                    lu = max(textes, key=len)
                    return True, lu == str(contenu), largeur, lu, f"{nom}-{presentation}"
    return False, False, largeur, "", ""


def _dessiner(
    code, *, style: dict, taille: int, logo: bytes | None, couverture_max: float = LOGO_SURFACE_MAX
) -> tuple[Image.Image, int, float]:
    """Dessine `(image, taille_de_module, couverture_du_logo)`.

    Les modules sont poses a la main (Pillow) et non via `qrcode.make_image` : c'est la seule facon
    d'arrondir, de percer un logo et de garder une zone de silence exacte. Le cout est un second chemin
    de dessin — la grille de modules, elle, vient du meme `code_source` que le rendu classique.
    """
    from PIL import Image, ImageDraw

    modules = code.modules_count
    marge = code.border
    # `get_matrix()` inclut DEJA la marge (matrice = modules + 2*border) : recaler les indices de
    # `marge` une seconde fois dessinerait un code decale et rogne, indefiniment refuse par la relecture.
    grille = code.get_matrix()
    cote = len(grille)
    module = max(MODULE_MIN_PX, taille // cote)
    cote_image = module * cote

    image = Image.new("RGB", (cote_image, cote_image), style["claire"])
    trait = ImageDraw.Draw(image)
    sombre = style["sombre"]

    for y in range(cote):
        ligne = grille[y]
        for x in range(cote):
            if not ligne[x]:
                continue
            x0, y0 = x * module, y * module
            x1, y1 = x0 + module - 1, y0 + module - 1
            detecteur = _dans_detecteur(x - marge, y - marge, modules)
            forme = style["module"] if not detecteur else "carre"
            if forme == "cercle":
                demi = module / 2
                cx, cy = x0 + demi, y0 + demi
                trait.ellipse([cx - demi, cy - demi, cx + demi, cy + demi], fill=sombre)
            elif forme == "losange":
                demi = module / 2
                cx, cy = x0 + demi, y0 + demi
                trait.polygon([(cx, y0), (x1, cy), (cx, y1), (x0, cy)], fill=sombre)
            elif forme == "arrondi":
                trait.rounded_rectangle([x0, y0, x1, y1], radius=max(1, int(module * 0.3)), fill=sombre)
            else:
                trait.rectangle([x0, y0, x1, y1], fill=sombre)

    couverture = 0.0
    if logo:
        try:
            marque = Image.open(io.BytesIO(logo)).convert("RGBA")
        except Exception as exc:
            raise ApiError("logo_ilisible", "Le logo fourni n'est pas une image lisible.") from exc
        # Surfaces, pas cotes : la regle des 20 % porte sur la **zone couverte**, done le cote vient de
        # la racine du rapport, multiplie par la taille d'image (pas de la racine d'un produit mixte).
        cote_logo = int(cote_image * (min(couverture_max, LOGO_SURFACE_MAX) * 0.92) ** 0.5)
        cote_logo -= cote_logo % 2
        reech = getattr(Image, "Resampling", Image).LANCZOS  # nom Pillow >= 9.1, avec repli
        marque = marque.resize((cote_logo, cote_logo), reech)
        halo = max(module, cote_logo // 8)
        milieu = cote_image // 2
        trait.rounded_rectangle(
            [
                milieu - cote_logo // 2 - halo,
                milieu - cote_logo // 2 - halo,
                milieu + cote_logo // 2 + halo,
                milieu + cote_logo // 2 + halo,
            ],
            radius=halo // 2,
            fill=style["claire"],
        )
        image.paste(marque, (milieu - cote_logo // 2, milieu - cote_logo // 2), marque)
        couverture = (cote_logo / cote_image) ** 2
    return image, module, couverture


def _dans_detecteur(x: int, y: int, modules: int) -> bool:
    """Vrai si le module tombe dans un des trois repères de position, garde inclusive."""
    zone = 8  # 7 modules de detecteur + le silence qui les entoure
    return (x < zone and y < zone) or (x >= modules - zone and y < zone) or (x < zone and y >= modules - zone)


def rendre(qr, *, contenu: str, style: str, taille: int, logo: bytes | None = None) -> tuple[bytes, str, Score]:
    """Compose, relit, et ne rend que ce qui se redéchiffre. Retourne `(octets_png, mime, score)`.

    L'ordre des tentatives est le style demandé puis les replis. Un echec de relecture n'est pas une
    fatalite : c'est souvent le motif, pas le contenu — et le client repart avec une image lisible plutot
    qu'avec une erreur, a condition qu'on le lui dise (`repli: true` dans le score).
    """
    from apps.qr import render

    style = (style or "encre").strip().lower()
    if style not in STYLES:
        raise ApiError("style_inconnu", f"Style artistique inconnu : `{style}`.", details={"supportes": sorted(STYLES)})
    reglages = STYLES[style]
    ratio = contraste(reglages["sombre"], reglages["claire"])
    if ratio < CONTRASTE_MIN:  # demande explicite, refusee avant de consommer du CPU
        raise ApiError(
            "contraste_insuffisant",
            f"Le style `{reglages['nom']}` presente un contraste de {ratio}:1, sous le seuil de "
            f"{CONTRASTE_MIN}:1 necessaire a une impression fiable.",
            details={"contraste": ratio, "seuil": CONTRASTE_MIN},
        )

    code = render.code_source(contenu, ecc="H", margin=ZONE_SILENCE)
    dernier: Score | None = None
    echecs = 0
    # Les tentatives sont ordonnees du plus beau au plus prudent : style demande, puis replis de style ;
    # a l'interieur, le logo est reduit (0.10 -> 0.07 -> 0.05) avant d'etre retire. Un client qui recoit
    # son QR avec un logo plus petit que prevu doit le savoir (le score le dit), mais il recoit surtout
    # un QR qui se lit — pas une erreur.
    for nom_style in [style, *REPLIS]:
        reglages = STYLES[nom_style]
        pas_logo = [None] if not logo else [*LOGO_REDUGRADATIONS, None]
        for couverture_max in pas_logo:
            # `couverture_max`, pas `ratio` : le ratio de contraste du style est deja dans une variable du
            # meme scope, et l'ecraser ici rendait le score faux sur les tentatives suivantes (et mypy rouge).
            image, module, couverture = _dessiner(
                code, style=reglages, taille=taille, logo=logo, couverture_max=couverture_max or 0.0
            )
            tampon = io.BytesIO()
            image.save(tampon, format="PNG", optimize=True)
            octets = tampon.getvalue()
            decode, exact, largeur, lu, epreuve = relire(octets, contenu=contenu)
            score = Score(
                style=nom_style,
                modules=code.modules_count,
                module_px=module,
                taille_px=largeur or image.size[0],
                zone_silence_modules=code.border,
                taux_contraste=contraste(reglages["sombre"], reglages["claire"]),
                couverture_logo=round(couverture, 4),
                ecc="H",
                decode=decode,
                contenu_attendu=exact,
                contenu_recupere=lu,
                epreuve=epreuve,
                repli=nom_style != style,
                logo_reduit=bool(couverture_max and couverture_max < LOGO_SURFACE_MAX),
                logo_retire=couverture_max is None and bool(logo),
            )
            if decode and exact:
                if nom_style != style or couverture_max != pas_logo[0]:
                    # Un ajustement doit se voir dans le journal comme dans le score : c'est la trace qui
                    # explique au support pourquoi le logo du client est plus petit que sur son mail.
                    logger.info(
                        "qr art: ajustement pour rester lisible (id=%s, style=%s, logo=%s, contraste=%s, "
                        "tentatives=%s)",
                        getattr(qr, "pk", None),
                        nom_style,
                        "retire" if score.logo_retire else f"{couverture_max}",
                        ratio,
                        echecs,
                    )
                return octets, "image/png", score
            dernier = score
            echecs += 1
    raise ApiError(
        "art_ilisible",
        "Aucun style, meme sans logo, ne produit une image qui se relit a cette taille : le contenu est "
        f"trop dense pour {taille} px. Augmentez la taille ou raccourcissez le contenu.",
        details={"style": style, "taille": taille, "dernier_score": asdict(dernier) if dernier else None},
        status_code=409,
    )
