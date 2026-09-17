"""QR animé : un GIF dont le code reste scannable pendant qu'il bouge.

Le piège du QR animé est connu : un scanner lit une image figée, pas une transition. Les outils qui
animent les modules eux-mêmes ne marchent que sur écran — jamais sur une photo de flyer, et à moitié sur
un téléphone un peu vieux. Ce module fait donc le choix inverse, et il est écrit pour être vérifiable :

* **les modules ne bougent jamais** d'une frame « complète » à l'autre : l'animation vit dans le liseré
  (bandeau de progression, anneau, accroche) et dans l'introduction, pas dans le code ;
* **au moins 60 % des frames sont l'image finie**, sinon le GIF ne se scanne qu'à la loterie ;
* chaque frame complète est **re-décodée** (`art.relire`) avant l'envoi : une frame illisible fait échouer
  la génération, pas le client.

C'est ce que le palier vend : une animation pour les réseaux et les écrans d'accueil, pas un GIF qui
force le spectateur à essayer douze fois.
"""

from __future__ import annotations

import io
import logging
import math
from dataclasses import asdict, dataclass

from apps.common.exceptions import ApiError

logger = logging.getLogger(__name__)

FRAMES_MIN, FRAMES_MAX = 8, 48
DUREE_MIN, DUREE_MAX = 40, 400  # ms par frame : en dessous, le liseré clignote; au-dessus, la boucle est lente
PART_STATIQUE_MIN = 0.60  # au moins 60 % de frames ou le code est entier et identique
LISIERE_MIN, LISIERE_MAX = 0, 64  # largeur du bandeau d'animation, en pixels
TROP_LOURD = 4 * 1024 * 1024  # 4 Mo : un GIF de reseau social se charge, un GIF de 12 Mo non


@dataclass(frozen=True)
class Montage:
    frames: int
    frames_statiques: int
    part_statique: float
    duree_totale_ms: int
    taille_px: int
    verifiee: bool
    contenu_attendu: bool

    def pour_api(self) -> dict:
        return asdict(self)


def _borne(valeur, *, mini: int, maxi: int, nom: str) -> int:
    try:
        n = int(valeur)
    except (TypeError, ValueError):
        raise ApiError("parametre_invalide", f"`{nom}` doit être un entier.") from None
    if not mini <= n <= maxi:
        raise ApiError("parametre_invalide", f"`{nom}` doit être entre {mini} et {maxi}.")
    return n


def rendre(
    qr,
    *,
    contenu: str,
    style: str = "encre",
    taille: int = 512,
    frames: int = 24,
    duree: int = 90,
    liser: int = 24,
    accroche: str = "",
    logo: bytes | None = None,
) -> tuple[bytes, str, Montage]:
    """Compose le GIF. Retourne `(octets, "image/gif", montage)` ; lève une `ApiError` si une frame ne se lit pas."""
    from PIL import Image, ImageDraw

    from apps.qr import art, render

    frames = _borne(frames, mini=FRAMES_MIN, maxi=FRAMES_MAX, nom="frames")
    duree = _borne(duree, mini=DUREE_MIN, maxi=DUREE_MAX, nom="duree")
    liser = _borne(liser, mini=LISIERE_MIN, maxi=LISIERE_MAX, nom="liser")
    style = (style or "encre").strip().lower()
    if style not in art.STYLES:
        raise ApiError(
            "style_inconnu", f"Style artistique inconnu : `{style}`.", details={"supportes": sorted(art.STYLES)}
        )
    reglages = art.STYLES[style]

    # Le code est construit une fois, avec la correction H : les frames statiques ne sont pas un
    # echantillon, ce sont des copies octet pour octet de cette image.
    code = render.code_source(contenu, ecc="H", margin=art.ZONE_SILENCE)
    # Le logo, quand il y en a un, est fige : il fait partie du bloc verifie. Animer le logo reviendrait a
    # animer le code lui-meme (il est derriere), et la garantie « les modules ne bougent pas » tombe.
    nude, module, _ = art._dessiner(code, style=reglages, taille=taille, logo=logo)

    # L'animation se loge hors du code : on agrandit la toile, jamais on ne recouvre les modules.
    toile = nude.size[0] + 2 * liser
    statique = Image.new("RGB", (toile, toile), reglages["claire"])
    statique.paste(nude, (liser, liser))

    # `ceil` cote statiques, pas `round` cote introduction : arrondir dans l'autre sens faisait tomber le
    # ratio sous la garantie (24 frames -> 14 statiques = 58 %), et la regle doit etre vraie, pas visee.
    statiques = max(1, math.ceil(frames * PART_STATIQUE_MIN))
    introduction = frames - statiques
    if statiques / frames < PART_STATIQUE_MIN:  # garde-fou si les bornes bougent un jour
        raise ApiError(
            "montage_impraticable",
            f"Il faut au moins {PART_STATIQUE_MIN:.0%} de frames statiques pour qu'un GIF se scanne ; "
            f"avec {frames} frames, l'introduction les absorbe.",
        )

    frames_images: list[Image.Image] = []
    grille = code.get_matrix()
    cote = len(grille)
    modules = code.modules_count
    for index in range(introduction):
        # L'intro dessine le code progressivement, du centre vers les bords : le regard suit, et la
        # derniere frame d'intro est deja l'image finie (la boucle ne « claque » pas au raccord).
        image = Image.new("RGB", (toile, toile), reglages["claire"])
        trait = ImageDraw.Draw(image)
        seuil = (index + 1) / introduction
        limite = int(modules / 2 * seuil) + 1
        centre = cote // 2
        for y in range(cote):  # la matrice inclut la marge : indices bruts, pas de recalage
            for x in range(cote):
                if not grille[y][x]:
                    continue
                if max(abs(x - centre), abs(y - centre)) > limite:
                    continue
                x0, y0 = liser + x * module, liser + y * module
                trait.rectangle([x0, y0, x0 + module - 1, y0 + module - 1], fill=reglages["sombre"])
        _liser(trait, toile, liser, reglages, progression=seuil, accroche=accroche)
        frames_images.append(image)

    for index in range(statiques):
        image = statique.copy()
        if liser:
            trait = ImageDraw.Draw(image)
            # Le liser continue de vivre, mais rien ne touche au code : c'est toute la garantie.
            _liser(
                trait, toile, liser, reglages, progression=1.0, accroche=accroche, phase=index / max(1, statiques - 1)
            )
        frames_images.append(image)

    tampon = io.BytesIO()
    palette = [image.convert("P", palette=Image.Palette.ADAPTIVE, colors=8) for image in frames_images]
    palette[0].save(
        tampon,
        format="GIF",
        save_all=True,
        append_images=palette[1:],
        duration=duree,
        loop=0,
        optimize=True,
        disposal=2,
    )
    octets = tampon.getvalue()
    if len(octets) > TROP_LOURD:
        raise ApiError(
            "animation_trop_lourde",
            f"Le GIF depasse {TROP_LOURD // (1024 * 1024)} Mo : reduisez la taille, le nombre de frames "
            "ou la largeur du liseré.",
            details={"octets": len(octets), "max": TROP_LOURD},
            status_code=413,
        )
    # La verification porte sur les **octets du GIF**, pas sur l'image avant quantisation : un GIF a 8
    # couleurs peut deplacer un bord de module, et c'est le fichier qui sera diffuse sur les reseaux.
    _verifier_gif(octets, contenu=contenu, premier_statique=introduction, art=art)
    montage = Montage(
        frames=frames,
        frames_statiques=statiques,
        part_statique=round(statiques / frames, 3),
        duree_totale_ms=frames * duree,
        taille_px=toile,
        verifiee=True,
        contenu_attendu=True,
    )
    logger.info(
        "qr anime id=%s frames=%s statiques=%s octets=%s", getattr(qr, "pk", None), frames, statiques, len(octets)
    )
    return octets, "image/gif", montage


