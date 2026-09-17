"""L'espace admin : qui y entre, ce qu'il y voit, et le fait que la création n'y est pas facturée.

Le test central est le n°8 en substance : **le même compte, au même palier, avec ou sans la permission**
ne doit pas seulement changer de prix, il doit changer de porte. Si les deux chemins divergent ailleurs que
sur l'exemption, l'espace admin est une remise déguisée et non un outil interne.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse

from apps.analytics.aggregates import aggreger_plateforme
from apps.analytics.models import PlatformDailyStats, QrDailyStats
from apps.billing.models import Abonnement
from apps.qr.models import QrCode

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _second_facteur_admis(monkeypatch):
    """`/manage/` est derrière la garde TOTP du personnel : on la lève pour tester le métier.

    Le contrôle lui-même est assertion par `test_sans_second_facteur_l_espace_admin_est_ferme` — sinon on
    vérifierait l'accès aux pages en ayant désactivé la porte, ce qui ne prouverait rien.
    """
    monkeypatch.setattr("apps.common.staff_access.has_totp", lambda user: True)


@pytest.fixture
def superadmin(django_user_model):
    """Le « super admin » dont parle la demande : plan gratuit, accès total, zéro facturation."""
    return django_user_model.objects.create_superuser(
        email="root@kamcofarm.com", password="un-mot-de-passe-solide-42", plan="free"
    )


@pytest.fixture
def employe(django_user_model):
    """Membre du personnel sans permission d'exemption : `is_staff` ne doit rien donner de plus."""
    return django_user_model.objects.create_user(
        email="support@kamcofarm.com", password="un-mot-de-passe-solide-42", plan="free", is_staff=True
    )


@pytest.fixture
def jour_qr(superadmin):
    # `origine="admin"` : c'est ce qui le rend comptable a part, et ce que le filtre de provenance verifie.
    return QrCode.objects.create(
        owner=superadmin, kind="static", type_id="text", label="Vitrine", payload="bonjour", origine="admin"
    )


@pytest.fixture
def qr_client(user):
    return QrCode.objects.create(
        owner=user, kind="dynamic", type_id="url", label="Campagne", target_url="https://kamcofarm.example/a"
    )


def _semis(jour_qr, qr_client, *, jour: dt.date | None = None) -> dt.date:
    """Deux QR agrégés sur un même jour, puis les totaux plateforme recalculés depuis ces lignes."""
    jour = jour or dt.date.today()
    QrDailyStats.objects.create(
        qr_id=jour_qr.pk, day=jour, country_code="FR", scans=30, unique_visitors=12, mobile=20, desktop=10
    )
    QrDailyStats.objects.create(
        qr_id=jour_qr.pk, day=jour, country_code="BE", scans=5, unique_visitors=3, mobile=5, desktop=0
    )
    QrDailyStats.objects.create(
        qr_id=qr_client.pk, day=jour, country_code="FR", scans=100, unique_visitors=60, mobile=70, desktop=30
    )
    aggreger_plateforme(jour)
    return jour


# --------------------------------------------------------------------------- qui entre


def test_sans_second_facteur_l_espace_admin_est_ferme(client, superadmin, settings, monkeypatch):
    """La page d'analytique est du personnel : elle doit tomber sur la meme garde que django-admin."""
    from django.urls import reverse as _reverse

    monkeypatch.setattr("apps.common.staff_access.has_totp", lambda user: False)
    monkeypatch.setattr(settings, "ADMIN_REQUIRE_MFA", True)
    client.force_login(superadmin)
    reponse = client.get(_reverse("backoffice:analytique"))
    assert reponse.status_code == 302
    assert reponse["Location"].startswith("/accounts/2fa/")


def test_un_anonyme_ne_voit_ren(client):
    for url in [reverse("backoffice:dashboard"), reverse("backoffice:analytique"), reverse("backoffice:qr_creer")]:
        reponse = client.get(url)
        assert reponse.status_code == 302, url
        assert "/accounts/login/" in reponse.url, url


