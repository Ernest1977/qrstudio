"""Codes promo générés pour les clients d'une campagne, et **vérifiés au scan**.

Le produit n'est pas « un champ de texte dans une base » : c'est un flyer imprimé qui doit continuer à
fonctionner pendant la campagne et s'arrêter net le lendemain de la date de fin, sans que personne ne
retire rien. Les tests ci-dessous portent donc sur les trois moments où ça casse d'habitude : la règle de
validité, le chemin chaud qui ne doit pas la réinventer, et le compteur qui ne doit ni brûler ni s'envoler.
"""

import asyncio
from datetime import timedelta

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db(transaction=True)


def _scan(slug, *, query="", ip="203.0.113.7"):
    """Un scan réel dans la vue asynchrone (mêmes helpers que `tests/test_redirect.py`)."""
    from django.test import RequestFactory

    from apps.redirect.views import scan_redirect

    request = RequestFactory().get(f"/r/{slug}{query}", REMOTE_ADDR=ip, HTTP_USER_AGENT="Mozilla/5.0 (iPhone)")
    request.client_ip = ip
    return asyncio.run(scan_redirect(request, slug))


def _offre(qr, *, expire_dans=3, usages_max=None, actif=True, remise="pourcentage", valeur="15"):
    from apps.qr.models import PromoCode

    return PromoCode.objects.create(
        owner=qr.owner,
        code="PROMO-CAVE-K7QF",
        libelle="-30% sur la cave",
        qr=qr,
        remise_type=remise,
        remise_valeur=valeur,
        devise="EUR",
        expire_le=timezone.now() + timedelta(days=expire_dans),
        usages_max=usages_max,
        actif=actif,
    )


# ------------------------------------------------------------------ génération
def test_un_code_est_genere_lisible_et_sans_ambiguite():
    from apps.qr import promo

    code = promo.generer_code(libelle="-30% sur la cave !")
    assert code.startswith("PROMO-CAVE-")
    suffixe = code.rsplit("-", 1)[1]
    assert len(suffixe) == 4
    # Ni 0/O ni 1/I dans la partie aleatoire : le commerçant dicte son code au telephone, et `O`/`0`
    # est la premiere cause de « code inconnu » sur une offre pourtant valide.
    assert not set(suffixe) & set("0O1I")
    assert set(suffixe) <= set(promo.ALPHABET)


def test_une_saisie_avec_des_zeros_est_quand_meme_reconnue():
    """`PR0M0-CAVE-K7QF` (le `O` tapé `0`) doit retrouver `PROMO-CAVE-K7QF`, et rien d'autre."""
    from apps.qr import promo

    assert promo.cle_normalisee("pr0m0-cave-k7qf") == promo.cle_normalisee("PROMO-CAVE-K7QF")
    assert promo.cle_normalisee("  promo_cave k7qf ") == "PROMO-CAVE-K7QF"
    # Deux codes distincts ne doivent jamais se rejoindre : la pliure ne s'applique qu'aux caracteres
    # absents de l'alphabet.
    assert promo.cle_normalisee("PROMO-CAVE-K7QF") != promo.cle_normalisee("PROMO-CAVE-K7QG")


def test_un_lot_sans_doublon_et_sans_reprendre_un_code_deja_pris():
    from apps.qr import promo

    codes = promo.generer_lot(40, libelle="Printemps")
    assert len(codes) == len(set(codes)) == 40
    pris = promo.generer_lot(5, libelle="Rentrée")
    seconds = promo.generer_lot(5, libelle="Rentrée", deja_vus=set(pris))
    assert not set(seconds) & set(pris)
    with pytest.raises(ValueError):
        promo.generer_lot(0)
    with pytest.raises(ValueError):
        promo.generer_lot(501)


# ------------------------------------------------------------------ règle de validité
def test_la_regled_e_validite_conna_trois_motifs_distincts(dynamic_qr):
    from apps.qr import promo

    maintenant = timezone.now()
    bonne = _offre(dynamic_qr, expire_dans=3)
    assert promo.statut(bonne, maintenant=maintenant).valide is True
    assert promo.statut(bonne, maintenant=maintenant).motif == ""

    assert promo.statut(bonne, maintenant=maintenant + timedelta(days=4)).motif == "expire"
    bonne.actif = False
    bonne.save()
    assert promo.statut(bonne, maintenant=maintenant).motif == "inactif"
    bonne.actif = True
    bonne.save()
    bonne.usages, bonne.usages_max = 4, 4
    bonne.save()
    verdict = promo.statut(bonne, maintenant=maintenant)
    assert (verdict.valide, verdict.motif) == (False, "epuise")
    assert verdict.usages_restants == 0
    # Les trois motifs ont trois messages : un visiteur qui arrive trop tôt ne doit pas lire « expiré ».
    assert len({promo.statut(bonne, maintenant=maintenant).message, promo.Verdict(False, "expire").message}) == 2


