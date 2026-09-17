"""La grille tarifaire réelle, vérifiée — pas une grille que les tests accréditent par omission.

Un prix est une promesse faite au client : s'il vit dans le code sans test, la prochaine personne qui
touche aux paliers peut le changer sans qu'aucune campagne ne rougisse. Les assertions ci-dessous sont
donc écrites sur les **chiffres de la grille** (2,99 / 8,99 / 15,99 €) et sur la **logique d'héritage**
(standard ⊂ premium ⊂ business), qui est la partie où l'on se trompe réellement.
"""

import importlib

import pytest

from apps.accounts import plans

pytestmark = pytest.mark.django_db


def test_les_prix_sont_ceux_de_la_grille():
    assert plans.PALIERS["free"].prix_centimes == 0
    assert plans.PALIERS["standard"].prix_centimes == 299
    assert plans.PALIERS["premium"].prix_centimes == 899
    assert plans.PALIERS["business"].prix_centimes == 1599
    # Format d'affichage français, une seule implémentation : le front ne recopie jamais « 2.99 ».
    assert plans.PALIERS["standard"].prix_eur == "2,99"
    assert plans.PALIERS["premium"].prix_eur == "8,99"
    assert plans.PALIERS["business"].prix_eur == "15,99"


def test_l_heritage_des_caracteristiques_est_cumulatif():
    gratuit = plans.caracteristiques("free")
    standard = plans.caracteristiques("standard")
    premium = plans.caracteristiques("premium")
    entreprise = plans.caracteristiques("business")
    assert gratuit <= standard <= premium <= entreprise
    assert {"qr_statique", "export_image"} <= gratuit
    assert {"score_scannabilite", "cadres_cta", "export_pdf", "themes_sectoriels"} <= standard
    assert {"qr_dynamique", "analytics"} <= premium
    assert {"carte_visite_connectee", "qr_multi_liens", "api_developpeurs"} <= entreprise
    # Un palier ne doit JAMAIS retirer une caractéristique du palier inférieur : « premium » qui
    # enlèverait l'export PDF au client qui monte serait une régression vendue comme une amélioration.
    assert "export_pdf" not in gratuit and "export_pdf" in premium


def test_le_code_de_palier_inconnu_ne_donne_pas_acces():
    assert plans.caracteristiques(None) == plans.caracteristiques("free")
    assert plans.palier("licorne").code == "free"
    assert plans.rang("licorne") == 0


@pytest.mark.parametrize(("ancien", "nouveau"), [("pro", "premium"), ("team", "business"), ("free", "free")])
def test_mappage_des_anciens_identifiants(ancien, nouveau):
    assert plans.plan_depuis_ancien_identifiant(ancien) == nouveau


def test_la_migration_redirige_les_comptes_existants(django_user_model):
    """La migration de données est jouée pour de vrai, pas relue.

    Sans elle, un compte `pro` garde une valeur absente de la grille et retombe donc en gratuit : le
    client qui paie perd ses QR dynamiques au déploiement, sans message.
    """
    from django.apps import apps as django_apps

    migration = importlib.import_module("apps.accounts.migrations.0002_grille_tarifaire_reelle")

    # `full_clean` refuserait `plan="pro"` (plus dans les choices) : on écrit la valeur historique
    # comme elle est réellement en base avant le déploiement.
    vieux = django_user_model.objects.create_user(email="ancien@exemple.com", password="un-mot-de-passe-solide-8")
    django_user_model.objects.filter(pk=vieux.pk).update(plan="pro")
    equipe = django_user_model.objects.create_user(email="equipe@exemple.com", password="un-mot-de-passe-solide-8")
    django_user_model.objects.filter(pk=equipe.pk).update(plan="team")

    migration.vers_grille_reelle(django_apps, None)
    vieux.refresh_from_db()
    equipe.refresh_from_db()
    assert vieux.plan == "premium"
    assert equipe.plan == "business"

    # Aller-retour : `standard` n'a pas d'equivalent d'avant — la migration inverse doit le dire plutôt
    # que de le faire disparaitre.
    migration.vers_grille_precedente(django_apps, None)
    vieux.refresh_from_db()
    assert vieux.plan == "pro"


# --------------------------------------------------------------- ce que la grille change aux routes


@pytest.fixture
def client_gratuit(django_user_model, api):
    libre = django_user_model.objects.create_user(
        email="gratuit@exemple.com", password="un-mot-de-passe-solide-7", plan="free"
    )
    api.force_login(libre)
    return libre


