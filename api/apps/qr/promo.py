"""Codes promo d'une campagne : générés, datés, et vérifiés au moment du scan.

Le produit vendu par le palier Entreprise n'est pas « un champ texte où l'on colle un code » : c'est un
code qui **meurt** à une date, ou à un nombre d'usages, vérifié côté serveur à chaque passage devant le
QR. Un code lu par le navigateur se contrefait en une minute ; un code vérifié à la redirection non.

Trois décisions qui viennent de cette différence :

* **l'alphabet exclut `0/O` et `1/I`** — un code recopié depuis un flyer ou lu à voix haute ne doit pas
  pouvoir devenir un autre code. Ce n'est pas un détail d'esthétique : c'est le seul endroit où une
  ambiguïté de lecture devient une remise accordée à tort ;
* **la date d'expiration est obligatoire** à la création (pas de valeur par défaut éternelle) : une
  offre sans fin n'est pas une offre, c'est une fuite ;
* **le compteur d'usages vit dans Redis**, pas dans la ligne de redirection : le chemin chaud ne fait
  aucune écriture SQL (invariant de `apps/redirect/views.py`), et la table `usages` est resserrée par
  `manage.py sync_promo_usages`. Une surestimation d'un ou deux usages pendant la fenêtre de vidange est
  acceptable ; bloquer un scanneur pour une écriture de compteur ne l'est pas.
"""

from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass
from datetime import datetime

from django.core.cache import cache
from django.utils import timezone

#: Ni 0/O ni 1/I : voir le module. 32 caractères = log2(32^4) ≈ 20 bits par bloc de 4, soit 40 bits
#: avec le suffixe — largement assez pour que deux comptes ne se télescopent pas, pas assez pour
#: deviner un code valide au hasard (1 sur 1e12).
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
LONGUEUR_SUFFIXE = 4
SEPARATEUR = "-"

#: Les deux seuls replis de saisie autorises (voir `cle_normalisee`) : ils ne peuvent pas creer de
#: collision, parce que `ALPHABET` ne contient ni `0` ni `1`.
_CONFUSABLES = str.maketrans({"0": "O", "1": "I"})

#: Clé du compteur d'usages en cours de fenêtre. TTL aligné sur la journée : un compteur qui survivrait
#: à une correction manuelle de la table empêcherait de relancer une offre.
CLE_COMPTEUR = "qrs:promo:usages:{id}"
COMPTEUR_TTL = 60 * 60 * 25


@dataclass(frozen=True)
class Verdict:
    """Ce qu'on répond à « ce code est-il encore bon ? », avec le motif — jamais un simple booléen.

    Le motif est destiné au client final (« offre terminée » vs « quota atteint ») : ce ne sont pas les
    mêmes mots, et un visiteur qui arrive trois jours trop tôt n'a pas à lire « expiré ».
    """

    valide: bool
    motif: str  # "" | "expire" | "epuise" | "inactif"
    remise_type: str = ""
    remise_valeur: str | None = None
    devise: str = ""
    expire_le: datetime | None = None
    usages_restants: int | None = None

    @property
    def message(self) -> str:
        return {
            "expire": "Cette offre est terminée.",
            "epuise": "Tous les codes de cette offre ont été utilisés.",
            "inactif": "Cette offre n'est plus active.",
        }.get(self.motif, "Offre valide.")

    def pour_api(self) -> dict:
        return {
            **asdict(self),
            "expire_le": self.expire_le.isoformat() if self.expire_le else None,
            "message": self.message,
        }


#: Les mots qui ne disent rien d'une offre : sans ce filtre, « -30% sur la cave » devenait `SURLACAV`.
MOTS_OUTILS = frozenset(
    {
        "SUR",
        "LA",
        "LE",
        "LES",
        "DE",
        "DU",
        "DES",
        "EN",
        "ET",
        "AU",
        "AUX",
        "POUR",
        "AVEC",
        "CHEZ",
        "DANS",
        "UNE",
        "UN",
        "Offre".upper(),
        "PROMO",
        "CODE",
    }
)


def _racine(libelle: str) -> str:
    """`-30% sur la cave` -> `CAVE` : une amorce lisible, sans espaces ni accents, 8 lettres max.

    Un code intégralement aléatoire (`K7QF2M9X`) est plus sûr mais invendable sur un flyer : le
    commerçant dicte son code au téléphone. L'amorce ne porte aucune information sensible — le suffixe
    aléatoire, lui, fait le travail.
    """
    from unicodedata import normalize

    netto = normalize("NFKD", libelle or "").encode("ascii", "ignore").decode()
    mots = [
        mot
        for mot in netto.upper().replace("-", " ").split()
        if mot.isalnum() and not mot.isdigit() and mot not in MOTS_OUTILS
    ]
    base = "".join(mots)[:8] or "PROMO"
    return "".join(c for c in base if c in ALPHABET) or "PROMO"


def generer_code(*, libelle: str = "", prefixe: str = "") -> str:
    """Un code unique-en-forme : `PROMO-<amorce>-<4>` (ou `<prefixe>-<amorce>-<4>`)."""
    suffixe = "".join(secrets.choice(ALPHABET) for _ in range(LONGUEUR_SUFFIXE))
    tete = (prefixe or "PROMO").upper().strip()
    return SEPARATEUR.join(part for part in (tete, _racine(libelle), suffixe) if part)


