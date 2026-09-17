"""Inscription, vérification, connexion, réinitialisation — et l'anti-énumération."""

import pytest
from django.core import mail

pytestmark = pytest.mark.django_db


def _payload(client):
    from apps.accounts.models import EmailVerificationCode

    code = EmailVerificationCode.objects.filter(user=client).order_by("-created_at").first()
    return code


def test_register_cree_le_compte_et_envoie_un_code_6_chiffres(api):
    response = api.post(
        "/api/v1/auth/register",
        {"email": "new@exemple.com", "password": "un-mot-de-passe-tres-solide", "accept_terms": True},
        format="json",
    )
    assert response.status_code == 202, response.data
    assert response.data["requires_verification"] is True
    from apps.accounts.models import User

    user = User.objects.get(email="new@exemple.com")
    assert user.is_email_verified is False
    code = _payload(user)
    assert code is not None and len(code.code) == 6 and code.code.isdigit()
    assert len(mail.outbox) == 1


def test_email_normalise_en_minuscules(api):
    api.post(
        "/api/v1/auth/register",
        {"email": "  MaRiE@ExEmple.CoM ", "password": "un-mot-de-passe-tres-solide", "accept_terms": True},
        format="json",
    )
    from apps.accounts.models import User

    assert User.objects.get().email == "marie@exemple.com"