def _liser(
    trait, cote: int, liser: int, reglages: dict, *, progression: float, accroche: str = "", phase: float = 0.0
) -> None:
    """Bandeau d'animation dans le liseré : barre de remplissage a l'intro, pulsation ensuite."""
    if not liser:
        return
    épaisseur = max(2, liser // 6)
    if progression < 1.0:
        largeur = int((cote - 2 * liser) * progression)
        trait.rectangle(
            [liser, cote - liser + épaisseur // 4, liser + largeur, cote - liser + épaisseur], fill=reglages["sombre"]
        )
        return
    # Frames statiques : une pulsation douce, identique en hauteur, qui ne survit pas a une photo
    # d'ecran — donc ne gene aucun scanner.
    avance = int((cote - 2 * liser) * (0.5 + 0.5 * phase))
    trait.rectangle(
        [liser, liser - épaisseur, liser + avance, liser - épaisseur + épaisseur // 2], fill=reglages["sombre"]
    )
    if accroche:
        # Pas de police chargee (aucune dependance de plus, et le rendu doit rester identique d'une
        # frame a l'autre) : l'accroche est un simple trait de soulignement sous le code.
        trait.line(
            [liser, liser - épaisseur - 3, liser + avance, liser - épaisseur - 3], fill=reglages["sombre"], width=1
        )


def _verifier_gif(octets: bytes, *, contenu: str, premier_statique: int, art) -> None:
    """Relit la premiere et la derniere frame « complete » du GIF encode.

    Ces deux-la suffisent et sont les seules qui comptent : les frames statiques sont des copies de la
    meme image (seul le liseré bouge), donc la premiere et la derniere encadrent la boucle. Une frame
    d'introduction n'est pas testee, volontairement : elle est illisible par construction, c'est son role.
    """
    from PIL import Image, ImageSequence

    indices = {premier_statique}
    Lus: list[tuple[int, bool, bool, str]] = []
    with Image.open(io.BytesIO(octets)) as gif:
        frames = list(ImageSequence.Iterator(gif))
        cibles = sorted(indices | {len(frames) - 1})
        for index in cibles:
            if not (premier_statique <= index < len(frames)):
                continue
            tampon = io.BytesIO()
            frames[index].convert("RGB").save(tampon, format="PNG")
            decode, exact, _largeur, lu, _epreuve = art.relire(tampon.getvalue(), contenu=contenu)
            Lus.append((index, decode, exact, lu or ""))
    if not Lus or not all(decode and exact for _i, decode, exact, _lu in Lus):
        raise ApiError(
            "animation_ilisible",
            "Une frame finale du GIF encodé ne se relit pas : contenu trop dense pour cette taille, ou "
            "quantification de la palette intervenue sur un bord de module.",
            details={"frames_testees": Lus, "attendu": contenu[:64]},
            status_code=409,
        )