def test_un_client_connecte_non_personnel_recoit_un_403_pas_une_boucle(client, user):
    """403, et non 302 vers la connexion : c'est la correction trouvee par le test du front.

    La page de connexion d'allauth ejecte un deja-connecte vers sa destination ; renvoyer un compte client
    vers `/accounts/login/` depuis `/manage/` produit un aller-retour infini (`ERR_TOO_MANY_REDIRECTS`).
    ``staff_member_required`` seul ne peut pas le dire, d'ou `acces_admin`.
    """
    client.force_login(user)
    reponse = client.get(reverse("backoffice:analytique"))
    assert reponse.status_code == 403
    for url in [reverse("backoffice:qr_creer"), reverse("backoffice:abonnements"), reverse("backoffice:queue")]:
        assert client.get(url).status_code == 403, url


def test_is_staff_ne_suffit_pas_pour_voir_les_stats_ni_cree_gratuitement(client, employe):
    """La distinction est le cœur de la demande : « admin » ne veut pas dire « n'est pas facturé ».

    Un compte de support lit donc l'état du service (déjà ouvert au personnel) mais ni l'analytique globale,
    ni la création gratuite — les deux sont gated par une permission nommément accordée.
    """
    client.force_login(employe)
    assert client.get(reverse("backoffice:analytique")).status_code == 403
    assert client.get(reverse("backoffice:qr_creer")).status_code == 403
    assert client.get(reverse("backoffice:abonnements")).status_code == 403
    assert client.get(reverse("backoffice:dashboard")).status_code == 200


def test_le_super_admin_voit_les_nombres_agreges(client, superadmin, jour_qr, qr_client):
    jour = _semis(jour_qr, qr_client)
    client.force_login(superadmin)
    reponse = client.get(reverse("backoffice:analytique"))
    assert reponse.status_code == 200
    contenu = reponse.content.decode()
    # 30 + 5 + 100 = 135 scans le jour même, et les totaux plateforme viennent de la table agrégée.
    # On cible le KPI lui-meme : `135` apparaitrait aussi dans une coordonnee de graphique, et un test
    # qui passe sur une coordonnee ne prouverait rien le jour ou l'echelle change.
    assert "<b>135</b>" in contenu
    assert "Vitrine" in contenu and "Campagne" in contenu
    assert "viewBox=" in contenu and "<polyline" in contenu


def test_le_top_classe_par_volume_et_le_filtre_de_provenance_sepere_admin_et_client(
    client, superadmin, jour_qr, qr_client
):
    _semis(jour_qr, qr_client)
    client.force_login(superadmin)

    global_page = client.get(reverse("backoffice:analytique")).content.decode()
    assert "<b>135</b>" in global_page

    seulement_client = client.get(reverse("backoffice:analytique"), {"origine": "client"}).content.decode()
    assert "<b>100</b>" in seulement_client and "<b>135</b>" not in seulement_client

    seulement_admin = client.get(reverse("backoffice:analytique"), {"origine": "admin", "jours": 7}).content.decode()
    assert "<b>35</b>" in seulement_admin and "<b>135</b>" not in seulement_admin


def test_un_jour_hors_periode_n_entre_pas_dans_la_serie(client, superadmin, jour_qr, qr_client):
    _semis(jour_qr, qr_client, jour=dt.date.today() - dt.timedelta(days=45))
    client.force_login(superadmin)
    sept_jours = client.get(reverse("backoffice:analytique"), {"jours": 7}).content.decode()
    assert "<b>0</b>" in sept_jours and "<b>135</b>" not in sept_jours
    quatre_vingt_dix = client.get(reverse("backoffice:analytique"), {"jours": 90}).content.decode()
    assert "<b>135</b>" in quatre_vingt_dix


# --------------------------------------------------------------------------- création gratuite


