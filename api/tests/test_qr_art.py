"""QR artistiques et animés : le produit ne vaut que si l'image se relit **sur ce serveur**.

Ces tests sont la raison d'être de `apps.qr.art` : un beau PNG qui ne décode pas n'est pas un QR, c'est
une affiche. Chaque assertion porte donc sur la preuve (relecture zxing/OpenCV), pas sur l'esthétique.
"""

import io
import json

import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _verificateur_de_reference() -> None:
    """Ces tests affirment une preuve, pas une approximation : sans zxing-cpp, elle est impossible.

    Le repli OpenCV refuse les QR encadrés (mesuré : il décroche dès qu'un liseré clair entoure l'image),
    donc sur un poste où la dépendance est manquante on obtiendrait un 409 `art_ilisible` qui ressemble à
    une régression du rendu. Le message d'échec dit plutôt quoi lancer ; `requirements.txt` installe
    `zxing-cpp`, en CI comme ici.
    """
    try:
        import zxingcpp  # noqa: F401
    except ImportError as exc:
        pytest.fail(
            "zxing-cpp absent de l'environnement : installer `python3 -m pip install -r requirements.txt` "
            "(dans ce bac à sable : `python3 scripts/repair_vendor.py --avec-rendu`). "
            "Ne pas retirer ces tests pour autant — c'est la seule chose qui prouve la scannabilité.",
        )


