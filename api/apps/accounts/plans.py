"""Grille tarifaire et ce que chaque palier ouvre — **la source unique de vérité**.

Trois règles de forme, parce que c'est là que les grilles tarifaires pourrissent :

* **un seul dictionnaire par palier, et une échelle ordonnée**. Les caractéristiques sont héritées du
  palier précédent : votre description (« standard : score, cadres CTA, export PDF, thèmes »,
  « premium : idem + QR dynamiques + analytics ») est cumulative, donc elle est écrite une fois par
  caractéristique, au palier où elle apparaît. Dupliquer la liste dans quatre endroits différents est
  la garantie qu'un jour l'un des quatre aura oublié le correctif ;
* **les prix vivent ici, pas dans une variable d'environnement**. Un prix est une décision produit
  affichée à la face du client ; le laisser surchargeable par l'environnement d'un serveur, c'est
  pouvoir facturer 2,99 € sur une instance et 8,99 € sur une autre sans qu'aucun test ne s'en aperçoive ;
* **les quotas sont surchargeables par l'environnement, les paliers non**. En dessous d'un seuil, on
  ajuste la générosité sans redéployer de code ; on ne change pas ce que le client a acheté.

Les valeurs monétaires sont en **centimes** : un flottant pour de l'argent est la deuxième erreur la
plus fréquente du métier (la première étant d'arrondir au mauvais endroit).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _eur(centimes: int) -> str:
    """`899` -> `8,99` — format d'affichage français, une seule implémentation."""
    return f"{centimes // 100},{centimes % 100:02d}"


ORDRE = ("free", "standard", "premium", "business")


@dataclass(frozen=True)
class Palier:
    code: str
    nom: str
    prix_centimes: int
    # Caractéristiques introduites par CE palier (le palier suivant les hérite).
    apporte: tuple[str, ...] = ()
    limites: dict[str, int | None] = field(default_factory=dict)

    @property
    def prix_eur(self) -> str:
        return _eur(self.prix_centimes)


# `None` dans `limites` = illimité. Les seuils sont choisis pour être tenables sur le scénario A
# (3 workers, Postgres mono-instance) : ils sont là pour protéger l'infrastructure, pas pour punir.
PALIERS: dict[str, Palier] = {
    "free": Palier(
        code="free",
        nom="Gratuit",
        prix_centimes=0,
        # Une seule creation statique : le Gratuit sert a decouvrir l'objet (un QR qui marche, imprime,
        # scannes), pas a equiper une boutique. Le plafond bas est aussi ce qui rend le quota lisible :
        # « 1 » se verifie du premier coup d'oeil, « 20 » se discute.
        #
        # Les licences deja emises ne descendent PAS a 1 : `User.quota_statique_gele` porte le niveau
        # acquis (20) et le plafond ne peut que protéger ce qui est deja la, jamais le rogner — voir
        # `apps/accounts/models.py` et la migration `0003_gele_quota_statique`.
        apporte=("qr_statique", "export_image"),
        limites={"statiques": 1, "dynamiques_30j": 0, "historique_jours": 0, "exports_par_jour": 0},
    ),
    "standard": Palier(
        code="standard",
        nom="Standard",
        prix_centimes=299,
        # Le palier « studio augmenté » : tout ce qui se fabrique côté navigateur ou en un seul
        # fichier, sans engagement de service en ligne. Aucun QR dynamique ici — c'est votre liste,
        # et c'est aussi ce qui protège le coût du lien court (le seul poste qui scale).
        apporte=("score_scannabilite", "cadres_cta", "export_pdf", "themes_sectoriels"),
        limites={
            "statiques": 500,
            "dynamiques_30j": 0,
            "historique_jours": 0,
            "exports_par_jour": 100,
            "codes_promo_max": 0,
        },
    ),
    "premium": Palier(
        code="premium",
        nom="Premium",
        prix_centimes=899,
        # À partir d'ici le service est en ligne : le slug, la redirection mesurée, l'agrégat.
        # L'art et l'animation sont des rendus, pas des nouveaux objets : le QR et son payload restent
        # les memes, seule la facon dont il est dessine change. Ils sont ici et pas en Standard parce
        # qu'ils coutent du CPU par demande (compose + re-decode) la ou le Standard ne coute rien.
        apporte=("qr_dynamique", "analytics", "qr_artistique_ia", "qr_anime"),
        limites={
            "statiques": 5000,
            "dynamiques_30j": 100,
            "historique_jours": 90,
            "exports_par_jour": 500,
            "codes_promo_max": 0,
        },
    ),
    "business": Palier(
        code="business",
        nom="Entreprise",
        prix_centimes=1599,
        # La carte de visite connectée et le multi-liens sont des QR *dynamiques enrichis* : ils
        # supposent donc le palier premium (héritage), et l'API développeurs est le seul palier où
        # un jeton de service a du sens.
        # Les codes promo sont l'unique raison pour laquelle ce palier touche au chemin de redirection :
        # une offre qui s'eteint toute seule (date, puis nombre d'usages) ne se joue pas sur le client
        # qui scanne, elle se verifie cote serveur a chaque passage.
        apporte=(
            "carte_visite_connectee",
            "qr_multi_liens",
            "api_developpeurs",
            "codes_promo",
            "boutique_templates",
        ),
        limites={
            "statiques": None,
            "dynamiques_30j": 2000,
            "historique_jours": 365,
            "exports_par_jour": None,
            # 50 codes actifs par compte : assez pour une operation, assez peu pour que la table reste
            # petite devant les QR. Un `Campaign` de 5 000 codes se genere par lot, pas en ligne.
            "codes_promo_max": 50,
        },
    ),
}


