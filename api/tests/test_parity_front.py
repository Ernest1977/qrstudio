"""Le serveur et le front doivent connaitre les memes 22 types.

Sans ce garde-fou, un type ajoute cote front s'enregistre en `url` cote serveur et I'utilisateur
retrouve un QR dont le libelle est faux dans sa liste - incident sans message d'erreur, donc non
rapporte. Le test lit le fichier source du front : il est rouge des la première divergence.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings

from apps.qr.types import KNOWN_TYPE_IDS

FRONT = Path(__file__).resolve().parents[2] / "qr-coding-react" / "src/lib/types.js"
# Le front déclare les types « pleins » en `{ id: 'x' }` et les familles via des fabriques
# `social('instagram', ...)` / `sharing('pdf', ...)` : il faut lire les deux formes, sinon la
# comparaison accuse 10 types d'être orphelins.
MOTIF = re.compile(r"""(?:\{\s*id:\s*|social\(\s*|sharing\(\s*)['"]([a-z0-9]+)['"]""")


def _ids_du_front() -> set[str]:
    source = FRONT.read_text(encoding="utf-8")
    ids = set(MOTIF.findall(source))
    assert ids, "aucun identifiant de type lu dans le front: le motif du test a change"
    return ids


@pytest.mark.skipif(not FRONT.is_file(), reason="le depot du front n'est pas a cote de l'api")
def test_tous_les_types_du_front_sont_acceptes_par_l_api():
    inconnus = _ids_du_front() - KNOWN_TYPE_IDS
    assert not inconnus, f"types connus du front mais refuses par l'API: {sorted(inconnus)}"


@pytest.mark.skipif(not FRONT.is_file(), reason="front absent")
def test_aucun_type_orphelin_cote_serveur():
    orphelins = KNOWN_TYPE_IDS - _ids_du_front()
    assert not orphelins, f"types acceptes par l'API mais absents du front: {sorted(orphelins)}"


@pytest.mark.skipif(not FRONT.is_file(), reason="front absent")
def test_la_liste_sert_a_quelque_chose():
    # 22 = le nombre reel du studio (12 reseaux/usages + 4 fichiers + texte/wifi/tel/sms/mail/event).
    assert len(_ids_du_front()) == 22 == len(KNOWN_TYPE_IDS)


def test_la_capacite_rappelee_dans_le_message_est_bonnee():
    """2953 octets = version 40 / ecc L, mesure avec la lib `qrcode`. Si ce nombre bouge, le
    message d'erreur de `QrSerializer` ment a l'utilisateur."""
    assert settings.QR["MAX_PAYLOAD_BYTES"] == 2953


# ------------------------------------------------------------------ capacites et limites affichees
# `content.js` est le frere de `types.js`, pas son parent : `FRONT.parent` donnait
# `src/lib/content.js`, inexistant — et les trois tests de parite des libelles se douchaient en
# « front absent » sans jamais rien verifier. Un test qui saute est un test qui ne protege rien.
CONTENT = FRONT.parents[1] / "content.js"
MOTIF_CLE = __import__("re").compile(r"^\s{2}([a-z_][a-z0-9_]*):", __import__("re").M)


def _bloc(nom: str) -> set[str]:
    """Les cles d'un objet exporte de `content.js` (`PALIERS_LABELS`, `LIMITES_LABELS`)."""
    source = CONTENT.read_text(encoding="utf-8")
    depart = source.index(f"export const {nom} = {{")
    suite = source[depart + len(f"export const {nom} = {{") :]
    corps = suite[: suite.index("\n};")]
    return set(MOTIF_CLE.findall(corps))


def _toutes_les_caracteristiques() -> set[str]:
    from apps.accounts import plans

    return set().union(*(plans.caracteristiques(code) for code in plans.ORDRE))


def _toutes_les_limites() -> set[str]:
    from apps.accounts import plans

    trouve = set()
    for code in plans.ORDRE:
        trouve |= set((plans.PALIERS[code].limites or {}).keys())
    return trouve


@pytest.mark.skipif(not CONTENT.is_file(), reason="front absent")
def test_toute_caracteristique_de_la_grille_a_un_libelle_dans_le_front():
    """Une capacite sans libelle s'affiche `qr_artistique_ia` chez le client : visible, jamais rouge.

    C'est le test qui manque quand on ajoute un poste a un palier : l'API fonctionne, la page d'abonnement
    aussi, et seule la carte du palier montre une cle interne.
    """
    orphelines = _toutes_les_caracteristiques() - _bloc("PALIERS_LABELS")
    assert not orphelines, f"capacites sans libelle cote front : {sorted(orphelines)}"


@pytest.mark.skipif(not CONTENT.is_file(), reason="front absent")
def test_aucun_libelle_de_capacite_ne_survit_a_sa_suppression_dans_la_grille():
    inverse = _bloc("PALIERS_LABELS") - _toutes_les_caracteristiques()
    assert not inverse, f"libelles front sans capacite correspondante (grille modifiee ?) : {sorted(inverse)}"


@pytest.mark.skipif(not CONTENT.is_file(), reason="front absent")
def test_toute_limite_de_la_grille_a_un_libelle():
    orphelines = _toutes_les_limites() - _bloc("LIMITES_LABELS")
    assert not orphelines, f"limites affichees sans libelle : {sorted(orphelines)}"


def test_les_quatre_postes_du_sprint_sont_bien_place_dans_la_grille():
    """Art et anime en Premium **et** Entreprise, codes promo en Entreprise seul, un seul statique en Gratuit.

    La demande est litterale ; ce test est la seule chose qui empeche un reclassement futur d'etre silencieux.
    """
    from apps.accounts import plans

    pour("qr_artistique_ia", "qr_anime").sont_au_moins("premium")
    pour("codes_promo", "boutique_templates").sont_au_moins("business")
    assert plans.limite("free", "statiques", 1) == 1
    assert plans.limite("premium", "codes_promo_max", 0) == 0, "le lot promo reste une porte Entreprise"
    assert plans.limite("business", "codes_promo_max", 0) == 50


class pour:
    def __init__(self, *caracteristiques):
        self.caracteristiques = caracteristiques

    def sont_au_moins(self, palier: str) -> None:
        from apps.accounts import plans

        for caract in self.caracteristiques:
            assert plans.palier_minimum(caract) == palier, (
                f"`{caract}` est annonce comme une capacite {palier} alors que la grille la place des {plans.palier_minimum(caract)}"
            )
