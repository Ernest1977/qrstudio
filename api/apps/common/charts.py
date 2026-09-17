"""Graphiques du back-office : de l'SVG écrit côté serveur, rien d'autre.

Pourquoi pas une librairie JS : le back-office tourne derrière un login Django, ses pages sont rendues
en templates, et embarquer un bundler (ou un CDN, qui tombe au premier voyage réseau du datacenter) pour
tracer quatre courbes ajouterait un point de panne à l'endroit précis où l'on veut lire l'état du service.
Un `<svg>` produit ici se teste (`tests/test_backoffice.py` vérifie le nombre de points et les étiquettes),
s'imprime, et ne dépend d'aucune ressource externe.

Sécurité : les libellés viennent de la base (labels d'utilisateurs, noms de pays). Tout ce qui entre dans
le balisage passe par `escape()` ; les nombres, eux, sont formatés à la main — un `1 234` insécable ne se
échappe pas pareil qu'un texte.
"""

from __future__ import annotations

from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe


def _marquer(fragment: str) -> SafeString:
    """Unique sortie `mark_safe` du module.

    Le contrat est simple, et il est verifie par `test_un_libelle_hostile_ne_peut_pas_sortir_du_texte` :
    tout ce qui vient de la base passe par `escape()` en amont, tout ce qui vient d'un calcul est un nombre.
    Un `# nosec` centralise vaut mieux que trois suppressions eparses qu'on oublie de relire.
    """
    # Forme canonique `nosec B308, B703` : du texte libre derriere les identifiants fait dire a bandit
    # « Test in comment: … is not a test name or id, ignoring » a chaque passe, et un vrai probleme se
    # noierait dans ce bruit. La justification se lit ici, pas dans le marqueur.
    return mark_safe(fragment)  # nosec B308, B703


HAUTEUR_DEFAUT = 210
LARGEUR_DEFAUT = 800
MARGE = {"haut": 14, "droite": 12, "bas": 26, "gauche": 46}
PALETTE = ("#2563eb", "#0d9488", "#b45309", "#7c3aed", "#dc2626")


def nombre(valeur: int | None) -> str:
    """`12345` → `12 345` avec une espace fine insécable, lisible dans un tableau comme dans un <text>."""
    return f"{int(valeur or 0):,}".replace(",", "\u202f")


def _maxi(series: list[dict], plancher: int = 1) -> int:
    plus = max((max(ligne["points"], default=0) for ligne in series), default=0)
    return max(plancher, int(plus))


def _graduations(maxi: int, pas: int = 4) -> list[int]:
    return [round(maxi * i / pas) for i in range(pas + 1)]


