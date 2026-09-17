"""`django-admin` : ce que le personnel voit, et ce qui lui est interdit."""

import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client, django_user_model):
    user = django_user_model.objects.create_superuser(email="ops@kamcofarm.com", password="un-mot-de-passe-admin-x")
    user.is_staff = user.is_superuser = True
    user.save()
    client.force_login(user)
    return client


@pytest.fixture
def admin_user(django_user_model):
    user = django_user_model.objects.create_superuser(email="ops2@kamcofarm.com", password="un-mot-de-passe-admin-x")
    user.is_staff = True
    user.save()
    return user


def test_la_liste_des_qr_est_accessible(admin_client, settings, monkeypatch):
    monkeypatch.setattr("apps.common.staff_access.has_totp", lambda user: True)
    url = f"/{settings.ADMIN_URL.strip('/')}/qr/qrcode/"
    assert admin_client.get(url).status_code == 200


def test_le_code_de_verification_est_en_lecture_seule(admin_client, user, monkeypatch):
    """Aucun droit d'ajout/suppression : un code de vérification modifiable à la main = porte de service."""
    from apps.accounts.models import EmailVerificationCode

    monkeypatch.setattr("apps.common.staff_access.has_totp", lambda user: True)
    EmailVerificationCode.issue(user)
    from apps.accounts.admin import EmailVerificationCodeAdmin

    class Factice:
        pass

    # Droits écrits sur la classe : pas besoin d'instance de requête pour prouver l'interdit.
    assert EmailVerificationCodeAdmin.has_add_permission(Factice(), Factice()) is False
    assert EmailVerificationCodeAdmin.has_delete_permission(Factice(), Factice()) is False


def test_lhistorique_des_modifications_est_expose(admin_client, dynamic_qr, settings, monkeypatch):
    from apps.qr.models import QrVersion

    monkeypatch.setattr("apps.common.staff_access.has_totp", lambda user: True)
    QrVersion.objects.create(qr=dynamic_qr, actor=dynamic_qr.owner, change={"target_url": ["a", "b"]})
    response = admin_client.get(f"/{settings.ADMIN_URL.strip('/')}/qr/qrcode/{dynamic_qr.pk}/change/")
    assert response.status_code == 200
    corps = str(response.content)
    assert "istorique des modifications" in corps, "le journal des changements doit être visible côté admin"


def test_sceler_un_qr_depuis_ladmin_invalide_le_cache(admin_client, dynamic_qr, settings, monkeypatch):
    from apps.qr import cache as qr_cache
    from apps.qr.models import QrCode

    monkeypatch.setattr("apps.common.staff_access.has_totp", lambda user: True)
    qr_cache.write(dynamic_qr.slug, {"target_url": "x"})
    url = f"/{settings.ADMIN_URL.strip('/')}/qr/qrcode/"
    response = admin_client.post(url, {"action": "freeze_qrs", "_selected_action": [dynamic_qr.pk]})
    assert response.status_code in (200, 302)
    assert QrCode.objects.get(pk=dynamic_qr.pk).is_active is False
    assert qr_cache.read(dynamic_qr.slug) is None


def test_le_personnel_sans_second_facteur_est_arretele(client, admin_user, settings, monkeypatch):
    """La garde est branchée sur `admin.site.admin_view` (voir apps/common/urls_admin.py) : sans elle,
    n'importe quel compte `is_staff` — y compris un compte de staging oublié — ouvre le back-office."""
    from apps.common import staff_access

    monkeypatch.setattr(staff_access, "has_totp", lambda user: False)
    monkeypatch.setattr(settings, "ADMIN_REQUIRE_MFA", True)
    client.force_login(admin_user)
    response = client.get(f"/{settings.ADMIN_URL.strip('/')}/")
    assert response.status_code == 302, "le personnel sans TOTP ne doit pas atteindre l'admin"
    # allauth sert la page d'activation du second facteur sur /accounts/2fa/ : on vérifie la
    # destination réelle plutôt que le mot « mfa », qui n'apparaît pas dans l'URL.
    assert response["Location"].startswith("/accounts/2fa/")


def test_sans_allauth_mfa_on_refuse_plutot_que_douvrir(client, admin_user, settings, monkeypatch):
    """Retirer `allauth.mfa` ne doit pas transformer la garde en laisser-passer : le repli est 403."""
    from django.urls import NoReverseMatch

    from apps.common import staff_access

    monkeypatch.setattr(staff_access, "has_totp", lambda user: False)
    monkeypatch.setattr(settings, "ADMIN_REQUIRE_MFA", True)

    def explose(*args, **kwargs):
        raise NoReverseMatch("allauth.mfa retiré du projet")

    monkeypatch.setattr("django.urls.reverse", explose)
    client.force_login(admin_user)
    assert client.get(f"/{settings.ADMIN_URL.strip('/')}/").status_code == 403


def test_anonyme_ne_voit_pas_ladmin(client, settings):
    assert client.get(f"/{settings.ADMIN_URL.strip('/')}/").status_code == 302  # redirige vers login


def test_le_backoffice_garde_le_statut(client, admin_user, settings, monkeypatch):
    from apps.common import staff_access

    monkeypatch.setattr(staff_access, "has_totp", lambda user: True)
    client.force_login(admin_user)
    assert client.get("/manage/").status_code == 200
    assert client.get("/manage/queue/").status_code == 200