def test_la_creation_admin_n_est_facturee_ni_limitee_et_porte_la_provenance(client, superadmin):
    """Un super admin en plan gratuit crée un QR **dynamique** : impossible pour un client au même palier."""
    client.force_login(superadmin)
    reponse = client.post(
        reverse("backoffice:qr_creer"),
        {
            "kind": "dynamic",
            "type_id": "url",
            "label": "Maquette commerciale",
            "target_url": "https://kamcofarm.example/demo",
        },
    )
    assert reponse.status_code == 302, reponse.content[:400]
    qr = QrCode.objects.get()
    assert qr.kind == "dynamic" and qr.origine == "admin" and qr.owner_id == superadmin.pk
    assert qr.slug and qr.is_resolvable
    assert not Abonnement.objects.exists(), "aucune licence ne doit naitre d'une création d'admin"
    assert superadmin.plan == "free" and superadmin.plan_effectif == "free"
    from django.contrib.admin.models import LogEntry

    trace = LogEntry.objects.get(object_id=str(qr.pk))
    assert "sans facturation" in trace.change_message


def test_le_meme_post_en_api_cote_client_non_habilite_recoit_un_402(api, django_user_model):
    """La seule différence entre les deux comptes est la permission : le refus vient d'ailleurs de nulle part.

    Sans ce test, « l'espace admin est gratuit » pourrait reposer sur un `if admin:` dans la vue — et le
    client équivalent, lui, se prendrait un 402 pour la même donnée.
    """
    client_libre = django_user_model.objects.create_user(
        email="libre@exemple.com", password="un-mot-de-passe-solide-42", plan="free"
    )
    api.force_authenticate(user=client_libre)
    reponse = api.post(
        "/api/v1/qr/",
        {"kind": "dynamic", "type_id": "url", "label": "Maquette", "target_url": "https://kamcofarm.example/demo"},
        format="json",
    )
    assert reponse.status_code == 402
    assert reponse.data["error"]["code"] == "plan_required"
    assert not QrCode.objects.exists()


def test_un_type_inconnu_est_refuse_par_les_regles_de_l_api(client, superadmin):
    """La page d'admin refuse exactement ce que l'API refuse : elle n'a pas ses propres regles.

    C'est le controle de parite des deux chemins — si la vue ajoutait (ou retirait) une validation, ce
    test la verrait, parce que la phrase d'erreur attendue est celle du serializer, pas d'un gabarit.
    """
    client.force_login(superadmin)
    reponse = client.post(
        reverse("backoffice:qr_creer"),
        {"kind": "static", "type_id": "inexistant", "label": "Mauvais type", "payload": "bonjour"},
    )
    assert reponse.status_code == 400
    contenu = reponse.content.decode()
    assert "Type inconnu" in contenu
    assert not QrCode.objects.exists()


def test_la_suppression_admin_passe_par_le_service_et_n_efface_rien(client, superadmin, jour_qr):
    client.force_login(superadmin)
    reponse = client.post(reverse("backoffice:qr_supprimer", args=[jour_qr.pk]))
    assert reponse.status_code == 302
    jour_qr.refresh_from_db()
    assert jour_qr.deleted_at is not None
    assert QrCode.objects.count() == 1


def test_les_pages_de_controle_sont_rendues(client, superadmin, user, jour_qr):
    """`/manage/` et `/manage/abonnements/` : la page d'abonnement a deja casse en `FieldError`.

    `Abonnement` n'a pas de colonne `id` (sa cle primaire est son OneToOne vers `User`) : un `Count("id")`
    ecrit dans une vue d'admin ne se voit qu'au navigateur, jamais par les autres tests. Cette page est
    donc rendue avec des lignes reelles — abonnement actif, evenement non traite, paiement mobile en cours.
    """
    import datetime as dt

    from apps.billing.models import Abonnement, EvenementPaiement, PaiementMobile

    Abonnement.objects.create(
        user=user,
        palier="premium",
        statut="active",
        fournisseur="stripe",
        periode_fin=dt.datetime.now(dt.UTC) + dt.timedelta(days=10),
    )
    EvenementPaiement.objects.create(
        identifiant="evt_broken_1", type_evenement="invoice.payment_failed", traite=False, erreur="montant incoherent"
    )
    PaiementMobile.objects.create(
        reference="QRM-SMOKE-1",
        user=user,
        palier="standard",
        montant_centimes=299,
        telephone="256700000000",
        fournisseur="flutterwave",
        statut="pending",
        expire_le=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=20),
        detail={"montant_facture": "2.99", "devise_facturee": "EUR"},
    )
    client.force_login(superadmin)
    for url in [reverse("backoffice:dashboard"), reverse("backoffice:abonnements"), reverse("backoffice:queue")]:
        assert client.get(url).status_code == 200, url
    corps = client.get(reverse("backoffice:abonnements")).content.decode()
    assert "QRM-SMOKE-1" in corps
    assert "evt_broken_1" in corps and "montant incoherent" in corps
    assert "invoice.payment_failed" in corps
    assert "Mararie" not in corps  # pas de concatena tion accidentelle de l'email dans le gabarit
    assert "premium" in corps and "active" in corps