def courbe(
    series: list[dict],
    *,
    labels: list[str],
    titre: str,
    largeur: int = LARGEUR_DEFAUT,
    hauteur: int = HAUTEUR_DEFAUT,
) -> SafeString:
    """Courbes multiples. Chaque ligne = `{"libelle": str, "points": [int, ...]}`.

    L'échelle est commune aux séries (sinon un comparatif visuel devient un mensonge) et l'axe des abscisses
    n'affiche que quelques dates : à 90 jours, toutes les étiquettes se chevauchent et ne disent plus rien.
    """
    utile_l = largeur - MARGE["gauche"] - MARGE["droite"]
    utile_h = hauteur - MARGE["haut"] - MARGE["bas"]
    maxi = _maxi(series)
    n = max(len(labels), 1)
    x = lambda i: MARGE["gauche"] + (utile_l * i / max(n - 1, 1))  # noqa: E731
    y = lambda v: MARGE["haut"] + utile_h * (1 - (v / maxi))  # noqa: E731

    parts = [
        f'<svg viewBox="0 0 {largeur} {hauteur}" width="100%" height="{hauteur}" role="img" '
        f'aria-label="{escape(titre)}" class="qrc-graph">'
        f"<title>{escape(titre)}</title>"
    ]
    for grad in _graduations(maxi):
        pos = y(grad)
        parts.append(
            f'<line x1="{MARGE["gauche"]}" y1="{pos:.1f}" x2="{largeur - MARGE["droite"]}" y2="{pos:.1f}" '
            f'stroke="#e2e8f0" stroke-width="1"/>'
            f'<text x="{MARGE["gauche"] - 6}" y="{pos + 4:.1f}" text-anchor="end" '
            f'font-size="10" fill="#64748b">{nombre(grad)}</text>'
        )
    if n:
        saut = max(1, -(-n // 6))  # 6 etiquettes au plus, arrondi superieur
        for indice in range(0, n, saut):
            parts.append(
                f'<text x="{x(indice):.1f}" y="{hauteur - 8}" text-anchor="middle" font-size="10" '
                f'fill="#64748b">{escape(labels[indice])}</text>'
            )
    for rang, ligne in enumerate(series):
        couleur = ligne.get("couleur") or PALETTE[rang % len(PALETTE)]
        points = ligne["points"]
        if not points:
            continue
        trace = " ".join(f"{x(indice):.1f},{y(valeur):.1f}" for indice, valeur in enumerate(points))
        parts.append(
            f'<polyline points="{trace}" fill="none" stroke="{couleur}" stroke-width="2" stroke-linejoin="round"/>'
        )
        # Les points ne servent que sur les series courtes : 90 jours x 3 series = 270 <circle>, illisible.
        if n <= 32:
            for indice, valeur in enumerate(points):
                # Une serie plus longue que l'axe n'est pas un motif de 500 sur un tableau de bord : on
                # laisse le point sans etiquette de date. (`labels` vient d'une serie agregee, `points`
                # d'une serie demandee — les deux peuvent diverger si un filtre a change entre les deux.)
                etiquette = labels[indice] if indice < len(labels) else ""
                parts.append(
                    f'<circle cx="{x(indice):.1f}" cy="{y(valeur):.1f}" r="2.5" fill="{couleur}">'
                    f"<title>{escape(ligne['libelle'])} {escape(etiquette)} : {nombre(valeur)}</title></circle>"
                )
    legende = "".join(
        f'<g transform="translate({MARGE["gauche"] + 92 * rang},{hauteur - 2})">'
        f'<rect width="9" height="9" y="-7" fill="{ligne.get("couleur") or PALETTE[rang % len(PALETTE)]}"/>'
        f'<text x="14" font-size="11" fill="#334155">{escape(ligne["libelle"])} : '
        f"{nombre(sum(ligne['points']))}</text></g>"
        for rang, ligne in enumerate(series)
    )
    parts.append(legende)
    parts.append("</svg>")
    return _marquer("".join(parts))


def barres(paires: list[tuple[str, int]], *, titre: str, largeur: int = 380, couleur: str = "#2563eb") -> SafeString:
    """Barres horizontales étiquetées : pays, canaux, top QR — ce qui se lit par rang, pas par tendance."""
    if not paires:
        return _marquer(
            f'<svg viewBox="0 0 {largeur} 40" width="100%" height="40" role="img" aria-label="{escape(titre)}">'
            f"<title>{escape(titre)}</title>"
            f'<text x="0" y="24" font-size="12" fill="#64748b">Aucune donnee sur la periode</text></svg>'
        )
    hauteur_ligne = 22
    hauteur = len(paires) * hauteur_ligne + 6
    place_texte = 132
    utile = largeur - place_texte - 62
    maxi = max(valeur for _, valeur in paires) or 1
    parts = [
        f'<svg viewBox="0 0 {largeur} {hauteur}" width="100%" height="{hauteur}" role="img" '
        f'aria-label="{escape(titre)}" class="qrc-graph"><title>{escape(titre)}</title>'
    ]
    for rang, (libelle, valeur) in enumerate(paires):
        bas = rang * hauteur_ligne + 4
        longueur = max(1, utile * valeur / maxi)
        parts.append(
            f'<text x="{place_texte - 8}" y="{bas + 12}" text-anchor="end" font-size="11" fill="#334155">'
            f"{escape(libelle)}</text>"
            f'<rect x="{place_texte}" y="{bas}" width="{longueur:.1f}" height="14" fill="{couleur}" rx="2">'
            f"<title>{escape(libelle)} : {nombre(valeur)}</title></rect>"
            f'<text x="{place_texte + longueur + 6:.1f}" y="{bas + 12}" font-size="11" fill="#0f172a">'
            f"{nombre(valeur)}</text>"
        )
    parts.append("</svg>")
    return _marquer("".join(parts))
