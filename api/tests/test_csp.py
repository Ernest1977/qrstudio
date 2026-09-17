"""Politique de contenu : un nonce par requête, `script-src` strict, et un mode qui ne bloque rien par défaut.

Quatre étages d'assertion, dans l'ordre où ils peuvent mentir :

1. la **forme** de la politique — stricte sur ce qui exécute du code, permissive là où la permissivité est
   mesurée inoffensive (attributs `style=`) ;
2. le **nonce** — engendré une fois par requête, partagé par toutes les balises, jamais écrit en clair ;
3. le **bout du tuyau** — une vraie page rendue porte le même nonce dans son en-tête et dans son `<style>` ;
4. les **régressions d'amont** — si `django.contrib.admin` ou `django-allauth` introduit un jour un script
   en ligne exécutable, la politique stricte le casserait en production : mieux vaut le voir rougir ici.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.common import csp

pytestmark = pytest.mark.django_db

NONCE = "Ab1Cd2Ef3Gh4Ij5Kl6Mn7O"


@pytest.fixture(autouse=True)
def _second_facteur_admis(monkeypatch):
    """`/manage/` est derrière la garde TOTP du personnel ; on la lève pour tester le contenu des pages."""
    monkeypatch.setattr("apps.common.staff_access.has_totp", lambda user: True)


@pytest.fixture
def personnel(django_user_model):
    return django_user_model.objects.create_superuser(
        email="root-csp@kamcofarm.com", password="un-mot-de-passe-solide-42", plan="free"
    )


def _ligne(lignes: list[str], debut: str) -> str:
    return next(ligne for ligne in lignes if ligne.startswith(debut))


def _nonce_de_l_en_tete(reponse) -> str:
    entete = reponse.headers.get("Content-Security-Policy-Report-Only") or reponse.headers.get(
        "Content-Security-Policy"
    )
    assert entete, f"aucune politique publiée : {dict(reponse.headers)}"
    return re.search(r"nonce-([A-Za-z0-9_-]+)", entete).group(1)


# ------------------------------------------------------------------ 1. la forme de la politique
def test_script_src_est_strict_et_sans_unsafe_inline():
    script = _ligne(csp.directives(NONCE), "script-src")
    assert script == f"script-src 'self' 'nonce-{NONCE}'"
    assert "unsafe-inline" not in script


def test_les_blocs_style_exigent_le_nonce_et_les_attributs_resten_permis():
    """Un attribut `style=` ne peut pas exécuter de code ; un `<style>` le frôle, donc nonce."""
    assert _ligne(csp.directives(NONCE), "style-src ") == f"style-src 'self' 'nonce-{NONCE}'"
    assert "style-src-attr 'unsafe-inline'" in csp.directives(NONCE)


def test_le_cloisonnement_de_cadrage_et_de_base_est_posé():
    lignes = csp.directives(NONCE)
    for attendu in (
        "default-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "object-src 'none'",
        "form-action 'self'",
        "img-src 'self' data:",
    ):
        assert attendu in lignes, attendu


def test_la_montee_vers_https_ne_sort_que_si_le_tls_est_force():
    """Sur `http://localhost` du dev, `upgrade-insecure-requests` casserait les statiques servis en clair."""
    assert "upgrade-insecure-requests" not in csp.directives(NONCE, montee_insegure=False)
    assert "upgrade-insecure-requests" in csp.directives(NONCE, montee_insegure=True)


def test_l_echappement_documente_peut_elargir_script_src_sans_patcher_le_code():
    """`CSP_SCRIPT_SRC_EXTRA` est la soupape amont : un hash, pas un `unsafe-inline` qu'on oublie."""
    ligne = _ligne(csp.directives(NONCE, script_extra="'sha256-abcd='"), "script-src")
    assert f"'nonce-{NONCE}'" in ligne and ligne.endswith("'sha256-abcd='")


# ------------------------------------------------------------------ 2. le mode
def test_report_only_ne_bloque_pas_et_apply_si():
    entete, valeur = csp.politique(NONCE, mode="report-only")
    assert entete == "Content-Security-Policy-Report-Only"
    assert valeur.startswith("default-src 'self'")
    assert csp.politique(NONCE, mode="apply")[0] == "Content-Security-Policy"


def test_debug_sert_off_et_production_report_only():
    assert csp.resoudre_mode("", debug=True) == "off"
    assert csp.resoudre_mode("", debug=False) == "report-only"
    assert csp.resoudre_mode("apply", debug=False) == "apply"


def test_un_mode_mal_orthographie_empeche_de_demarrer():
    """Le pire des deux mondes est une politique absente qui ressemble à une politique respectée."""
    with pytest.raises(csp.ConfigurationCsp):
        csp.resoudre_mode("aplliquer", debug=False)


# ------------------------------------------------------------------ 3. le nonce
def test_le_nonce_est_engendre_une_seule_fois_par_requete():
    nonce = csp.Nonce()
    valeur = nonce.valeur
    assert str(nonce) == valeur and len(valeur) >= 22
    assert re.fullmatch(r"[A-Za-z0-9_-]+", valeur), "le nonce doit traverser un attribut HTML sans échappement"
    assert csp.Nonce().valeur != valeur