def palier(code: str | None) -> Palier:
    return PALIERS.get(code or "free", PALIERS["free"])


def rang(code: str | None) -> int:
    try:
        return ORDRE.index(code or "free")
    except ValueError:
        return 0


def caracteristiques(code: str | None) -> frozenset[str]:
    """Union des apports de ce palier **et des précédents** — l'héritage, pas une copie."""
    n = rang(code)
    return frozenset(c for p in ORDRE[: n + 1] for c in PALIERS[p].apporte)


def a_caracteristique(code: str | None, nom: str) -> bool:
    return nom in caracteristiques(code)


def palier_minimum(caracteristique: str) -> str:
    """Le premier palier qui apporte cette caracteristique — pour les messages 402 et les badges du front.

    Ecrit ici et pas dans le front : un libelle « reserve au palier X » calcote deux endroits est la
    garantie qu'un jour les deux chiffres ne se recoupent plus, et c'est le client qui le voit.
    """
    for code in ORDRE:
        if caracteristique in PALIERS[code].apporte:
            return code
    raise ValueError(f"caracteristique inconnue de la grille : {caracteristique!r}")


def limite(code: str | None, nom: str, defaut: int | None = None) -> int | None:
    """`None` = illimité. Les variables `QUOTA_<PALIER>_<LIMITE>` permettent d'ouvrir un robinet
    sans déployer (support, promotion, client qui attend une facture) — jamais de changer de palier."""
    brute = os.environ.get(f"QUOTA_{(code or 'free').upper()}_{nom.upper()}")
    if brute is not None:
        return None if brute in {"", "-1", "illimite", "illimité"} else int(brute)
    return palier(code).limites.get(nom, defaut)


def palier_requis(palier_cible: str) -> str:
    """Le nom du palier à vendre pour débloquer quelque chose — utilisé dans les messages 402."""
    return palier(palier_cible).nom


def plan_depuis_ancien_identifiant(code: str | None) -> str:
    """Mappage des anciens identifiants (`pro`, `team`) vers la grille réelle.

    La migration de données l'appelle ; la fonction est publique et testée parce qu'un script
    d'import ou un jeton encore valide peut arriver avec l'ancienne valeur. Un `KeyError` ici
    signifierait un compte redescendu en gratuit par accident — on préfère donc l'escalier explicite.
    """
    return {"pro": "premium", "team": "business", "free": "free"}.get(code or "free", code or "free")


def tableau_pour_api(code: str | None) -> dict:
    """Ce que `/auth/me` et le front lisent pour savoir quoi afficher, verrouiller ou facturer."""
    p = palier(code)
    return {
        "code": p.code,
        "nom": p.nom,
        "prix_centimes": p.prix_centimes,
        "prix_eur": p.prix_eur,
        "caracteristiques": sorted(caracteristiques(p.code)),
        "limites": dict(p.limites),
        "paliers": [
            {
                "code": q.code,
                "nom": q.nom,
                "prix_centimes": q.prix_centimes,
                "prix_eur": q.prix_eur,
                "apporte": sorted(q.apporte),
            }
            for q in PALIERS.values()
        ],
    }