def test_un_compteur_chaud_compte_autant_que_la_base(dynamic_qr):
    """Redis a vu 2 usages, la base 0 : l'offre est close. Sans ce rapprochement, on survend la campagne."""
    from apps.qr import promo

    offre = _offre(dynamic_qr, expire_dans=3, usages_max=2)
    assert promo.marquer_utilise(offre.pk) == 1
    assert promo.marquer_utilise(offre.pk) == 2
    assert promo.statut(offre, maintenant=timezone.now()).motif == "epuise"
    assert promo.compteurs(offre.pk)[offre.pk] == 2


# ------------------------------------------------------------------ API
def test_crud_un_code_et_capacite_bloquee_selon_le_palier(api, django_user_model, dynamic_qr):
    from apps.qr.models import QrCode

    gratuit = django_user_model.objects.create_user(email="free@exemple.com", password="un-mot-de-passe-1", plan="free")
    qr = QrCode.objects.create(owner=gratuit, kind="static", type_id="text", payload="https://kamcofarm.com/a")
    api.force_authenticate(user=gratuit)
    reponse = api.post(
        "/api/v1/promo/codes/",
        {
            "libelle": "Fidelite",
            "remise_type": "pourcentage",
            "remise_valeur": "15",
            "qr": qr.pk,
            "expire_le": (timezone.now() + timedelta(days=5)).isoformat(),
        },
        format="json",
    )
    assert reponse.status_code == 402, reponse.data
    assert reponse.data["error"]["code"] == "plan_required"
    assert reponse.data["error"]["details"]["palier_requis"] == "business"

    business = django_user_model.objects.create_user(
        email="biz@exemple.com", password="un-mot-de-passe-1", plan="business"
    )
    leur_qr = QrCode.objects.create(owner=business, kind="static", type_id="text", payload="https://kamcofarm.com/b")
    api.force_authenticate(user=business)
    reponse = api.post(
        "/api/v1/promo/codes/",
        {
            "libelle": "-30% cave",
            "remise_type": "pourcentage",
            "remise_valeur": "30",
            "qr": leur_qr.pk,
            "expire_le": (timezone.now() + timedelta(days=5)).isoformat(),
        },
        format="json",
    )
    assert reponse.status_code == 201, reponse.data
    corps = reponse.data
    assert corps["code"].startswith("PROMO-CAVE-")  # genere, jamais saisi
    assert corps["usages"] == 0 and corps["statut"]["valide"] is True
    assert f"promo={corps['code']}" in corps["lien"]
    assert "/r/" in corps["lien"]  # le lien du QR, pas une URL inventee par le serializer
    # Un `code` fourni par le client est accepte **seulement** par le lot : le POST normal le surcharge.
    liste = api.get("/api/v1/promo/codes/")
    # Curseur, pas `count` : compter 5 M de lignes pour afficher une page de 25 est exactement ce que
    # `apps.common.pagination.CursorById` existe pour eviter (`results`, `next_url`, `previous_url`).
    assert liste.status_code == 200 and len(liste.data["results"]) == 1


def test_on_ne_peut_pas_attacher_un_code_au_qr_d_un_autre(auth_api, other_user):
    from apps.qr.models import QrCode

    leur = QrCode.objects.create(owner=other_user, kind="static", type_id="text", payload="https://kamcofarm.com/x")
    reponse = auth_api.post(
        "/api/v1/promo/codes/",
        {
            "libelle": "Voyeur",
            "remise_type": "livraison",
            "qr": leur.pk,
            "expire_le": (timezone.now() + timedelta(days=5)).isoformat(),
        },
        format="json",
    )
    assert reponse.status_code == 400, reponse.data
    assert "qr" in reponse.data["error"]["details"]