def test_le_nonce_ne_se_reproduce_pas():
    nonce = csp.Nonce()
    assert "Nonce" in repr(nonce) and nonce.valeur not in repr(nonce)


def test_hors_requete_la_balise_ne_met_pas_un_nonce_vide():
    from apps.common.templatetags.csp import csp_nonce

    assert str(csp_nonce({})) == ""  # gabarits d'e-mail, rendus hors requête
    porteur = type("R", (), {"csp_nonce": NONCE})()
    assert str(csp_nonce({"request": porteur})) == f'nonce="{NONCE}"'
    assert csp.valeur_du_nonce(None) is None


def test_un_porteur_sans_attribut_ne_casse_pas_le_gabarit():
    from apps.common.templatetags.csp import csp_nonce

    assert str(csp_nonce({"request": type("R", (), {})()})) == ""


def test_la_bibliotheque_est_bien_declaree_aupres_de_django():
    """Django n'admet une bibliotheque de balises que si son module expose `register` : sans cette ligne,
    `{% load csp %}` meurt d'un `TemplateSyntaxError` au premier rendu, et seulement au premier rendu."""
    from django.template.backends.django import get_installed_libraries

    assert "csp" in get_installed_libraries()


# ------------------------------------------------------------------ 4. le bout du tuyau
@override_settings(CSP_MODE="apply")
def test_une_page_du_back_office_porte_le_meme_nonce_que_son_en_tete(client, personnel):
    client.force_login(personnel)
    reponse = client.get("/manage/")
    assert reponse.status_code == 200
    nonce = _nonce_de_l_en_tete(reponse)
    corps = reponse.content.decode()
    assert f'<style nonce="{nonce}">' in corps, "le <style> du back-office n'a pas reçu le nonce de la requête"
    assert "<style>" not in corps, "un bloc <style> sans nonce serait bloqué par la politique appliquée"


@override_settings(CSP_MODE="apply")
def test_une_requete_vaut_un_nonce_et_la_suivante_un_autre(client, personnel):
    client.force_login(personnel)
    assert _nonce_de_l_en_tete(client.get("/manage/")) != _nonce_de_l_en_tete(client.get("/manage/"))


@override_settings(CSP_MODE="apply")
def test_la_page_publique_d_offre_close_est_noncée_aussi(client, django_user_model):
    """/r/{slug}?promo= est rendue sans session ni cookie : elle est couverte au même titre que l'admin."""
    from apps.qr.models import PromoCode, QrCode

    owner = django_user_model.objects.create_user(
        email="promo-csp@kamcofarm.com", password="un-mot-de-passe-solide-42", plan="business"
    )
    qr = QrCode.objects.create(
        owner=owner, label="Cave", kind="dynamic", type_id="url", target_url="https://exemple.fr/cave", is_public=True
    )
    PromoCode.objects.create(
        owner=owner,
        qr=qr,
        code="CSP-EXPIRE",
        libelle="-10%",
        remise_type="pourcentage",
        remise_valeur="10",
        expire_le=timezone.now() - dt.timedelta(days=1),
    )

    reponse = client.get(f"/r/{qr.slug}/?promo=CSP-EXPIRE")
    assert reponse.status_code == 200, reponse.content[:200]
    corps = reponse.content.decode()
    assert f'<style nonce="{_nonce_de_l_en_tete(reponse)}">' in corps
    assert 'name="robots" content="noindex"' in corps, "la page d'offre terminée ne doit pas être indexée"


@override_settings(CSP_MODE="apply")
def test_le_json_d_api_et_la_redirection_chaude_ne_paient_rien(client, dynamic_qr):
    """Un 302 de `/r/` et une réponse d'API ne sont pas des documents rendus : pas de politique, pas de nonce.

    C'est la mesure qui compte sur ce chemin : le critère de dimensionnement est `p99 < 15 ms` par scan, et
    `HttpResponseRedirect` porte un `Content-Type: text/html` hérité de Django. Sans l'exception sur les
    redirections, chaque scan paierait un tirage aléatoire et ~400 octets d'en-tête pour rien.
    """
    api = client.get("/api/v1/qr/")
    assert "Content-Security-Policy" not in api.headers
    assert not any("Report-Only" in nom for nom in api.headers)
    scan = client.get(f"/r/{dynamic_qr.slug}/")
    assert scan.status_code == 302 and scan.headers["Content-Type"].startswith("text/html")
    assert "Content-Security-Policy" not in scan.headers


@override_settings(CSP_MODE="apply")
def test_une_page_d_erreur_rendue_recoit_la_politique(client):
    """L'exception ne doit pas devenir un trou : une 404 *rendue* est un document, elle est couverte."""
    reponse = client.get("/nexiste-pas/")
    assert reponse.status_code == 404 and reponse.headers["Content-Type"].startswith("text/html")
    assert "Content-Security-Policy" in reponse.headers