def _rendu(auth_api, qr, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return auth_api.get(f"/api/v1/qr/{qr.pk}/rendu/?{query}")


def _score(reponse) -> dict:
    return json.loads(reponse["X-Score"])


def _montage(reponse) -> dict:
    return json.loads(reponse["X-Montage"])


# ------------------------------------------------------------------ styles
def test_la_liste_des_styles_vient_du_serveur_et_porte_le_contraste(api, auth_api):
    """Le front affiche ce que l'API dit : aucune table de styles dupliquée côté client."""
    reponse = auth_api.get("/api/v1/qr/styles/")
    assert reponse.status_code == 200, reponse.data
    assert {s["code"] for s in reponse.data["styles"]} == {"encre", "naples", "halles", "nuit", "vitrine"}
    for style in reponse.data["styles"]:
        # 4.5 = le seuil WCAG des grands textes. En dessous, un QR imprimé sur un menu plié ne se scanne
        # plus à la lumière du soir : la contrainte est dans la table, pas dans un commentaire.
        assert style["contraste"] >= 4.5, style
    assert reponse.data["logo_surface_max"] == pytest.approx(0.10)
    # Une table de constantes n'a rien d'authentifié : le studio la lit avant meme de se connecter.
    assert api.get("/api/v1/qr/styles/").status_code == 200


@pytest.mark.parametrize("style", ["encre", "naples", "halles", "nuit", "vitrine"])
def test_chaque_style_est_redcode_apres_rendu(auth_api, dynamic_qr, style):
    """L'assertion centrale : ce que le serveur a servi, il vient de le relire et d'y retrouver le contenu."""
    reponse = _rendu(auth_api, dynamic_qr, fmt="art", style=style, size=512)
    assert reponse.status_code == 200, reponse.data
    assert reponse["Content-Type"] == "image/png"
    assert reponse.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert reponse["X-Lisibilite"] == "verifiee"
    score = _score(reponse)
    assert score["decode"] is True and score["contenu_attendu"] is True
    assert score["style"] == style
    assert score["epreuve"].startswith("zxing")  # le décodeur de référence, mesuré comme le plus fiable
    assert score["module_px"] >= 6, score  # sous 6 px/module, un téléphone d'entrée de gamme décroche
    assert score["repli"] is False  # aucun de nos quatre styles n'a besoin de se rabattre sur `encre`


def test_un_style_inconnu_est_refuse_avec_la_liste_des_bons(auth_api, dynamic_qr):
    reponse = _rendu(auth_api, dynamic_qr, fmt="art", style="baroque")
    assert reponse.status_code == 400, reponse.data
    assert reponse.data["error"]["code"] == "style_inconnu"
    assert set(reponse.data["error"]["details"]["supportes"]) == {"encre", "naples", "halles", "nuit", "vitrine"}


def test_un_format_non_dessine_par_rendu_est_refuse(auth_api, dynamic_qr):
    """`/rendu/` ne prend que `art` et `gif` (plus les formats de base, délégués) : `webp` n'existe pas."""
    reponse = _rendu(auth_api, dynamic_qr, fmt="webp")
    assert reponse.status_code == 400
    assert reponse.data["error"]["code"] == "invalid_format"
    assert set(reponse.data["error"]["details"]["supportes"]) == {"art", "gif", "pdf", "png", "svg"}


def test_le_png_de_base_reste_accessible_par_les_deux_urls(auth_api, dynamic_qr):
    """/image/ et /rendu/?fmt=png dessinent la meme chose : une seule implementation, pas deux politiques."""
    par_image = auth_api.get(f"/api/v1/qr/{dynamic_qr.pk}/image/?size=320")
    par_rendu = _rendu(auth_api, dynamic_qr, fmt="png", size=320)
    assert par_image.status_code == par_rendu.status_code == 200
    assert par_image.content == par_rendu.content
    assert par_image["Content-Type"] == "image/png"


# ------------------------------------------------------------------ cache
def test_le_cache_est_par_style_et_par_contenu(auth_api, dynamic_qr, monkeypatch):
    """Deux requêtes identiques = un seul dessin ; deux styles = deux dessins.

    Compter les appels à `art.rendre` plutôt que comparer les octets : ce qu'on garantit ici, c'est le coût
    CPU, et un cache qui confondrait deux styles resterait invisible à l'œil.
    """
    from apps.qr import art

    appels = []
    reel = art.rendre

    def sonde(qr, **kwargs):
        appels.append(kwargs.get("style"))
        return reel(qr, **kwargs)

    monkeypatch.setattr(art, "rendre", sonde)
    assert _rendu(auth_api, dynamic_qr, fmt="art", style="naples").status_code == 200
    assert _rendu(auth_api, dynamic_qr, fmt="art", style="naples").status_code == 200
    assert appels == ["naples"], appels
    assert _rendu(auth_api, dynamic_qr, fmt="art", style="halles").status_code == 200
    assert appels == ["naples", "halles"], appels
    # Un GIF du même QR ne doit pas lire l'entrée du PNG.
    assert _rendu(auth_api, dynamic_qr, fmt="gif", frames=8).status_code == 200
    assert appels == ["naples", "halles"], "le GIF a été servi depuis l'entrée du PNG"


def test_la_cle_de_cache_porte_chaque_reglage(django_user_model):
    """Un paramètre oublié dans la clé = un client servi avec l'image d'un autre. Test explicite."""
    from apps.qr import rendus
    from apps.qr.models import QrCode

    texte = "https://kamcofarm.com/menu"
    un = QrCode.objects.create(
        owner=django_user_model.objects.create_user(email="un@exemple.com", password="un-mot-de-passe-1"),
        kind="static",
        type_id="url",
        payload=texte,
    )
    base = rendus.cle_cache(un, fmt="art", taille=512, style="encre")
    assert base == rendus.cle_cache(un, fmt="art", taille=512, style="encre")
    for nom, valeur in [("taille", 640), ("style", "halles"), ("fmt", "gif"), ("logo", "abc"), ("frames", 24)]:
        autres = {"fmt": "art", "taille": 512, "style": "encre", "logo": "", "frames": 8}
        autres[nom] = valeur
        assert base != rendus.cle_cache(un, **autres), f"`{nom}` n'entre pas dans la clé de cache"


# ------------------------------------------------------------------ logo
def test_le_logo_est_accepte_et_reste_sous_le_seuil_de_surface(auth_api, dynamic_qr):
    """Le logo est posé sur le code, borné en surface, et la preuve de lisibilité est faite **avec** lui."""
    reponse = auth_api.post(
        f"/api/v1/qr/{dynamic_qr.pk}/rendu/",
        {"fmt": "art", "style": "naples", "size": "512", "logo": _logo(64, (30, 90, 200))},
        format="multipart",
    )
    assert reponse.status_code == 200, reponse.data
    score = _score(reponse)
    # 0.10 : au-delà, le vérifieur de référence décroche (mesures à 512 et 1024 px, contenu de 900 octets).
    # Le « 20 % » des générateurs en ligne est une habitude, pas une garantie de lecture.
    assert 0 < score["couverture_logo"] <= 0.10, score
    assert score["decode"] is True and score["contenu_attendu"] is True


def test_logo_trop_lourd_refuse_avant_de_dessiner(auth_api, dynamic_qr):
    from django.core.files.uploadedfile import SimpleUploadedFile

    reponse = auth_api.post(
        f"/api/v1/qr/{dynamic_qr.pk}/rendu/",
        {"fmt": "art", "logo": SimpleUploadedFile("gros.png", b"\x89PNG\r\n\x1a\n" + b"0" * (300 * 1024), "image/png")},
        format="multipart",
    )
    assert reponse.status_code == 413, reponse.data
    assert reponse.data["error"]["code"] == "logo_trop_lourd"


def test_logo_qui_n_est_pas_une_image_refuse(auth_api, dynamic_qr):
    from django.core.files.uploadedfile import SimpleUploadedFile

    reponse = auth_api.post(
        f"/api/v1/qr/{dynamic_qr.pk}/rendu/",
        {"fmt": "art", "logo": SimpleUploadedFile("script.png", b"# pas une image", "image/png")},
        format="multipart",
    )
    assert reponse.status_code == 400, reponse.data
    assert reponse.data["error"]["code"] == "logo_ilisible"


def test_logo_de_format_non_attendu_refuse(auth_api, dynamic_qr):
    from django.core.files.uploadedfile import SimpleUploadedFile

    reponse = auth_api.post(
        f"/api/v1/qr/{dynamic_qr.pk}/rendu/",
        {"fmt": "art", "logo": SimpleUploadedFile("logo.svg", b"<svg/>", "image/svg+xml")},
        format="multipart",
    )
    assert reponse.status_code == 400
    assert reponse.data["error"]["code"] == "logo_format_invalide"


# ------------------------------------------------------------------ animation
@pytest.mark.parametrize("cote", [5000, 65535])
def test_un_png_qui_se_declare_enorme_est_refuse_avant_decodage(auth_api, dynamic_qr, cote):
    """Bombe de décompression : 45 octets sur le fil, des gigaoctets en mémoire si on laisse `load()` décider.

    Le poids du fichier est déjà borné à 256 Ko, ce qui ne dit rien de sa **surface**. Deux couches sont
    vérifiées ici : notre seuil à 16 Mpx (5000²) et le refus de Pillow au-delà de ~178 Mpx (65535²), que
    l'on traduit dans notre code d'erreur — sinon l'utilisateur lirait « ce n'est pas une image lisible »
    sur un PNG parfaitement valide. Dans les deux cas l'allocation n'a pas eu lieu.
    """
    import struct
    import zlib

    from django.core.files.uploadedfile import SimpleUploadedFile

    def _bloc(type_: bytes, donnees: bytes) -> bytes:
        return struct.pack(">I", len(donnees)) + type_ + donnees + struct.pack(">I", zlib.crc32(type_ + donnees))

    ihdr = struct.pack(">IIBBBBB", cote, cote, 8, 6, 0, 0, 0)  # 8 bits, RGBA, rien d'autre
    png = b"\x89PNG\r\n\x1a\n" + _bloc(b"IHDR", ihdr) + _bloc(b"IEND", b"")
    assert len(png) < 128, "le fichier doit rester sous la limite de poids, sinon ce test prouve autre chose"

    reponse = auth_api.post(
        f"/api/v1/qr/{dynamic_qr.pk}/rendu/",
        {"fmt": "art", "logo": SimpleUploadedFile("geant.png", png, "image/png")},
        format="multipart",
    )
    assert reponse.status_code == 400, reponse.data
    assert reponse.data["error"]["code"] == "logo_trop_grand"
    if cote == 5000:  # notre seuil : la surface est connue, on peut la citer
        assert reponse.data["error"]["details"]["pixels"] == cote * cote


def test_gif_anime_garde_ses_frames_statiques(auth_api, dynamic_qr):
    reponse = _rendu(auth_api, dynamic_qr, fmt="gif", style="encre", size=512, frames=24, liser=24)
    assert reponse.status_code == 200, reponse.data
    assert reponse["Content-Type"] == "image/gif"
    assert reponse.content[:6] in (b"GIF87a", b"GIF89a")
    montage = _montage(reponse)
    assert montage["verifiee"] is True and montage["contenu_attendu"] is True
    assert montage["frames"] == 24
    assert montage["part_statique"] >= 0.60, montage
    assert montage["frames_statiques"] == 15  # ceil(24 × 0,60) : arrondi vers le haut, jamais `round`
    assert montage["duree_totale_ms"] == 24 * 90


def test_le_lisere_agrandit_la_toile_sans_toucher_au_code(auth_api, dynamic_qr):
    """Liseré 0 px contre 64 px : +128 px de côté, et le contenu relu reste le même."""
    mini = _rendu(auth_api, dynamic_qr, fmt="gif", liser=0, frames=8, size=320)
    maxi = _rendu(auth_api, dynamic_qr, fmt="gif", liser=64, frames=8, size=320)
    assert mini.status_code == maxi.status_code == 200
    petit, grand = _montage(mini), _montage(maxi)
    assert grand["taille_px"] - petit["taille_px"] == 128
    assert petit["verifiee"] is True and grand["verifiee"] is True
    # L'invariant, pas un nombre recopie : la toile du GIF est exactement celle du rendu fixe **plus** le
    # liseré. Si l'animation recadrait ou redessinait le code, cette égalité sauterait en premier.
    fixe = _rendu(auth_api, dynamic_qr, fmt="art", style="encre", size=320)
    assert grand["taille_px"] == _score(fixe)["taille_px"] + 2 * 64


def test_des_frames_hors_bornes_sont_refusees_pas_corrigees(auth_api, dynamic_qr):
    """Le slider du front est borné ; l'API refuse ce qui passe outre, sans inventer un GIF tronqué."""
    reponse = _rendu(auth_api, dynamic_qr, fmt="gif", frames=5000)
    assert reponse.status_code == 400, reponse.data
    assert reponse.data["error"]["code"] == "parametre_invalide"
    assert "frames" in reponse.data["error"]["message"]


def test_un_gif_trop_lourd_est_refuse_plutot_que_tronque(auth_api, dynamic_qr, monkeypatch):
    """Le plafond de 4 Mo n'est pas atteint aux réglages maxima (mesure : 913 Ko à 1024 px / 48 frames) :
    on abaisse le plafond pour prouver que la garde tient. Un GIF tronqué serait un QR cassé, et la
    garantie « vérifié » ne doit jamais porter sur des octets qu'on a raccourcis.
    """
    from apps.qr import animation

    monkeypatch.setattr(animation, "TROP_LOURD", 20 * 1024)
    reponse = _rendu(auth_api, dynamic_qr, fmt="gif", frames=24, size=512)
    assert reponse.status_code == 413, reponse.data
    assert reponse.data["error"]["code"] == "animation_trop_lourde"
    assert reponse.data["error"]["details"]["octets"] > 20 * 1024


# ------------------------------------------------------------------ portes payantes
@pytest.mark.parametrize("fmt", ["art", "gif"])
def test_art_et_animation_sont_reservees_aux_paliers_superieurs(django_user_model, api, fmt):
    """Gratuit et Standard reçoivent un 402 qui nomme Premium et son prix ; Premium passe."""
    from apps.qr.models import QrCode

    gratuit = django_user_model.objects.create_user(
        email="gratuit@exemple.com", password="un-mot-de-passe-1", plan="free"
    )
    api.force_authenticate(user=gratuit)
    ligne = QrCode.objects.create(owner=gratuit, kind="static", type_id="text", payload="bonjour")
    reponse = api.get(f"/api/v1/qr/{ligne.pk}/rendu/?fmt={fmt}")
    assert reponse.status_code == 402, reponse.data
    erreur = reponse.data["error"]
    assert erreur["code"] == "plan_required"
    assert erreur["details"]["palier_requis"] == "premium"
    # Le prix vient de la grille, format europeen inclus : un 402 qui annonce un tarif invente est
    # une promesse commerciale fausse, pas une nuance de libelle.
    assert "Premium" in erreur["message"] and "8,99" in erreur["message"]
    assert erreur["details"]["plan_actuel"] == "free"

    standard = django_user_model.objects.create_user(
        email="std@exemple.com", password="un-mot-de-passe-1", plan="standard"
    )
    QrCode.objects.create(owner=standard, kind="static", type_id="text", payload="bonjour")
    api.force_authenticate(user=standard)
    assert api.get(f"/api/v1/qr/{standard.qrcodes.first().pk}/rendu/?fmt={fmt}").status_code == 402

    premium = django_user_model.objects.create_user(
        email="prem@exemple.com", password="un-mot-de-passe-1", plan="premium"
    )
    ligne = QrCode.objects.create(owner=premium, kind="static", type_id="text", payload="bonjour")
    api.force_authenticate(user=premium)
    assert api.get(f"/api/v1/qr/{ligne.pk}/rendu/?fmt={fmt}").status_code == 200


def test_un_qr_d_un_autre_reste_invisible_meme_pour_un_rendu(auth_api, other_user):
    """La règle du 404 (et non 403) vaut aussi pour les rendus : pas de sonde d'existence."""
    from apps.qr.models import QrCode

    ligne = QrCode.objects.create(owner=other_user, kind="static", type_id="text", payload="privé")
    assert _rendu(auth_api, ligne, fmt="art").status_code == 404


def test_non_connecte_ne_dessine_rien(api, dynamic_qr):
    assert api.get(f"/api/v1/qr/{dynamic_qr.pk}/rendu/?fmt=art").status_code in {401, 403}


# ------------------------------------------------------------------ vérifieur absent
def test_sans_verifieur_le_format_est_refuse_et_non_servi_les_yeux_fermes(monkeypatch, dynamic_qr):
    """Aucun décodeur installé = 503 explicite, jamais un envoi sans preuve."""
    from apps.common.exceptions import ApiError
    from apps.qr import art

    monkeypatch.setattr(art, "_detecteurs", lambda: [])
    with pytest.raises(ApiError) as levee:
        art.rendre(dynamic_qr, contenu=dynamic_qr.short_url, style="naples", taille=512)
    assert levee.value.status_code == 503
    assert levee.value.code == "art_verifieur_absent"


def test_un_contenu_dense_tient_a_1024_px(dynamic_qr):
    """Le cas qui casse les générateurs en ligne : 900 octets encodés, motif dense, module minimal."""
    from apps.qr import art

    octets, _mime, score = art.rendre(
        dynamic_qr, contenu="https://kamcofarm.com/" + "x" * 900, style="halles", taille=1024
    )
    assert score.module_px >= 6
    assert score.decode is True
    assert len(octets) > 1000


def test_l_ecc_releve_quand_un_logo_couvre_le_code(dynamic_qr):
    """Le studio relève la correction en H dès qu'un logo est présent ; le rendu serveur suit la promesse."""
    from apps.qr import art

    _octets, _mime, sans = art.rendre(dynamic_qr, contenu=dynamic_qr.short_url, style="encre", taille=512)
    _octets, _mime, avec = art.rendre(
        dynamic_qr,
        contenu=dynamic_qr.short_url,
        style="encre",
        taille=512,
        logo=_png(120, (10, 10, 10)),  # des octets : c'est `rendus.logo_de` qui fabrique le fichier
    )
    assert avec.decode is True and sans.decode is True
    assert avec.ecc in {"H", "Q"}


def _png(cote: int, couleur: tuple[int, int, int]) -> bytes:
    from PIL import Image

    image = Image.new("RGBA", (cote, cote), (*couleur, 255))
    tampon = io.BytesIO()
    image.save(tampon, format="PNG")
    return bytes(tampon.getvalue())


def _logo(cote: int, couleur: tuple[int, int, int]):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile("logo.png", _png(cote, couleur), "image/png")


# ------------------------------------------------------------------ aperçu sans QR enregistré
def _apercu(client, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(f"/api/v1/qr/apercu/?{query}")


def test_le_gratuit_peut_pas_regarder_l_art_meme_sans_enregistrer(api, django_user_model):
    """L'aperçu est soumis a la meme porte que le telechargement : sinon la capacite se goberge en PNG.

    Le gratuit a le droit de voir son QR en noir et blanc (c'est son palier), pas de faire tourner le
    composeur + le decodeur d'un palier payant a l'infini sous pretexte de « juste un apercu ».
    """
    compte = django_user_model.objects.create_user(
        email="curieux@exemple.com", password="un-mot-de-passe-1", plan="free"
    )
    api.force_authenticate(user=compte)
    reponse = _apercu(api, fmt="art", payload="https://kamcofarm.com/cave", style="naples")
    assert reponse.status_code == 402, reponse.data
    assert reponse.data["error"]["details"]["caracteristique"] == "qr_artistique_ia"


def test_apercu_verifie_un_contenu_non_enregistre(auth_api):
    reponse = _apercu(auth_api, fmt="art", payload="https://kamcofarm.com/cave?campagne=2", style="naples", size=384)
    assert reponse.status_code == 200, reponse.data
    assert reponse["Content-Type"] == "image/png"
    score = _score(reponse)
    assert score["decode"] is True and score["contenu_attendu"] is True
    assert score["taille_px"] >= 384


def test_apercu_anime_refuse_un_contenu_trop_long(auth_api):
    """La meme limite que la creation : un QR ne peut pas plus, apercu ou pas apercu."""
    from django.conf import settings

    limite = int((settings.QR or {}).get("MAX_PAYLOAD_BYTES", 2953))
    reponse = _apercu(auth_api, fmt="gif", payload="x" * (limite + 10))
    assert reponse.status_code == 400, reponse.data
    assert reponse.data["error"]["code"] == "payload_trop_long"
    assert reponse.data["error"]["details"]["max"] == limite


def test_apercu_exige_un_contenu(auth_api):
    assert _apercu(auth_api, fmt="art").status_code == 400
    assert _apercu(auth_api, fmt="png", payload="bonjour").data["error"]["code"] == "invalid_format"


def test_apercu_et_telechargement_partagent_le_cache_du_meme_contenu(auth_api, user, monkeypatch):
    """Un contenu identique a l'apercu et au telechargement ne doit etre dessine qu'une fois.

    Les deux chemins prennent `contenu` comme cle (pas `id`) : c'est ce qui rend l'apercu du studio
    gratuit en CPU quand le client telecharge dix fois le meme flyer.
    """
    from apps.qr import art

    appels = []
    reel = art.rendre

    def sonde(qr, **kwargs):
        appels.append(kwargs.get("style"))
        return reel(qr, **kwargs)

    monkeypatch.setattr(art, "rendre", sonde)
    contenu = "https://kamcofarm.com/partage"
    assert _apercu(auth_api, fmt="art", payload=contenu, style="nuit", size=320).status_code == 200
    from apps.qr.models import QrCode

    qr = QrCode.objects.create(owner=user, kind="static", type_id="url", payload=contenu)
    assert _rendu(auth_api, qr, fmt="art", style="nuit", size=320).status_code == 200
    assert appels == ["nuit"], appels


def test_un_logo_debordant_est_dimensionne_sans_fausser_le_score(dynamic_qr):
    """Un logo de 400 px sur une toile de 481 est **ramene** a la surface maximale, et le score le dit.

    Important : `logo_reduit`/`logo_retire` ne signalent pas cette mise a l'echelle (elle est systematique,
    c'est le dessin qui choisit son cote) — ils signalent un **repli apres echec de relecture**. Les
    confondre ferait croire au commercial que son logo a ete rogne alors qu'il est simplement cadre.
    """
    from apps.qr import art

    _octets, _mime, score = art.rendre(
        dynamic_qr, contenu=dynamic_qr.short_url, style="naples", taille=512, logo=_png(400, (20, 60, 160))
    )
    assert score.decode is True and score.contenu_attendu is True
    assert 0 < score.couverture_logo <= art.LOGO_SURFACE_MAX, score
    assert score.repli is False and score.logo_reduit is False and score.logo_retire is False


def test_lescalier_de_repli_sur_logo_est_parcouru_et_traverse(dynamic_qr, monkeypatch):
    """Quand la premiere tentative ne se relit pas, l'escalier reduit puis retire le logo — et le score porte la marque.

    L'echec de relecture est simule (le premier appel renvoie `False`) : ce qu'on verifie ici, c'est la
    **comptabilite** du chemin, pas la capacite du decodeur. Un client qui recoit son QR avec un logo plus
    petit que prevu doit pouvoir le lire dans la reponse, pas le deviner.
    """
    from apps.qr import art

    reelire = art.relire
    etat = {"n": 0}

    def premier_echec(octets, *, contenu):
        etat["n"] += 1
        if etat["n"] == 1:
            return False, False, 0, "", "simule"
        return reelire(octets, contenu=contenu)

    monkeypatch.setattr(art, "relire", premier_echec)
    _octets, _mime, score = art.rendre(
        dynamic_qr, contenu=dynamic_qr.short_url, style="naples", taille=512, logo=_png(200, (20, 60, 160))
    )
    assert score.decode is True, score
    assert score.logo_reduit is True, "la deuxieme marche de l'escalier a du etre consommee"
    assert etat["n"] >= 2
