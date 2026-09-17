"""Export et effacement: les deux routes que le droit impose, et qui doivent être testées comme telles."""

import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def compte_avec_donnees(user, dynamic_qr):
    """Un compte qui a tout: QR, version, agrégats, ligne brute, signalement, code de vérification."""
    from datetime import date

    from django.utils import timezone

    from apps.accounts.models import EmailVerificationCode
    from apps.analytics.ingest import persist_many
    from apps.analytics.models import QrDailyStats
    from apps.qr.models import QrReport, QrVersion

    QrVersion.objects.create(qr=dynamic_qr, actor=user, change={"label": ["a", "b"]})
    QrReport.objects.create(qr=dynamic_qr, reason="lienmort", reporter_email="plaignant@example.com")
    QrDailyStats.objects.create(qr_id=dynamic_qr.pk, day=date.today(), country_code="IT", scans=5, unique_visitors=4)
    persist_many(
        [
            {
                "scan_id": "rgpd1",
                "ts": timezone.now().isoformat(),
                "day": timezone.now().date().isoformat(),
                "qr_id": dynamic_qr.pk,
                "owner_id": user.pk,
                "ip_trunc": "203.0.113.0",
                "ip_prefix_len": 24,
                "geo": {"country_code": "IT"},
            }
        ]
    )
    EmailVerificationCode.issue(user)
    return dynamic_qr


def test_export_contient_le_compte_les_qr_et_le_consentement(auth_api, compte_avec_donnees):
    response = auth_api.get("/api/v1/account/export")
    assert response.status_code == 200, response.data
    corps = response.json()
    assert corps["format"] == "qrs-export/1"
    assert corps["compte"]["email"] == "marie@exemple.com"
    assert corps["compte"]["consentement_mesure"]["accorde"] is False
    assert corps["qr"][0]["slug"] == compte_avec_donnees.slug
    assert corps["statistiques"][0]["country_code"] == "IT"
    assert any("label" in str(v["change"]) for v in corps["historique_modifications"])
    assert "attachment" in response["Content-Disposition"]
    assert "mot_de_passe" not in str(corps) and "password" not in corps["compte"]


def test_export_refuse_un_volume_synchrone_inconvenant(auth_api, user, settings):
    from apps.accounts import services
    from apps.qr.models import QrCode

    services.EXPORT_LIMITE_LIGNES = 3
    for i in range(5):
        QrCode.objects.create(owner=user, kind="static", type_id="text", label=f"l{i}", payload="x")
    response = auth_api.get("/api/v1/account/export")
    assert response.status_code == 413
    assert response.data["error"]["details"]["limite"] == 3


def test_effacement_exige_le_mot_de_passe(auth_api, compte_avec_donnees, django_user_model):
    from apps.accounts.models import User

    reponse = auth_api.delete("/api/v1/account/erase", {"password": "mauvais"}, format="json")
    assert reponse.status_code == 403
    assert reponse.data["error"]["code"] == "wrong_password"
    assert User.objects.filter(pk=compte_avec_donnees.owner_id).exists()


def test_apres_effacement_il_ne_reste_aucune_ligne(auth_api, compte_avec_donnees):
    """Le critère « 0 ligne restante », vérifié table par table — pas « la vue a renvoyé 204 »."""
    from apps.accounts.models import EmailVerificationCode, User
    from apps.analytics.models import QrDailyStats, ScanEvent
    from apps.qr.models import QrCode, QrReport, QrVersion

    user_id = compte_avec_donnees.owner_id
    qr_id = compte_avec_donnees.pk
    response = auth_api.delete("/api/v1/account/erase", {"password": "un-mot-de-passe-solide-42"}, format="json")
    assert response.status_code == 204, response.data
    assert response["X-Deleted"].startswith("utilisateur=1")
    # Liste explicite (modele, champ de rattachement, valeur) : une boucle qui déduit la valeur du nom
    # du champ est exactement l'endroit ou un test vert peut cacher une table oubliee.
    for modele, champ, valeur in [
        (User, "pk", user_id),
        (QrCode, "owner_id", user_id),
        (QrVersion, "qr_id", qr_id),
        (QrReport, "qr_id", qr_id),
        (QrDailyStats, "qr_id", qr_id),
        (ScanEvent, "owner_id", user_id),
        (EmailVerificationCode, "user_id", user_id),
    ]:
        assert not modele.objects.filter(**{champ: valeur}).exists(), f"{modele.__name__} contient encore des lignes"


@pytest.mark.django_db(transaction=True)
def test_apres_effacement_le_slug_ne_redirige_plus(dynamic_qr):
    """Le cache de résolution ne doit pas faire survivre la destination au compte: un 302 apres une
    suppression effective est une fuite, meme si la ligne n'existe plus en base."""
    import asyncio

    from django.test import RequestFactory

    from apps.redirect.views import scan_redirect

    request = RequestFactory().get(f"/r/{dynamic_qr.slug}")
    request.client_ip = "203.0.113.7"
    assert asyncio.run(scan_redirect(request, dynamic_qr.slug)).status_code == 302
    from apps.qr import services as qr_services

    qr_services.hard_delete(dynamic_qr, actor=None)
    reponse = asyncio.run(scan_redirect(RequestFactory().get(f"/r/{dynamic_qr.slug}"), dynamic_qr.slug))
    assert reponse.status_code == 410, "la cle en cache a survécu à la suppression"


def test_compte_sans_mot_de_passe_doit_taper_le_mot_d_ordre(api, django_user_model, settings):
    from apps.accounts.models import User

    social = django_user_model.objects.create_user(email="google@example.com", password=None)
    social.set_unusable_password()
    social.is_email_verified = True
    social.save()
    api.force_login(social)
    reponse = api.delete("/api/v1/account/erase", {"confirm": "je supprime"}, format="json")
    assert reponse.status_code == 400
    assert reponse.data["error"]["code"] == "confirmation_required"
    assert User.objects.filter(pk=social.pk).exists()
    assert api.delete("/api/v1/account/erase", {"confirm": "SUPPRIMER"}, format="json").status_code == 204
    assert not User.objects.filter(pk=social.pk).exists()


def test_la_session_est_detruite_avec_le_compte(api, user):
    """`api` + `force_login` ( vraie session ) et non `auth_api`: le fixture d'API force
    l'authentification par objet, ce qui ne laisse aucune session a detruire - le test verifierait
    alors le mecanique de DRF et non la notre."""
    api.force_login(user)
    api.delete("/api/v1/account/erase", {"password": "un-mot-de-passe-solide-42"}, format="json")
    reponse = api.get("/api/v1/qr/")
    assert reponse.status_code == 401


def test_le_chemin_canonique_fonctionne_aussi(auth_api, user):
    """`/api/v1/account/` (avec slash) rend le meme service: le front qui colle la route de la doc
    ne doit pas tomber sur le seul cas ou la route est morte a cause d'un detail de convention."""
    assert auth_api.delete("/api/v1/account/", {"password": "mauvais"}, format="json").status_code == 403