def generer_lot(quantite: int, *, libelle: str = "", prefixe: str = "", deja_vus: set[str] | None = None) -> list[str]:
    """Plusieurs codes d'un coup, **sans doublon dans le lot** (l'unicité par compte est en base).

    Une liste construite par `set()` perd l'ordre et peut se terminer avant d'avoir rendu le compte
    demandé : ici on itère jusqu'à obtenir `quantite` codes distincts, avec un plafond de tentatives
    pour qu'un alphabet appauvri ne tourne pas à l'infini.
    """
    if not 1 <= int(quantite) <= 500:
        raise ValueError("quantité entre 1 et 500 par lot")
    pris = set(deja_vus or ())
    vus: set[str] = set()
    tentatives = 0
    while len(vus) < quantite and tentatives < quantite * 40:
        candidate = generer_code(libelle=libelle, prefixe=prefixe)
        tentatives += 1
        # On heurte la contrainte d'unicite en base le moins possible : un doublon ici ne serait pas une
        # erreur visible, ce serait un lot de 24 codes sur 25 — et le client ne le saurait pas.
        if candidate in pris:
            continue
        vus.add(candidate)
    if len(vus) < quantite:
        raise ValueError(f"impossible de produire {quantite} codes distincts (butes sur {len(vus)})")
    return sorted(vus)


def cle_normalisee(valeur: str) -> str:
    """Le code tel qu'on le stocke et tel qu'on le compare : majuscules, sans espaces, tirets unifiés.

    Sans cette normalisation, `promo-cave-ab12` saisi par un client est un code différent de
    `PROMO-CAVE-AB12` en base — et le commerçant entend « code inconnu » pour une offre valide.
    """
    import re

    # Blancs ET tirets bas sont des separateurs : un code dicte au telephone revient souvent en
    # « PROMO CAVE K7QF », et l'exiger avec les tirets exacts serait un « code inconnu » gratuit.
    brut = re.sub(r"[\s_]+", SEPARATEUR, (valeur or "").strip().upper())
    # Replis de saisie : `0` -> `O` et `1` -> `I`. Ce n'est pas une tolerance generale, c'est une
    # reparation possible **grace a** l'alphabet de generation : aucun code genere ne contient 0 ni 1
    # dans sa partie aleatoire, donc un de ces chiffres ne peut venir que du prefixe lisible (`PROMO`)
    # ou d'un `I` mal recopie. Plier dans l'autre sens (`O` -> `0`) mettrait deux offres distinctes en
    # collision, comme le ferait `5` -> `S` : ces caracteres-la sont legtimes dans le code.
    brut = brut.translate(_CONFUSABLES)
    # L'alphabet n'est **pas** un filtre de comparaison : `ALPHABET` exclut `O`, et le prefixe de
    # lisibilite s'appelle `PROMO`. Filtrer a l'execution transformait `PROMO-CAVE-K7QF` en
    # `PRM-CAVEK7QF` — c'est-a-dire qu'on aurait reecrit les codes en base en cassant les tirets.
    netto = "".join(c for c in brut if c.isalnum() or c == SEPARATEUR)
    return re.sub(rf"\{SEPARATEUR}+", SEPARATEUR, netto).strip(SEPARATEUR)


def cle_usages(promo_id: int) -> str:
    """La cle du compteur chaud d'une offre. Une seule fabrique de cle : sinon `sync_promo_usages`
    et `marquer_utilise` peuvent ne plus ecrire au meme endroit, et l'usage disparait en silence."""
    return CLE_COMPTEUR.format(id=promo_id)


def compteurs(*promo_ids: int) -> dict[int, int]:
    """Les usages deja consommes dans la fenetre courante (Redis), par id de code."""
    if not promo_ids:
        return {}
    cles = [cle_usages(p) for p in promo_ids]
    valeurs = cache.get_many(cles)
    return {int(cle.split(":")[-1]): int(valeurs.get(cle) or 0) for cle in cles}


def marquer_utilise(promo_id: int, *, maintenant: datetime | None = None) -> int:
    """Incmente le compteur courant et renvoie la valeur atteinte (0 si Redis est absent).

    Un `cache.incr` qui echoue (Redis en panne) ne doit pas empecher la vente : on retombe sur le compteur
    en base, qui sera resserré par la tache de nuit. Le prix a payer est une fenetre de survente possible,
    et c'est le bon echange — `usages_max` est un plafond de campagne, pas un stock unique.
    """
    cle = cle_usages(promo_id)
    try:
        valeur = cache.incr(cle)
    except ValueError:
        cache.set(cle, 1, COMPTEUR_TTL)
        valeur = 1
    return int(valeur or 0)


def statut(promo, *, maintenant: datetime | None = None) -> Verdict:
    """Le verdict de validité, utilisé par la redirection, l'API et le back-office (une seule règle)."""
    maintenant = maintenant or timezone.now()
    expire_le = promo.expire_le
    valide = bool(promo.actif)
    motif = "" if valide else "inactif"
    if valide and expire_le and expire_le <= maintenant:
        valide, motif = False, "expire"

    restants = None
    if valide and promo.usages_max:
        consommes = int(promo.usages) + compteurs(promo.pk).get(promo.pk, 0)
        restants = max(0, int(promo.usages_max) - consommes)
        if restants <= 0:
            valide, motif = False, "epuise"

    return Verdict(
        valide=valide,
        motif=motif,
        remise_type=promo.remise_type,
        remise_valeur=str(promo.remise_valeur) if promo.remise_valeur is not None else None,
        devise=promo.devise,
        expire_le=expire_le,
        usages_restants=restants,
    )