# --------------------------------------------------------------------------- drill-down et agrégats


def test_le_drill_down_donne_l_apercu_et_les_pays(client, superadmin, jour_qr, qr_client):
    _semis(jour_qr, qr_client)
    client.force_login(superadmin)
    reponse = client.get(reverse("backoffice:analytique_qr", args=[jour_qr.pk]))
    assert reponse.status_code == 200
    contenu = reponse.content.decode()
    assert "data:image/svg+xml;base64," in contenu
    assert "France" in contenu and "Belgique" in contenu


def test_le_recalcul_reecrit_les_totaux_plateforme(client, superadmin, jour_qr, qr_client):
    jour = _semis(jour_qr, qr_client)
    PlatformDailyStats.objects.filter(day=jour).update(scans=0)
    client.force_login(superadmin)
    reponse = client.post(reverse("backoffice:recalcul"), {"jours": "1"})
    assert reponse.status_code == 302
    assert "recalcule=" in reponse.url
    # Le total est reecrit depuis `QrDailyStats`, pas affiche depuis un cache : passer de 0 a 135 le prouve.
    assert PlatformDailyStats.objects.get(day=jour).scans == 135


def test_le_graphique_ne_depend_d_aucune_ressource_externe(client, superadmin, jour_qr, qr_client):
    _semis(jour_qr, qr_client)
    client.force_login(superadmin)
    contenu = client.get(reverse("backoffice:analytique")).content.decode()
    # `admin/base_site.html` charge son propre `theme.js` en local : ce qu'on exclut ici, c'est une
    # ressource distante — le back-office doit repondre meme quand un CDN est mort.
    assert '<script src="http' not in contenu and "cdn" not in contenu.lower()
    assert 'href="http' not in contenu.split("</head>")[0].replace('href="/accounts', "")
    assert contenu.count("<polyline") >= 3, "un graphique par bloc, sans exception muette"


def test_un_libelle_hostile_ne_peut_pas_sortir_du_texte():
    """Le contrat de `charts._marquer`, verifie la ou il est ecrit : `mark_safe` ne recoit que de l'echappe.

    Les libelles viennent de la base (labels de QR saisis par les clients, noms de pays). Sans cette
    assertion, la suppression `# nosec` de `charts.py` serait une confiance accordee sans preuve.
    """
    from django.utils.html import escape

    from apps.common import charts

    hostile = '<img src=x onerror="alert(1)">'
    svg_barres = str(charts.barres([(hostile, 5)], titre=hostile))
    svg_courbe = str(charts.courbe([{"libelle": hostile, "points": [1, 2]}], labels=[hostile], titre=hostile))
    for rendu in (svg_barres, svg_courbe):
        # Ce qui compte n'est pas que le mot « onerror » disparaisse — il est du texte, et le texte doit
        # rester du texte : c'est l'absence de balise reconstituee qui est la veritable propriete.
        assert "<img" not in rendu and "<script" not in rendu
        assert escape(hostile) in rendu
        assert rendu.count("<svg") == 1


