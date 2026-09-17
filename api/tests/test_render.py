"""Rendu serveur des images — le critère d'acceptation du sprint 2 se joue ici."""

import struct

import pytest

pytestmark = pytest.mark.django_db


def _image(auth_api, qr, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return auth_api.get(f"/api/v1/qr/{qr.pk}/image/?{query}")


def test_png_reel_et_dimensions_reservees(auth_api, dynamic_qr):
    response = _image(auth_api, dynamic_qr, size=320)
    assert response.status_code == 200, response.data
    assert response["Content-Type"] == "image/png"
    corps = response.content
    assert corps[:8] == b"\x89PNG\r\n\x1a\n", "ce n'est pas un PNG"
    # IHDR: largeur/hauteur aux octets 16..24 (big-endian). Verifier la taille reelle du fichier, pas
    # seulement le parametre recu: le rendu arondit a la taille de module pres, et c'est voulu.
    largeuur, hauteur = struct.unpack(">II", corps[16:24])
    assert largeuur == hauteur
    assert 200 <= largeuur <= 340, f"tailre hors borne demandee: {largeuur}"
    assert 'filename="qr-' in response["Content-Disposition"]


def test_svg_authentique_et_non_un_png_renomme(auth_api, dynamic_qr):
    response = _image(auth_api, dynamic_qr, fmt="svg")
    assert response.status_code == 200
    assert response["Content-Type"] == "image/svg+xml"
    assert response.content.lstrip()[:5] in (b"<?xml", b"<svg ")
    assert b"<svg" in response.content


def test_l_image_d_un_qr_dynamic_ne_change_pas_quand_la_destination_change(
    auth_api, dynamic_qr, django_capture_on_commit_callbacks
):
    """Le coeur du produit: le flyer imprime reste valide, donc son image ne doit pas bouger d'un octet.

    Si un jour le slug entrait dans la cle de rendu *et* dans le contenu, cette assertion casserait —
    c'est le test qui empeche la régression « mes flyers imprimés ne marchent plus ».
    """
    avant = _image(auth_api, dynamic_qr, size=256).content
    with django_capture_on_commit_callbacks(execute=True):
        auth_api.patch(f"/api/v1/qr/{dynamic_qr.pk}/", {"target_url": "https://autre.example/campagne"}, format="json")
    apres = _image(auth_api, dynamic_qr, size=256).content
    assert avant == apres


def test_le_QR_imprime_suivant_la_nouvelle_destination_en_moins_d_une_seconde(
    auth_api, api, dynamic_qr, django_capture_on_commit_callbacks
):
    """Le critère d'acceptation du sprint 2, mesuré et non déclaré.

    On chronomètre le premier scan *après* la modification, jusqu'à ce que le 302 porte la nouvelle
    URL. Le budget de 1 s couvre le TTL du cache: si l'invalidation `on_commit` sautait une étape, ce
    test deviendrait rouge sur le délai, pas sur le statut.
    """
    import time

    from django.db import connection
    from django.test import Client

    with django_capture_on_commit_callbacks(execute=True):
        reponse = auth_api.patch(
            f"/api/v1/qr/{dynamic_qr.pk}/", {"target_url": "https://nouvelle.example/x"}, format="json"
        )
    assert reponse.status_code == 200
    client = Client()
    debut = time.perf_counter()
    location = None
    while time.perf_counter() - debut < 1.0:
        scan = client.get(f"/r/{dynamic_qr.slug}")
        location = scan.get("Location")
        if location == "https://nouvelle.example/x":
            break
    delai = time.perf_counter() - debut
    assert location == "https://nouvelle.example/x", f"destination non propagée: {location}"
    assert delai < 1.0, f"propagation en {delai:.2f} s"
    connection.close() if False else None  # (rien à fermer: la connexion suit le test)


def test_parametres_hors_bornes_refuses_sans_toucher_l_image(auth_api, dynamic_qr):
    for params, code in [
        ({"size": "8"}, "invalid_size"),
        ({"size": "99999"}, "invalid_size"),
        ({"size": "abc"}, "invalid_size"),
        ({"fmt": "tiff"}, "invalid_format"),
    ]:
        response = _image(auth_api, dynamic_qr, **params)
        assert response.status_code == 400, params
        assert response.data["error"]["code"] == code, response.data


def test_couleur_validee_avant_le_rendu(auth_api, user):
    """Un `dark` non parseable partirait dans Pillow et finirait en 500: on répond 400 avec le format attendu."""
    from apps.qr.models import QrCode

    qr = QrCode.objects.create(owner=user, kind="static", type_id="text", payload="bonjour", design={"dark": "rouge"})
    response = _image(auth_api, qr, size=256)
    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_color"


def test_ecc_h_reussit_et_l_sur_un_contenu_long(user):
    """Le studio relèvede la correction en H quand un logo est présent: le rendu serveur doit suivre."""
    from apps.qr import render
    from apps.qr.models import QrCode

    contenu = "A" * 1200
    qr = QrCode.objects.create(owner=user, kind="static", type_id="text", payload=contenu)
    octets, mime, _ = render.rendu(qr, fmt="png", size=512, design={"ecc": "H"})
    assert mime == "image/png" and octets[:4] == b"\x89PNG"
    with pytest.raises(Exception) as exc:
        render.ECC["Z"]  # un niveau inventé ne doit pas exister silencieusement
    assert isinstance(exc.value, KeyError)


def test_cle_de_cache_derivee_du_contenu_pas_de_l_identifiant():
    from apps.qr.render import cache_key

    a = cache_key(contenu="https://x/r/abc", fmt="png", size=512, ecc="M", margin=2, dark="#000000", light="#ffffff")
    b = cache_key(contenu="https://x/r/abc", fmt="png", size=512, ecc="M", margin=2, dark="#000000", light="#ffffff")
    c = cache_key(contenu="https://x/r/zzz", fmt="png", size=512, ecc="M", margin=2, dark="#000000", light="#ffffff")
    assert a == b and a != c
    assert a.startswith("qrs:qrimg:")


def test_un_qr_statique_vide_ne_fabrique_pas_une_image(auth_api, user):
    from apps.qr.models import QrCode

    qr = QrCode.objects.create(owner=user, kind="static", type_id="text", payload="")
    response = _image(auth_api, qr, size=256)
    assert response.status_code == 400
    assert response.data["error"]["code"] == "empty_payload"


def test_le_rendu_d_un_qr_d_un_tier_est_absent(api, other_user, dynamic_qr):
    api.force_login(other_user)
    assert api.get(f"/api/v1/qr/{dynamic_qr.pk}/image/").status_code == 404


def test_export_pdf_reserve_au_palier_standard(auth_api, dynamic_qr):
    """`export_pdf` est le premier element facture en Standard dans votre grille — c'est donc le seul
    des trois formats qui doit fermer une porte. PNG et SVG restent gratuits : sans eux, le QR
    telechargeable du palier gratuit n'existerait plus."""
    response = _image(auth_api, dynamic_qr, fmt="pdf", size=300)
    assert response.status_code == 200, response.data
    assert response["Content-Type"] == "application/pdf"
    assert response.content[:5] == b"%PDF-"

    # QR **different**, possede par le compte gratuit : reutiliser le QR du fixture premium renverrait
    # un 404 (le garde de propriete), et le test croirait voir un verrou de facturation la ou il n'y
    # a qu'une cloison d'acces.
    from apps.accounts.models import User
    from apps.qr.models import QrCode

    libre = User.objects.create_user(email="gratuit@exemple.com", password="un-mot-de-passe-solide-7", plan="free")
    qr_gratuit = QrCode.objects.create(owner=libre, kind="static", type_id="text", payload="bonjour")
    # Client neuf : le fixture `auth_api` repose sur `force_authenticate(user)`, qui **precede** la
    # session sur le meme objet — y coller `force_login(libre)` aurait garde l'utilisateur premium et
    # le test aurait lu un 404 de propriete pour un 402 de facturation.
    from rest_framework.test import APIClient

    client_gratuit = APIClient()
    client_gratuit.force_login(libre)
    refuse = _image(client_gratuit, qr_gratuit, fmt="pdf", size=300)
    assert refuse.status_code == 402, refuse.json()
    details = refuse.json()["error"]["details"]
    assert details["caracteristique"] == "export_pdf"
    assert details["palier_requis"] == "standard"