def test_un_compte_gratuit_ne_peut_pas_creer_de_qr_dynamique(api, client_gratuit):
    """C'est votre liste : les QR dynamiques apparaissent au palier Premium.

    Le code de réponse est 402 (et non 403) parce que la porte s'ouvre avec un paiement : un front qui
    lit 403 affiche « accès refusé », un front qui lit 402 affiche « 8,99 €/mois ».
    """
    response = api.post(
        "/api/v1/qr/", {"kind": "dynamic", "type_id": "url", "target_url": "https://exemple.com"}, format="json"
    )
    assert response.status_code == 402
    erreur = response.data["error"]
    assert erreur["code"] == "plan_required"
    assert erreur["details"]["palier_requis"] == "premium"
    assert "Premium" in erreur["message"] and "8,99" in erreur["message"]
    from apps.qr.models import QrCode

    assert not QrCode.objects.filter(owner=client_gratuit).exists(), "le refus ne doit rien avoir laissé"


def test_le_statique_reste_ouvert_en_gratuit(api, client_gratuit):
    reponse = api.post("/api/v1/qr/", {"kind": "static", "type_id": "text", "payload": "bonjour"}, format="json")
    assert reponse.status_code == 201, reponse.data


def test_les_stats_sont_fermees_sans_premium(api, client_gratuit):
    from apps.qr.models import QrCode

    qr = QrCode.objects.create(owner=client_gratuit, kind="dynamic", target_url="https://exemple.com")
    response = api.get(f"/api/v1/qr/{qr.pk}/stats/")
    assert response.status_code == 402
    assert response.data["error"]["details"]["caracteristique"] == "analytics"


def test_la_profondeur_d_historique_depende_du_palier(auth_api, user, dynamic_qr):
    """Premium = 90 jours, Entreprise = 365. Sans clamp, n'importe quel client peut demander dix ans
    d'agrégats à chaque ouverture de page — le coût est pour nous, pas pour lui."""
    user.plan = "premium"
    user.save()
    assert auth_api.get(f"/api/v1/qr/{dynamic_qr.pk}/stats/?days=365").data["window_days"] == 90

    user.plan = "business"
    user.save()
    assert auth_api.get(f"/api/v1/qr/{dynamic_qr.pk}/stats/?days=365").data["window_days"] == 365


def test_un_abonnement_echu_redescend_en_gratuit_dans_la_seconde(django_user_model, api):
    """`plan` est ce qui est facturé, `plan_effectif` ce qui est autorisé.

    Sans cette distinction, une carte refusée laisse le client au palier payant jusqu'à la prochaine
    tâche de nuit — et « la tâche de nuit » est exactement le composant qui ne s'exécute pas quand on
    en a besoin.
    """
    from datetime import timedelta

    from django.utils import timezone

    from apps.qr.models import QrCode

    payant = django_user_model.objects.create_user(
        email="expire@exemple.com",
        password="un-mot-de-passe-solide-6",
        plan="premium",
        plan_until=timezone.now() - timedelta(minutes=5),
    )
    assert payant.plan == "premium"
    assert payant.plan_effectif == "free"
    assert not payant.a_droit_a("qr_dynamique")

    api.force_login(payant)
    assert (
        api.post("/api/v1/qr/", {"kind": "static", "type_id": "text", "payload": "x"}, format="json").status_code == 201
    )
    assert (
        api.post(
            "/api/v1/qr/", {"kind": "dynamic", "type_id": "url", "target_url": "https://exemple.com"}, format="json"
        ).status_code
        == 402
    )
    assert QrCode.objects.filter(owner=payant, kind="dynamic").count() == 0

    payant.plan_until = timezone.now() + timedelta(days=30)
    payant.save()
    assert payant.plan_effectif == "premium"
    assert payant.a_droit_a("qr_dynamique")


def test_les_quotas_sont_reglables_sans_deploiement(user, monkeypatch):
    """Le robinet d'exploitation existe (`QUOTA_<PALIER>_<LIMITE>`), et `None`/`-1` = illimité.

    Ce sont les seuils qui protègent le serveur, pas les paliers : un client en attente de virement
    ou une promotion passagère se règlent ici, pas dans la table tarifaire.
    """
    from apps.qr.models import QrCode

    monkeypatch.setenv("QUOTA_PREMIUM_STATIQUES", "3")
    assert user.quota_statique == 3
    for i in range(3):
        QrCode.objects.create(owner=user, kind="static", type_id="text", payload=str(i))
    with pytest.raises(Exception) as exc:
        from apps.qr import quota

        quota.assert_can_create_static(user)
    assert exc.value.code == "quota_exceeded"

    monkeypatch.setenv("QUOTA_PREMIUM_STATIQUES", "-1")
    assert user.quota_statique == 1_000_000_000  # « illimité » n'est pas None côté modèle