def test_une_remise_sans_valeur_ou_date_passe_pas(biz_api):
    auth_api = biz_api
    pour_centaine = {
        "libelle": "Flash",
        "remise_type": "pourcentage",
        "remise_valeur": "150",  # > 100 % : une offre a perte n'est pas une remise
        "expire_le": (timezone.now() + timedelta(days=5)).isoformat(),
    }
    reponse = auth_api.post("/api/v1/promo/codes/", pour_centaine, format="json")
    assert reponse.status_code == 400, reponse.data
    assert "remise_valeur" in reponse.data["error"]["details"]

    reponse = auth_api.post(
        "/api/v1/promo/codes/",
        {**pour_centaine, "remise_valeur": "20", "expire_le": (timezone.now() - timedelta(days=1)).isoformat()},
        format="json",
    )
    assert reponse.status_code == 400
    assert "expire_le" in reponse.data["error"]["details"]

    # `livraison` n'a pas de valeur : celle recue est videe, pas conservee pour etre affichee plus tard.
    reponse = auth_api.post(
        "/api/v1/promo/codes/",
        {
            "libelle": "Livraison",
            "remise_type": "livraison",
            "remise_valeur": "15",
            "expire_le": (timezone.now() + timedelta(days=5)).isoformat(),
        },
        format="json",
    )
    assert reponse.status_code == 201, reponse.data
    assert reponse.data["remise_valeur"] is None


@pytest.fixture
def business(django_user_model):
    """Compte Entreprise : `codes_promo` est son poste, et rien d'autre ne l'ouvre."""
    return django_user_model.objects.create_user(
        email="entreprise@exemple.com", password="un-mot-de-passe-solide-1", plan="business"
    )


@pytest.fixture
def biz_api(api, business):
    api.force_authenticate(user=business)
    return api


def test_un_lot_de_codes_en_un_appel(biz_api):
    """Le lot est la raison d'etre du palier : 25 codes a une equipe de salle, en un appel."""
    auth_api = biz_api
    reponse = auth_api.post(
        "/api/v1/promo/codes/generer/",
        {
            "quantite": 25,
            "libelle": "Rentrée",
            "remise_type": "pourcentage",
            "remise_valeur": "10",
            "expire_le": (timezone.now() + timedelta(days=10)).isoformat(),
            "usages_max": 3,
        },
        format="json",
    )
    assert reponse.status_code == 201, reponse.data
    assert reponse.data["cree"] == 25
    codes = [c["code"] for c in reponse.data["codes"]]
    assert len(set(codes)) == 25
    assert all(c["statut"]["valide"] and c["statut"]["usages_restants"] == 3 for c in reponse.data["codes"])
    # Le quota du palier (50 actifs en Entreprise) est compte, pas ignore.
    reponse = auth_api.post(
        "/api/v1/promo/codes/generer/",
        {"quantite": 40, "expire_le": (timezone.now() + timedelta(days=10)).isoformat()},
        format="json",
    )
    assert reponse.status_code == 402, reponse.data
    assert reponse.data["error"]["code"] == "quota_exceeded"
    assert reponse.data["error"]["details"]["limit"] == 50


def test_un_lot_de_quantite_non_entiere_refuse_avant_500(biz_api):
    auth_api = biz_api
    expire = (timezone.now() + timedelta(days=2)).isoformat()
    assert (
        auth_api.post(
            "/api/v1/promo/codes/generer/", {"quantite": "beaucoup", "expire_le": expire}, format="json"
        ).status_code
        == 400
    )
    assert (
        auth_api.post("/api/v1/promo/codes/generer/", {"quantite": 0, "expire_le": expire}, format="json").status_code
        == 400
    )
    assert (
        auth_api.post("/api/v1/promo/codes/generer/", {"quantite": 501, "expire_le": expire}, format="json").status_code
        == 400
    )
    # Sans date, pas de lot : « sans fin » n'est pas une option du produit.
    assert auth_api.post("/api/v1/promo/codes/generer/", {"quantite": 3}, format="json").status_code == 400