@override_settings(CSP_MODE="off")
def test_le_mode_off_ne_publie_aucune_en_tete(client, personnel):
    client.force_login(personnel)
    reponse = client.get("/manage/")
    assert reponse.status_code == 200
    assert "Content-Security-Policy" not in reponse.headers
    assert "Content-Security-Policy-Report-Only" not in reponse.headers


@override_settings(CSP_MODE="apply", CSP_SIGNALER=True)
def test_le_rapport_est_demande_avec_un_groupe_resolu_en_url_absolue(client, personnel):
    client.force_login(personnel)
    reponse = client.get("/manage/")
    assert 'report-to "qrcsp"' in reponse.headers["Content-Security-Policy"]
    assert 'report-uri "/csp-violation/"' in reponse.headers["Content-Security-Policy"]
    endpoints = reponse.headers["Reporting-Endpoints"]
    assert endpoints.startswith('qrcsp="http') and endpoints.endswith('/csp-violation/"')


@override_settings(CSP_MODE="apply", CSP_SIGNALER=False)
def test_sans_signalement_les_directives_de_rapport_disparaissent(client, personnel):
    client.force_login(personnel)
    reponse = client.get("/manage/")
    assert "report-to" not in reponse.headers["Content-Security-Policy"]
    assert "Reporting-Endpoints" not in reponse.headers


# ------------------------------------------------------------------ 5. le point de chute
def test_un_rapport_de_violation_est_journalise_et_repond_204(client, monkeypatch):
    from django.core.cache import cache

    cache.clear()
    vu: list[str] = []
    monkeypatch.setattr(csp.logger, "warning", lambda message, *a: vu.append(message % a))
    reponse = client.post(
        "/csp-violation/",
        data=(
            '{"csp-report": {"violated-directive": "script-src \'self\'", "blocked-uri": "inline",'
            ' "document-uri": "https://x.test/manage/"}}'
        ),
        content_type="application/json",
    )
    assert reponse.status_code == 204 and reponse.content == b""
    assert len(vu) == 1 and "script-src" in vu[0] and "inline" in vu[0]


def test_les_deux_formes_de_rapport_sont_lues():
    ancien = csp.analyser_rapport({"csp-report": {"violated-directive": "img-src", "blocked-uri": "http://y"}})
    moderne = csp.analyser_rapport(
        {"type": "csp-violation", "body": {"effective-directive": "img-src", "blocked-url": "http://y"}}
    )
    assert ancien["directive"] == moderne["directive"] == "img-src"
    assert ancien["bloque"] == moderne["bloque"] == "http://y"
    assert csp.analyser_rapport({"csp-report": "pas un dict"}) == {}
    assert csp.analyser_rapport({}) == {}


def test_un_rapport_malforme_ne_leve_pas_et_un_get_est_refuse(client):
    from django.core.cache import cache

    cache.clear()
    assert (
        client.post("/csp-violation/", data="ceci n'est pas du json", content_type="application/json").status_code
        == 204
    )
    assert client.get("/csp-violation/").status_code == 405


def test_le_journal_ne_se_remplit_qu_une_fois_par_minute_et_par_adresse(client, monkeypatch):
    from django.core.cache import cache

    cache.clear()
    compteur: list[str] = []
    monkeypatch.setattr(csp.logger, "warning", lambda message, *a: compteur.append(message % a))
    corps = '{"csp-report": {"violated-directive": "script-src", "blocked-uri": "inline", "document-uri": "https://x.test/"}}'
    for _ in range(4):
        assert client.post("/csp-violation/", data=corps, content_type="application/json").status_code == 204
    assert len(compteur) == 1, "quatre rapports d'une même adresse ne doivent pas produire quatre lignes"


# ------------------------------------------------------------------ 6. régressions d'amont
@pytest.mark.parametrize(
    "chemin",
    ["/manage-9f2/login/", "/accounts/login/"],
    ids=["admin django", "connexion allauth"],
)
@override_settings(CSP_MODE="apply")
def test_aucun_script_executable_en_ligne_sur_les_pages_fournisseurs(client, chemin):
    """Nos gabarits n'ont aucun `<script>` ; ceux de l'admin non plus (mesuré : 50 gabarits installés).

    `django-allauth` pose bien des `<script>` en ligne, mais en `type="application/json"` : des îlots de
    données lus par un fichier externe, que `script-src` ne regarde pas — ils ne sont ni compilés ni exécutés.
    Si une montée de version en faisait de vrais scripts, cette assertion rougirait au lieu de fermer l'admin
    en production, et la réponse serait `CSP_SCRIPT_SRC_EXTRA` (un hash), jamais un `unsafe-inline` par surprise.
    """
    reponse = client.get(chemin)
    assert reponse.status_code == 200, f"{chemin} -> {reponse.status_code}"
    balises = re.findall(r"<script\b[^>]*>", reponse.content.decode(), re.I)
    for balise in balises:
        a_une_source = re.search(r"\bsrc=", balise, re.I) is not None
        est_inerte = re.search(r'type="application/json"', balise, re.I) is not None
        assert a_une_source or est_inerte, f"script exécutable en ligne sur {chemin} : {balise}"