def test_le_config_publique_expose_la_grille_et_letat_google(api, settings):
    """Le front lit prix et état des fournisseurs ici, avant même la connexion.

    Deux prix dans deux dépôts = un prix faux quelque part. Et le bouton Google suit le même
    chemin : il n'est pas rendu quand la route répond 503.
    """
    settings.GOOGLE_CLIENT_ID = ""
    settings.GOOGLE_CLIENT_SECRET = ""
    settings.GOOGLE_LOGIN_ENABLED = None
    response = api.get("/api/v1/auth/config")
    assert response.status_code == 200
    corps = response.json()
    codes = {p["code"]: p for p in corps["paliers"]}
    assert set(codes) == {"free", "standard", "premium", "business"}
    assert codes["standard"]["prix_centimes"] == 299
    assert "export_pdf" in codes["standard"]["apporte"]
    assert "qr_dynamique" in codes["premium"]["apporte"]
    assert corps["fournisseurs"]["google"]["actif"] is False
    assert corps["fournisseurs"]["google"]["raison"] == "client_id_absent"

    settings.GOOGLE_CLIENT_ID = "cle"
    settings.GOOGLE_CLIENT_SECRET = "secret"
    assert api.get("/api/v1/auth/config").json()["fournisseurs"]["google"]["actif"] is True


def test_google_desactive_alors_que_les_cles_existent(api, settings):
    """`GOOGLE_LOGIN_ENABLED=0` doit suffire à couper l'allée, sans effacer la configuration.

    Deux codes distincts : `provider_disabled` (l'exploitant a tranché) et `google_not_configured`
    (rien n'est saisi). Le premier ne doit jamais finir en ticket chez le fournisseur.
    """
    settings.GOOGLE_CLIENT_ID = "cle"
    settings.GOOGLE_CLIENT_SECRET = "secret"
    settings.GOOGLE_LOGIN_ENABLED = False
    response = api.get("/api/v1/auth/google/url")
    assert response.status_code == 503
    assert response.data["error"]["code"] == "provider_disabled"

    settings.GOOGLE_LOGIN_ENABLED = None
    settings.GOOGLE_CLIENT_ID = ""
    settings.GOOGLE_CLIENT_SECRET = ""
    settings.SOCIALACCOUNT_PROVIDERS = {}
    assert api.get("/api/v1/auth/google/url").data["error"]["code"] == "google_not_configured"


def test_le_payload_de_compte_porte_la_licence(auth_api, user):
    response = auth_api.get("/api/v1/auth/me")
    licence = response.data["licence"]
    assert licence["code"] == "premium"
    assert licence["prix_eur"] == "8,99"
    assert "analytics" in licence["caracteristiques"]
    assert licence["limites"]["dynamiques_30j"] == 100
    # La liste complète des paliers n'a rien à faire dans `/me` : elle est publique, déjà servie
    # par /config, et la dupliquer ici ferait deux sources de prix.
    assert "paliers" not in licence


def test_le_drapeau_d_inscription_est_honore_des_deux_cotes(api, settings):
    """Un interrupteur lu uniquement par le front est un cache, pas un controle."""
    settings.ALLOW_REGISTRATION = False
    assert api.get("/api/v1/auth/config").json()["inscription_ouverte"] is False
    reponse = api.post(
        "/api/v1/auth/register",
        {"email": "nouveau@exemple.com", "password": "un-mot-de-passe-solide-5", "accept_terms": True},
        format="json",
    )
    assert reponse.status_code == 403
    assert reponse.data["error"]["code"] == "registration_closed"
    from apps.accounts.models import User

    assert not User.objects.filter(email="nouveau@exemple.com").exists()

    settings.ALLOW_REGISTRATION = True
    assert api.get("/api/v1/auth/config").json()["inscription_ouverte"] is True


def test_le_gel_du_quota_gratuit_ne_survit_pas_a_un_paiement(django_user_model):
    """La non-rétroactivité de la baisse (20 → 1) est un **prêt**, pas un plancher à vie.

    Le compte a été créé avant la bascule avec 20 QR statiques promis ; il paie Premium (quota
    illimité : le gel ne le rabote pas, `max()` fait le travail) ; il annule. A cet instant il n'a plus
    droit ni au plafond Premium ni à la vieille promesse — et le repasser a 20 serait la seule facon de
    faire de l'annulation une aubaine.
    """
    from datetime import timedelta

    from django.utils import timezone

    from apps.billing import services
    from apps.billing.models import Statut

    compte = django_user_model.objects.create_user(
        email="gele@exemple.com", password="un-mot-de-passe-solide-7", plan="free"
    )
    compte.geler_quota_statique(valeur=20, palier="free")
    assert compte.quota_statique == 20  # la licence d'avant-grille garde ses 20

    services.accord(
        compte,
        palier="premium",
        fournisseur="stripe",
        statut=Statut.ACTIF,
        periode_fin=timezone.now() + timedelta(days=30),
    )
    compte.refresh_from_db()
    assert compte.quota_statique >= 5000  # Premium n'est pas bridé par un gel posé en gratuit
    assert compte.quota_statique_gele is None, "le gel doit etre solde au paiement"

    services.accord(
        compte,
        palier="free",
        fournisseur="stripe",
        statut=Statut.ANNULE,
        periode_fin=timezone.now() - timedelta(days=1),
    )
    compte.refresh_from_db()
    assert compte.plan_effectif == "free"
    assert compte.quota_statique == 1, "un compte qui annule entre dans la grille en vigueur, pas dans l'ancienne"