def test_verifier_un_code_est_lecture_seule(auth_api, dynamic_qr):
    """Le comptoir peut controler un code sans en consommer un : sinon une requete de test brûle l'offre."""
    offre = _offre(dynamic_qr, expire_dans=3, usages_max=1)
    offre.save()
    reponse = auth_api.post("/api/v1/promo/codes/verifier/", {"code": offre.code}, format="json")
    assert reponse.status_code == 200, reponse.data
    assert reponse.data["valide"] is True
    offre.refresh_from_db()
    assert offre.usages == 0
    from apps.qr import promo

    assert promo.compteurs(offre.pk).get(offre.pk, 0) == 0
    # Un code qui n'est pas a ce compte repond « inconnu », pas « inexistant » : pas de sonde.
    autre = auth_api.post("/api/v1/promo/codes/verifier/", {"code": "PROMO-INCONNU-ZZZZ"}, format="json")
    assert autre.status_code == 200 and autre.data["valide"] is False and autre.data["motif"] == "inconnu"


def test_prolonger_une_offre(auth_api, dynamic_qr):
    from datetime import timedelta as td

    offre = _offre(dynamic_qr, expire_dans=-2)  # deja morte
    offre.save()
    avant = offre.expire_le
    reponse = auth_api.post(f"/api/v1/promo/codes/{offre.pk}/prolonger/", {"jours": 7}, format="json")
    assert reponse.status_code == 200, reponse.data
    offre.refresh_from_db()
    assert offre.expire_le > timezone.now()
    assert (offre.expire_le - avant) >= td(days=6)
    assert offre.statut().valide is True
    assert auth_api.post(f"/api/v1/promo/codes/{offre.pk}/prolonger/", {"jours": 400}, format="json").status_code == 400


# ------------------------------------------------------------------ chemin du scan
def test_un_scan_pendant_la_campagne_emporte_le_code(dynamic_qr):
    offre = _offre(dynamic_qr, expire_dans=3)
    offre.save()
    reponse = _scan(dynamic_qr.slug, query=f"?promo={offre.code}")
    assert reponse.status_code == 302, reponse.content[:400]
    assert reponse["Location"] == "https://kamcofarm.com/boutique?promo=PROMO-CAVE-K7QF"


def test_un_scan_sans_code_applique_l_offre_du_flyer(dynamic_qr):
    """Le flyer imprimé ne porte qu'un lien court : l'offre attachée au QR s'applique quand même."""
    offre = _offre(dynamic_qr, expire_dans=3)
    offre.save()
    reponse = _scan(dynamic_qr.slug)
    assert reponse.status_code == 302
    assert "promo=PROMO-CAVE-K7QF" in reponse["Location"]


def test_un_scan_apres_la_date_affiche_un_avis_et_ne_redirige_pas(dynamic_qr):
    offre = _offre(dynamic_qr, expire_dans=3)
    offre.expire_le = timezone.now() - timedelta(hours=1)
    offre.save()
    reponse = _scan(dynamic_qr.slug, query=f"?promo={offre.code}")
    assert reponse.status_code == 200, reponse.content[:400]
    contenu = reponse.content.decode()
    assert "Cette offre est terminée" in contenu
    assert offre.code in contenu
    assert reponse["X-Promo"] == "expire"
    assert "Location" not in reponse  # pas de 302 vers la page normale : la remise n'existe plus


def test_un_scan_sur_une_offer_epuisee_dit_epuise(dynamic_qr):
    from apps.qr import promo

    offre = _offre(dynamic_qr, expire_dans=3, usages_max=1)
    offre.save()
    assert _scan(dynamic_qr.slug, query=f"?promo={offre.code}").status_code == 302
    assert promo.compteurs(offre.pk)[offre.pk] == 1
    reponse = _scan(dynamic_qr.slug, query=f"?promo={offre.code}")
    assert reponse.status_code == 200
    assert reponse["X-Promo"] == "epuise"
    assert "utilisée" in reponse.content.decode()  # le message du motif `epuise`, pas « terminée »


def test_un_code_inconnu_ne_bloque_pas_le_scan(dynamic_qr):
    """Un QR reste un QR : un paramètre `promo` qui ne correspond à rien doit passer comme une visite normale."""
    _offre(dynamic_qr, expire_dans=3).save()
    reponse = _scan(dynamic_qr.slug, query="?promo=PROMO-INCONNU-ZZZZ")
    assert reponse.status_code == 302
    assert reponse["Location"] == "https://kamcofarm.com/boutique"
    assert "X-Promo" not in reponse