def test_mot_de_passe_faible_refuse(api):
    response = api.post(
        "/api/v1/auth/register", {"email": "x@y.fr", "password": "court", "accept_terms": True}, format="json"
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid"


def test_conditions_non_acceptees_refusees(api):
    response = api.post(
        "/api/v1/auth/register",
        {"email": "x@y.fr", "password": "un-mot-de-passe-tres-solide", "accept_terms": False},
        format="json",
    )
    assert response.status_code == 400


@pytest.mark.parametrize("email", ["new@exemple.com", "inconnu@exemple.com"])
def test_email_deja_pris_repond_comme_un_succes(api, django_user_model, email):
    """Aucun moyen de distinguer « ce compte existe » de « il n'existe pas » : ni code, ni message."""
    django_user_model.objects.create_user(email="new@exemple.com", password="un-mot-de-passe-tres-solide")
    response = api.post(
        "/api/v1/auth/register",
        {"email": email, "password": "un-mot-de-passe-tres-solide", "accept_terms": True},
        format="json",
    )
    assert response.status_code == 202
    assert "Si cette adresse est libre" in response.data["message"]
    # Corps de réponse identique dans les deux cas : même clés, même statut.
    assert set(response.data) == {"status", "email", "requires_verification", "message"}


def test_verification_valide_le_compte(api, user):
    from apps.accounts.models import EmailVerificationCode

    code = EmailVerificationCode.issue(user)
    response = api.post("/api/v1/auth/verify", {"email": user.email, "code": code.code}, format="json")
    assert response.status_code == 200, response.data
    user.refresh_from_db()
    assert user.is_email_verified is True
    # `mark_verified()` écrit sur la ligne ; l'objet du test est une autre instance -> on relit.
    code.refresh_from_db()
    assert code.consumed_at is not None


def test_cinq_mauvais_codes_verrouillent_le_canal(api, user):
    from apps.accounts.models import EmailVerificationCode

    code = EmailVerificationCode.issue(user)
    for _ in range(code.MAX_ATTEMPTS):
        response = api.post("/api/v1/auth/verify", {"email": user.email, "code": "000000"}, format="json")
        assert response.status_code == 400
    # Le bon code lui-même est refusé tant que le verrou court : sinon le verrou ne sert à rien.
    response = api.post("/api/v1/auth/verify", {"email": user.email, "code": code.code}, format="json")
    assert response.status_code == 429
    assert "Trop de tentatives" in response.data["error"]["message"]


def test_un_nouveau_code_invalide_le_precedent(api, user):
    from apps.accounts.models import EmailVerificationCode

    premier = EmailVerificationCode.issue(user)
    api.post("/api/v1/auth/verify/resend", {"email": user.email}, format="json")
    second = _payload(user)
    assert second.pk != premier.pk
    premier.refresh_from_db()
    assert premier.consumed_at is not None
    response = api.post("/api/v1/auth/verify", {"email": user.email, "code": premier.code}, format="json")
    assert response.status_code == 400


def test_login_pose_la_session_et_le_csrf(api, user):
    user.is_email_verified = True
    user.save()
    response = api.post(
        "/api/v1/auth/login", {"email": "marie@exemple.com", "password": "un-mot-de-passe-solide-42"}, format="json"
    )
    assert response.status_code == 200, response.data
    assert response.data["user"]["email"] == "marie@exemple.com"
    # Le plafond vient de la table des paliers, pas d'un nombre recopie ici : un test qui fige « 25 »
    # rouge des qu'on ajuste l'offre, et un test qui ne dit rien sur `licence` laisse le contrat du
    # front se degrader sans que personne ne le voie.
    from apps.accounts import plans

    attendu = plans.limite(response.data["user"]["licence"]["code"], "dynamiques_30j")
    assert response.data["user"]["quota"]["dynamic_limit"] == attendu
    assert response.data["user"]["licence"]["caracteristiques"]
    assert response.data["user"]["licence"]["prix_eur"] == plans.palier("premium").prix_eur


def test_login_accepte_l_email_tel_quil_a_eté_tape(api, user):
    """L'utilisateur n'a pas à connaître la normalisation interne de l'identifiant."""
    response = api.post(
        "/api/v1/auth/login", {"email": "MaRiE@ExEmple.com", "password": "un-mot-de-passe-solide-42"}, format="json"
    )
    assert response.status_code == 200, response.data


def test_mot_de_passe_errone_401_sans_detail(api, user):
    response = api.post(
        "/api/v1/auth/login", {"email": user.email, "password": "mauvais-mot-de-passe-ici"}, format="json"
    )
    assert response.status_code == 401
    assert response.data["error"]["code"] == "invalid_credentials"
    assert "hachage" not in str(response.data)


def test_dix_tentatives_puis_quarante_deux(api, user):
    for _ in range(10):
        api.post("/api/v1/auth/login", {"email": user.email, "password": "mauvais-mot-de-passe-ici"}, format="json")
    response = api.post(
        "/api/v1/auth/login", {"email": "marie@exemple.com", "password": "un-mot-de-passe-solide-42"}, format="json"
    )
    assert response.status_code == 429, "le throttle de connexion ne s'applique pas"
    assert "10/min" in str(response.data) or response.data["error"]["code"] == "client_error"


def test_compte_suspendu_refuse_la_connexion(api, user):
    user.is_suspended = True
    user.suspension_reason = "Signalements multiples"
    user.save()
    response = api.post(
        "/api/v1/auth/login", {"email": user.email, "password": "un-mot-de-passe-solide-42"}, format="json"
    )
    assert response.status_code == 403
    assert response.data["error"]["code"] == "account_suspended"


def test_me_exige_l_authentication(api):
    assert api.get("/api/v1/auth/me").status_code == 401


def test_me_et_reglages(auth_api):
    assert auth_api.get("/api/v1/auth/me").data["email"] == "marie@exemple.com"
    response = auth_api.patch("/api/v1/auth/me", {"locale": "it", "timezone": "Europe/Rome"}, format="json")
    assert response.status_code == 200
    assert response.data["locale"] == "it"
    assert response.data["timezone_name"] == "Europe/Rome"


def test_fuseau_inconnu_refuse(auth_api):
    response = auth_api.patch("/api/v1/auth/me", {"timezone": "Mars/Olympic_Mons"}, format="json")
    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid_timezone"


def test_consentement_peut_etre_retire(auth_api, user):
    auth_api.post("/api/v1/auth/consent", {"consent_tracking": True}, format="json")
    user.refresh_from_db()
    assert user.consent_tracking_at is not None
    auth_api.post("/api/v1/auth/consent", {"consent_tracking": False}, format="json")
    user.refresh_from_db()
    assert user.consent_tracking_at is None
    assert user.can_track is False


def test_reinitialisation_ne_revele_pas_l_existence_du_compte(api, user):
    premier = api.post("/api/v1/auth/password/reset", {"email": "inconnu@exemple.com"}, format="json")
    second = api.post("/api/v1/auth/password/reset", {"email": "marie@exemple.com"}, format="json")
    assert premier.status_code == second.status_code == 202
    assert premier.data["message"] == second.data["message"]
    assert len(mail.outbox) == 1  # un seul e-mail : celui du compte réel


def test_reinitialisation_change_le_mot_de_passe(api, user):
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.http import urlsafe_base64_encode

    uid = urlsafe_base64_encode(str(user.pk).encode())
    token = default_token_generator.make_token(user)
    response = api.post(
        "/api/v1/auth/password/reset/confirm",
        {
            "uid": uid,
            "token": token,
            "new_password1": "nouveau-mot-de-passe-128",
            "new_password2": "nouveau-mot-de-passe-128",
        },
        format="json",
    )
    assert response.status_code == 204
    assert (
        api.post(
            "/api/v1/auth/login", {"email": user.email, "password": "nouveau-mot-de-passe-128"}, format="json"
        ).status_code
        == 200
    )


def test_jeton_de_reinitialisation_valide_une_fois(api, user):
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.http import urlsafe_base64_encode

    uid = urlsafe_base64_encode(str(user.pk).encode())
    token = default_token_generator.make_token(user)
    corps = {
        "uid": uid,
        "token": token,
        "new_password1": "un-autre-mot-de-passe-x",
        "new_password2": "un-autre-mot-de-passe-x",
    }
    assert api.post("/api/v1/auth/password/reset/confirm", corps, format="json").status_code == 204
    user.refresh_from_db()
    # Le token change dès que le mot de passe change : le lien ne peut pas être rejoué.
    assert default_token_generator.make_token(user) != token


def test_google_url_renvoie_une_erreur_de_configuration_explicite(api, settings):
    """Sans cles et sans `SocialApp`, le bouton n'existe pas et la route est honnete.

    Deux codes, pas un : `google_not_configured` (rien n'est saisi — l'exploitant doit agir) et
    `provider_disabled` (desactive a la main alors que les cles existent — un simple `GOOGLE_LOGIN_ENABLED=0`
    doit pouvoir couper l'allee sans perdre la configuration).
    """
    settings.SOCIALACCOUNT_PROVIDERS = {}
    settings.GOOGLE_CLIENT_ID = ""
    settings.GOOGLE_CLIENT_SECRET = ""
    settings.GOOGLE_LOGIN_ENABLED = None
    response = api.get("/api/v1/auth/google/url")
    assert response.status_code == 503
    assert response.data["error"]["code"] == "google_not_configured"

    settings.GOOGLE_CLIENT_ID = "cle"
    settings.GOOGLE_CLIENT_SECRET = "secret"
    settings.GOOGLE_LOGIN_ENABLED = False
    reponse2 = api.get("/api/v1/auth/google/url")
    assert reponse2.data["error"]["code"] == "provider_disabled"


def test_google_url_construit_le_renvoi(api, settings):
    settings.GOOGLE_CLIENT_ID = "x"
    settings.GOOGLE_CLIENT_SECRET = "y"
    settings.GOOGLE_LOGIN_ENABLED = None
    settings.SOCIALACCOUNT_PROVIDERS = {"google": {"APPS": [{"client_id": "x", "secret": "y"}]}}
    response = api.get("/api/v1/auth/google/url", {"next": "/dashboard/"})
    assert response.status_code == 200
    assert "google" in response.data["authorize_url"]
    assert "/accounts/google/login/callback/" in response.data["callback_hint"]


def test_logout_detruit_la_session(api, user):
    """`force_login` et non `force_authenticate` : seul le premier crée la session qu'on veut détruire."""
    api.force_login(user)
    assert api.get("/api/v1/auth/me").status_code == 200
    assert api.post("/api/v1/auth/logout").status_code == 204
    assert api.get("/api/v1/auth/me").status_code == 401


# --------------------------------------------------------------------- expéditeur


def test_tous_les_emails_du_service_viennent_de_l_expediteur_actif(api, user, settings):
    """`itsupport@kamcofarm.com`, pas un `noreply@` de fantôme.

    Le test porte sur les **deux** messages (vérification et réinitialisation) et sur la chaîne
    complète `From:`, pas seulement l'adresse: un nom d'affichage absent ou mal échappé fait
    atterrir le message en spam chez certains webmail, et le client nous écrit alors que « le code
    n'arrive jamais ».
    """
    from django.core import mail

    assert settings.DEFAULT_FROM_EMAIL == "QR Studio <itsupport@kamcofarm.com>"
    response = api.post(
        "/api/v1/auth/register",
        {"email": "from@exemple.com", "password": "un-mot-de-passe-solide-9", "accept_terms": True},
        format="json",
    )
    # 202 et non 201: la reponse d'inscription est neutre par construction (l'existence du compte ne
    # doit pas se deviner au code de statut ni a la forme du corps). Le test porte sur l'enveloppe
    # du message, pas sur la route.
    assert response.status_code == 202, response.data
    envoi = mail.outbox[-1]
    # On verifie l'enveloppe *telle qu'un MTA la verra*: le nom d'affichage doit etre separe de
    # l'adresse (un `From: QR Studio, itsupport@...` non quote fait rejeter le message chez certains
    # relais), et l'adresse utile doit etre exacte.
    from email.utils import parseaddr

    nom, adresse = parseaddr(envoi.from_email)
    assert nom == "QR Studio" and adresse == "itsupport@kamcofarm.com", envoi.from_email
    assert envoi.extra_headers.get("Auto-Submitted", "").startswith("auto-")
    assert "Reply-To" not in envoi.extra_headers, "Reply-To = From ici: le poser en double trompe les filtres"

    mail.outbox.clear()
    api.post("/api/v1/auth/password/reset", {"email": user.email}, format="json")
    assert mail.outbox, "aucun e-mail de réinitialisation envoyé"
    assert parseaddr(mail.outbox[-1].from_email)[1] == "itsupport@kamcofarm.com"


def test_server_email_herite_de_l_expediteur(monkeypatch, tmp_path):
    """Les rapports d'erreur du service partent au même endroit que les clients: `SERVER_EMAIL` vide
    doit hériter de la valeur, sinon une `CrashReport` part vers `root@localhost` et personne ne la lit."""
    import importlib

    from config import env as env_module

    monkeypatch.setenv("ENV_FILE", str(tmp_path / "absent.env"))
    monkeypatch.delenv("SERVER_EMAIL", raising=False)
    monkeypatch.setenv("DJANGO_ENV", "prod")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "y" * 64 + "-cle-unique-0123456789-tres-longue")
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "qrstudio.kamcofarm.com")
    importlib.reload(env_module)
    importlib.reload(importlib.import_module("config.settings.base"))
    prod = importlib.reload(importlib.import_module("config.settings.prod"))
    assert prod.SERVER_EMAIL == "QR Studio <itsupport@kamcofarm.com>"


def _jeton_reset(api, user):
    """(uid, token) valables pour `password/reset/confirm` — meme source que la vue."""
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    return urlsafe_base64_encode(force_bytes(user.pk)), default_token_generator.make_token(user)