def test_un_graphique_sans_donnee_ne_s_invente_pas_zero_partout():
    from apps.common import charts

    vide = str(charts.barres([], titre="Rien"))
    assert "Aucune donnee sur la periode" in vide
    plat = str(
        charts.courbe([{"libelle": "Scans", "points": [0, 0, 0]}], labels=["09-01", "09-02", "09-03"], titre="Zeros")
    )
    assert plat.count("<polyline") == 1, "une serie plate doit rester tracee, pas disparaitre"

    # Serie plus longue que l'axe : un tableau de bord ne merite pas un 500 pour une etiquette manquante.
    asymetrique = str(charts.courbe([{"libelle": "Scans", "points": [3, 5, 8]}], labels=["09-01"], titre="Decalage"))
    assert asymetrique.count("<circle") == 3


# --------------------------------------------------------------------------- la règle d'exemption, en unité


def test_la_regle_d_exemption_est_unique_et_exposee(superadmin, employe, user, django_user_model):
    from apps.accounts.exemption import exonere_de_facturation

    assert exonere_de_facturation(superadmin) is True
    assert exonere_de_facturation(employe) is False, "is_staff seul ne doit rien ouvrir"
    assert exonere_de_facturation(user) is False

    # La permission accordee nommement change tout, sans toucher a `is_staff`.
    from django.contrib.auth.models import Permission

    employe.user_permissions.add(
        Permission.objects.get(codename="creer_sans_facturation", content_type__app_label="qr")
    )
    # Un objet recharge, pas `refresh_from_db()` : le backend met les permissions en cache sur l'instance,
    # et c'est exactement ce qui fait qu'un changement de permission ne devient visible qu'a la requete
    # suivante en production. L'assertion doit donc repartir d'un objet neuf.
    employe = django_user_model.objects.get(pk=employe.pk)
    assert exonere_de_facturation(employe) is True


def test_l_exemption_ouvre_l_export_pdf_et_les_stats(api, django_user_model):
    """Le trou signalé à la revue : `views.py` gate l'export et les stats sur `a_droit_a`, sans exemption."""
    from apps.qr.models import QrCode as _Qr

    admin = django_user_model.objects.create_superuser(
        email="root2@kamcofarm.com", password="un-mot-de-passe-solide-42", plan="free"
    )
    qr = _Qr.objects.create(
        owner=admin, kind="dynamic", type_id="url", label="Lien", target_url="https://kamcofarm.example/x"
    )
    api.force_authenticate(user=admin)
    assert admin.a_droit_a("export_pdf") is True and admin.a_droit_a("analytics") is True
    reponse = api.get(f"/api/v1/qr/{qr.pk}/stats/")
    assert reponse.status_code == 200, reponse.data

    pareil = django_user_model.objects.create_user(
        email="payant@exemple.com", password="un-mot-de-passe-solide-42", plan="free"
    )
    qr2 = _Qr.objects.create(
        owner=pareil, kind="dynamic", type_id="url", label="Lien 2", target_url="https://kamcofarm.example/y"
    )
    api.force_authenticate(user=pareil)
    assert api.get(f"/api/v1/qr/{qr2.pk}/stats/").status_code == 402


def test_auth_me_decline_l_exemption_au_front(client, superadmin, user):
    client.force_login(superadmin)
    reponse = client.get("/api/v1/auth/me")
    assert reponse.status_code == 200 and reponse.json()["exonere_de_facturation"] is True
    client.force_login(user)
    assert client.get("/api/v1/auth/me").json()["exonere_de_facturation"] is False