def test_le_chemin_chaud_ne_touche_toujours_pas_la_base_avec_une_offre(dynamic_qr):
    """La garantie de perf tient avec une campagne : la validité se juge sur l'entrée de cache.

    Sans cette assertion, la facon la plus naturelle d'implémenter l'offre (un `JOIN` par scan) ferait
    passer le p99 de 15 ms a une requête de plus par visite — et rien ne le verrait dans les autres tests.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    _offre(dynamic_qr, expire_dans=3, usages_max=500).save()
    _scan(dynamic_qr.slug)
    with CaptureQueriesContext(connection) as capture:
        reponse = _scan(dynamic_qr.slug)
    assert reponse.status_code == 302
    assert reponse["X-Redirect-Cache"] == "hit"
    assert len(capture.captured_queries) == 0, capture.captured_queries


def test_l_entree_de_cache_porte_loffre(dynamic_qr, django_user_model):
    from apps.qr import cache as qr_cache

    offre = _offre(dynamic_qr, expire_dans=3, usages_max=9)
    offre.save()
    entree = qr_cache.serialize_for_cache(offre.qr)
    assert entree["promo"]["code"] == offre.code
    assert entree["promo"]["usages_max"] == 9
    assert entree["promo"]["expire_ts"] == int(offre.expire_le.timestamp())
    assert entree["codes_promo"][offre.code]["remise_type"] == "pourcentage"
    assert entree["codes_promo"][offre.code]["remise_valeur"] == "15.00"
    # Un QR sans offre a les deux clés vides : la redirection ne traite pas un cas particulier.
    vide = qr_cache.serialize_for_cache(_qr_sans_offre(django_user_model))
    assert vide["promo"] is None and vide["codes_promo"] == {}


def _qr_sans_offre(django_user_model):
    from apps.qr.models import QrCode

    compte = django_user_model.objects.create_user(
        email="nu@exemple.com", password="un-mot-de-passe-1", plan="business"
    )
    return QrCode.objects.create(owner=compte, kind="static", type_id="text", payload="https://kamcofarm.com/nu")


def test_creer_une_offre_invalide_le_cache_deja_en_place(dynamic_qr):
    """Le scan a deja eu lieu (entrée en cache sans offre) : créer l'offre doit la rendre visible tout de suite."""
    premier = _scan(dynamic_qr.slug)
    assert premier.status_code == 302 and "promo=" not in premier["Location"]
    offre = _offre(dynamic_qr, expire_dans=3)
    offre.save()  # `touch_qr_cache()` est dans `save()` — sans lui, il faudrait attendre l'expiration du TTL
    second = _scan(dynamic_qr.slug)
    assert "promo=PROMO-CAVE-K7QF" in second["Location"]


def test_supprimer_une_offre_delivre_le_qr(dynamic_qr):
    offre = _offre(dynamic_qr, expire_dans=3)
    offre.save()
    offre.expire_le = timezone.now() - timedelta(days=1)
    offre.save()
    assert _scan(dynamic_qr.slug).status_code == 200
    offre.delete()
    reponse = _scan(dynamic_qr.slug)
    assert reponse.status_code == 302
    assert reponse["Location"] == "https://kamcofarm.com/boutique"


# ------------------------------------------------------------------ resserrage des compteurs
def test_sync_promo_usages_resserre_redis_dans_la_base(dynamic_qr, capsys):
    from django.core.management import call_command

    from apps.qr import promo
    from apps.qr.models import PromoCode

    offre = _offre(dynamic_qr, expire_dans=3)
    offre.save()
    for _ in range(3):
        promo.marquer_utilise(offre.pk)
    assert PromoCode.objects.get(pk=offre.pk).usages == 0
    call_command("sync_promo_usages")
    resseree = PromoCode.objects.get(pk=offre.pk)
    assert resseree.usages == 3
    assert promo.compteurs(offre.pk)[offre.pk] == 0  # compteur remis a zero : pas de double comptage
    # Idempotent : relancer ne recopie pas deux fois.
    call_command("sync_promo_usages")
    assert PromoCode.objects.get(pk=offre.pk).usages == 3


def test_sync_promo_usages_ne_rien_inventer_sur_une_offre_sans_qr(dynamic_qr, capsys):
    from django.core.management import call_command

    from apps.qr.models import PromoCode

    orpheline = PromoCode.objects.create(
        owner=dynamic_qr.owner,
        code="PROMO-SANSQR-K7QF",
        expire_le=timezone.now() + timedelta(days=3),
        qr=None,
        remise_type="acces",
    )
    call_command("sync_promo_usages")
    assert PromoCode.objects.get(pk=orpheline.pk).usages == 0