# ------------------------------------------------------------------ rendu et codes promo a la main
def test_la_creation_admin_porte_le_rendu_et_le_code_promo(client, superadmin):
    """L'agent cree un QR **avec** les options Entreprise, et l'offre qui va avec, en un seul formulaire.

    C'est la partie de la demande qui ne doit pas se faire par `psql` : style, animation et code promo
    generes a la volée, avec la date de fin obligatoire.
    """
    from datetime import timedelta

    from django.utils import timezone

    from apps.qr.models import PromoCode

    client.force_login(superadmin)
    reponse = client.post(
        reverse("backoffice:qr_creer"),
        {
            "kind": "dynamic",
            "type_id": "url",
            "label": "Operations cave",
            "target_url": "https://kamcofarm.example/cave",
            "art": "naples",
            "animation": "bordure",
            "frames": "32",
            "liser": "40",
            "promo_libelle": "-25% sur la cave",
            "promo_valeur": "25",
            "promo_jours": "21",
            "promo_usages_max": "300",
        },
    )
    assert reponse.status_code == 302, reponse.content[:600]
    qr = QrCode.objects.get(label="Operations cave")
    assert qr.design["art"] == "naples" and qr.design["frames"] == 32 and qr.design["liser"] == 40
    assert qr.origine == "admin"
    assert "/manage/qr/creer/?cree=" in reponse["Location"] and "promo=" in reponse["Location"]

    offre = PromoCode.objects.get(qr=qr)
    assert offre.owner_id == superadmin.pk
    assert offre.remise_type == "pourcentage" and str(offre.remise_valeur) == "25.00"
    assert offre.usages_max == 300 and offre.actif is True
    duree = offre.expire_le - timezone.now()
    assert timedelta(days=20) < duree <= timedelta(days=22), duree
    assert offre.code.startswith("PROMO-")  # genere, pas saisi
    # Le code n'affiche qu'une fois : l'URL de confirmation doit le rendre lisible.
    confirmation = client.get(reponse["Location"])
    assert confirmation.status_code == 200
    assert offre.code in confirmation.content.decode()


def test_sans_dure_indiquee_aucune_offre_n_est_creee(client, superadmin):
    from apps.qr.models import PromoCode

    client.force_login(superadmin)
    reponse = client.post(
        reverse("backoffice:qr_creer"),
        {"kind": "static", "type_id": "text", "label": "Simple", "payload": "bonjour", "promo_libelle": "sans date"},
    )
    assert reponse.status_code == 302
    assert PromoCode.objects.count() == 0
    assert "promo=" not in reponse["Location"]


def test_une_duree_non_chiffree_ne_cree_pas_de_code(client, superadmin):
    """`promo_jours="abc"` n'est pas une erreur 500 ni une offre sans fin : c'est simplement ignore."""
    from apps.qr.models import PromoCode

    client.force_login(superadmin)
    reponse = client.post(
        reverse("backoffice:qr_creer"),
        {"kind": "static", "type_id": "text", "label": "Brouillon", "payload": "bonjour", "promo_jours": "bientot"},
    )
    assert reponse.status_code == 302
    assert PromoCode.objects.count() == 0


def test_l_admin_des_codes_promo_se_charge_et_juge_comme_le_scan(client, superadmin, django_user_model):
    """La fiche admin affiche le **verdict du scan**, pas le booléen `actif` : sinon l'agent ment au client."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.qr.models import PromoCode

    client.force_login(superadmin)
    qr = QrCode.objects.create(
        owner=superadmin, kind="dynamic", type_id="url", label="Cave", target_url="https://kamcofarm.example/c"
    )
    offre = PromoCode.objects.create(
        owner=superadmin,
        qr=qr,
        code="PROMO-CAVE-K7QF",
        expire_le=timezone.now() - timedelta(hours=1),
        remise_type="pourcentage",
        remise_valeur="15",
    )
    from django.conf import settings

    racine = f"/{settings.ADMIN_URL.strip('/')}"
    page = client.get(f"{racine}/qr/promocode/")
    assert page.status_code == 200
    assert "PROMO-CAVE-K7QF" in page.content.decode()
    detail = client.get(f"{racine}/qr/promocode/{offre.pk}/change/")
    contenu = detail.content.decode()
    assert "Cette offre est terminée" in contenu
    # Le champ code n'est pas modifiable : un agent qui le reecrit decouple le flyer de sa ligne.
    assert 'name="code"' not in contenu
