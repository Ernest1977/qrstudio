"""Facturation : ce qui accorde un plan, et ce qui doit le refuser.

Le fil rouge de ces tests n'est pas « l'API répond 200 », c'est : **rien d'autre que le webhook signé
ne peut changer un palier**, et le webhook ne peut pas se tromper deux fois. Chaque test qui ressemble
à « on appelle la vue et on vérifie le plan » est là pour une raison précise, écrite en commentaire.
"""

import datetime as dt
import hashlib
import hmac
import json

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.billing import stripe_api
from apps.billing.models import Abonnement, EvenementPaiement, PaiementMobile, Statut

pytestmark = pytest.mark.django_db

SECRET = "whsec_test"


def _corps(type_evenement: str, objet: dict, *, identifiant: str = "evt_1") -> bytes:
    return json.dumps({"id": identifiant, "type": type_evenement, "data": {"object": objet}}).encode()


def _entete(corps: bytes, secret: str = SECRET, *, instant: int | None = None) -> str:
    """En-tête `t=…,v1=…` au format Stripe (deux signatures séparées par un espace sont acceptées)."""
    t = int(timezone.now().timestamp()) if instant is None else instant
    signature = hmac.new(secret.encode(), f"{t}.{corps.decode()}".encode(), hashlib.sha256).hexdigest()
    return f"t={t},v1={signature}"


@pytest.fixture(autouse=True)
def _secrets(settings):
    settings.STRIPE_WEBHOOK_SECRET = SECRET
    settings.STRIPE_SECRET_KEY = "sk_test_123"
    settings.STRIPE_PRICE_IDS = {"standard": "price_std", "premium": "price_prem", "business": "price_biz"}
    settings.MOBILE_MONEY_PROVIDER = "none"
    settings.MOBILE_MONEY_ALLOW_DEMO = False
    settings.BILLING_GRACE_DAYS = 3


# ------------------------------------------------------------------ demander un lien


def test_on_ne_peut_pas_acheter_le_palier_gratuit_ni_le_sien(auth_api, user):
    for palier, code in [("free", "palier_infacturable"), ("premium", "palier_deja_actif")]:
        response = auth_api.post("/api/v1/billing/checkout", {"palier": palier}, format="json")
        assert response.status_code == 400, palier
        assert response.data["error"]["code"] == code


def test_le_checkout_refuse_de_payer_sans_price_configure(auth_api, user, settings):
    """Le front ne doit jamais recevoir une URL qui mène à une page d'erreur Stripe.

    `price_id` manquant est une erreur de déploiement, pas une erreur client : elle se dit 503 et
    « l'instance n'est pas prête », et non 400 « votre requête est fausse ».
    """
    settings.STRIPE_PRICE_IDS = {}
    response = auth_api.post("/api/v1/billing/checkout", {"palier": "business"}, format="json")
    assert response.status_code == 503
    assert response.data["error"]["code"] == "stripe_price_manquant"


def test_ce_que_stripe_recoit_comme_session(auth_api, user, monkeypatch):
    user.plan = "free"
    user.save(update_fields=["plan"])
    """Le `metadata` posé ici est la seule chaîne de confiance entre la session et le compte.

    On vérifie le **contenu envoyé** (et pas seulement l'URL rendue) : si `user_id` ou `palier`
    disparaissent du `subscription_data`, le webhook ne saura plus à qui accorder quoi — et le test
    qui ne regarde que `url` restera vert.
    """
    capte = {}

    def fausse_session(**kwargs):
        capte.update(kwargs)
        return {
            "id": "cs_test_1",
            "url": "https://checkout.stripe.com/c/pay/cs_test_1",
            "customer": "cus_1",
            "expires_at": int(timezone.now().timestamp()) + 1800,
            "amount_total": 899,
            "currency": "eur",
        }

    monkeypatch.setattr(stripe_api, "session_checkout", fausse_session)
    response = auth_api.post("/api/v1/billing/checkout", {"palier": "premium"}, format="json")
    assert response.status_code == 201, response.data
    assert capte["mode"] == "subscription"
    assert capte["line_items"] == [{"price": "price_prem", "quantity": 1}]
    assert capte["metadata"] == {"user_id": str(user.pk), "palier": "premium"}
    assert capte["subscription_data"]["metadata"] == {"user_id": str(user.pk), "palier": "premium"}
    assert "{CHECKOUT_SESSION_ID}" in capte["success_url"]
    assert set(capte["payment_method_types"]) == {"apple_pay", "google_pay"}
    assert "montant_centimes" in response.data and response.data["montant_centimes"] == 899


def test_demander_un_lien_n_accorde_absolument_rien(auth_api, monkeypatch):
    monkeypatch.setattr(
        stripe_api, "session_checkout", lambda **kwargs: {"id": "cs_2", "url": "https://x", "customer": "cus_2"}
    )
    """Panier vide de conséquence : le plan ne bouge que sur webhook signé.

    Sans ce test, la tentation d'« accorder tout de suite, le webhook confirmera plus tard » reste
    ouverte — et elle vaut un palier payant à quiconque sait appeler `/checkout` et ignorer le paiement.
    """
    monkeypatch.setattr(
        stripe_api, "session_checkout", lambda **kwargs: {"id": "cs_2", "url": "https://x", "customer": "cus_2"}
    )
    from rest_framework.test import APIClient

    gratuit = User.objects.create_user(email="g@e.com", password="un-mot-de-passe-solide-1", plan="free")
    payeur = APIClient()
    payeur.force_login(gratuit)
    reponse = payeur.post("/api/v1/billing/checkout", {"palier": "premium"}, format="json")
    assert reponse.status_code == 201, reponse.data
    gratuit.refresh_from_db()
    assert gratuit.plan == "free" and gratuit.plan_effectif == "free"
    assert not gratuit.a_droit_a("qr_dynamique"), "le lien de paiement ne doit rien débloquer"
    assert Abonnement.objects.get(user=gratuit).statut != Statut.ACTIF


# ------------------------------------------------------------------ le webhook


def test_signature_exigee(api):
    corps = _corps("checkout.session.completed", {"metadata": {"user_id": "1", "palier": "premium"}})
    for entete in (None, "t=1,v1=deadbeef", "v1=aaaa"):
        response = api.post(
            "/api/v1/billing/webhook/stripe", data=corps, content_type="application/json", HTTP_STRIPE_SIGNATURE=entete
        )
        assert response.status_code == 400, entete
        assert (
            "signature" in response.data["error"]["message"].lower()
            or response.data["error"]["code"] == "signature_invalide"
        )


def test_un_rejeu_ancien_est_refuse(api, settings):
    """La tolérance d'horodatage est ce qui rend inutile la capture d'un webhook légitime.

    Sans elle, une requête enregistrée (log de proxy, Wi-Fi d'hôtel, backup) reste un ordre
    d'abonnement valable **à vie**.
    """
    corps = _corps("checkout.session.completed", {"metadata": {"user_id": "1", "palier": "premium"}})
    vieux = int(timezone.now().timestamp()) - 3600
    signature = hmac.new(SECRET.encode(), f"{vieux}.{corps.decode()}".encode(), hashlib.sha256).hexdigest()
    reponse = api.post(
        "/api/v1/billing/webhook/stripe",
        data=corps,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=f"t={vieux},v1={signature}",
    )
    assert reponse.status_code == 400
    assert "horodatage" in reponse.data["error"]["message"]


def test_le_webhook_accorde_le_palier(api, django_user_model):
    client = django_user_model.objects.create_user(
        email="acheteur@exemple.com", password="un-mot-de-passe-solide-2", plan="free"
    )
    echeance = int((timezone.now() + dt.timedelta(days=30)).timestamp())
    corps = _corps(
        "customer.subscription.updated",
        {
            "id": "sub_1",
            "object": "subscription",
            "customer": "cus_9",
            "status": "active",
            "current_period_end": echeance,
            "metadata": {"user_id": str(client.pk), "palier": "premium"},
            "items": {"data": [{"price": {"id": "price_prem", "unit_amount": 899, "currency": "eur"}}]},
        },
    )
    response = api.post(
        "/api/v1/billing/webhook/stripe",
        data=corps,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=_entete(corps),
    )
    assert response.status_code == 200, response.data
    client.refresh_from_db()
    assert client.plan == "premium" and client.a_droit_a("qr_dynamique")
    abo = Abonnement.objects.get(user=client)
    assert abo.statut == Statut.ACTIF and abo.client_id == "cus_9" and abo.periode_fin.year == timezone.now().year


def test_deux_livraisons_d_un_meme_evenement_ne_repoussent_rien(api, django_user_model):
    """Stripe rejoue. Un `subscription.updated` reçu deux fois ne doit pas créditer deux mois.

    C'est le test d'idempotence, celui qui manque dans à peu près toutes les intégrations de webhook
    écrites à la hâte — et le bug ne se voit jamais en dev, seulement dans les tickets de clients
    « on m'a facturé une période que je n'ai pas eue » ou l'inverse.
    """
    client = django_user_model.objects.create_user(
        email="double@exemple.com", password="un-mot-de-passe-solide-3", plan="free"
    )
    objet = {
        "id": "sub_2",
        "customer": "cus_8",
        "status": "active",
        "current_period_end": int((timezone.now() + dt.timedelta(days=30)).timestamp()),
        "metadata": {"user_id": str(client.pk), "palier": "business"},
        "items": {"data": [{"price": {"id": "price_biz", "unit_amount": 1599, "currency": "eur"}}]},
    }
    corps = _corps("customer.subscription.updated", objet, identifiant="evt_unique")
    entete = _entete(corps)
    url = "/api/v1/billing/webhook/stripe"
    premier = api.post(url, data=corps, content_type="application/json", HTTP_STRIPE_SIGNATURE=entete)
    date_apres_premier = Abonnement.objects.get(user=client).periode_fin
    second = api.post(url, data=corps, content_type="application/json", HTTP_STRIPE_SIGNATURE=entete)
    assert premier.status_code == second.status_code == 200
    assert second.data["doublon"] is True
    Abonnement.objects.get(user=client).refresh_from_db()
    assert Abonnement.objects.get(user=client).periode_fin == date_apres_premier
    assert EvenementPaiement.objects.filter(identifiant="evt_unique").count() == 1


def test_un_montant_incoherent_n_accorde_pas_le_palier(api, django_user_model, settings):
    """La garde monétaire : un `price_id` mal collé ne doit pas offrir Premium au tarif Standard.

    Le paiement n'est pas annulé (le client n'y est pour rien) mais **le palier n'est pas accordé** :
    l'événement reste en erreur, visible au personnel. Accorder quand même, ce serait facturer 2,99 €
    une prestation à 8,99 € — définitivement, parce que personne ne le saura.
    """
    client = django_user_model.objects.create_user(
        email="erreur@exemple.com", password="un-mot-de-passe-solide-4", plan="free"
    )
    corps = _corps(
        "customer.subscription.updated",
        {
            "id": "sub_3",
            "customer": "cus_7",
            "status": "active",
            "current_period_end": int((timezone.now() + dt.timedelta(days=30)).timestamp()),
            "metadata": {"user_id": str(client.pk), "palier": "premium"},
            "items": {"data": [{"price": {"id": "price_std", "unit_amount": 299, "currency": "eur"}}]},
        },
        identifiant="evt_montant",
    )
    response = api.post(
        "/api/v1/billing/webhook/stripe",
        data=corps,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=_entete(corps),
    )
    assert response.status_code == 200, "un 5xx ferait rejouer Stripe indéfiniment"
    client.refresh_from_db()
    assert client.plan == "free"
    journal = EvenementPaiement.objects.get(identifiant="evt_montant")
    assert journal.traite is False and "299" in journal.erreur and "premium" in journal.erreur


def test_un_type_inconnu_est_accepte_et_consigne(api, django_user_model):
    """200 + « type non géré », jamais 4xx : un 4xx sur un événement inconnu désactive la destination.

    Le jour où Stripe ajoute un type qu'on ignore, une réponse 400 ferait cesser **toutes** les
    livraisons — y compris celles qu'on sait traiter.
    """
    corps = _corps("charge.dispute.created", {"id": "du_1"}, identifiant="evt_inconnu")
    response = api.post(
        "/api/v1/billing/webhook/stripe",
        data=corps,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=_entete(corps),
    )
    assert response.status_code == 200
    assert response.data["ignore"] is True
    assert EvenementPaiement.objects.get(identifiant="evt_inconnu").erreur == "type non gere"


def test_evenement_sans_compte_connaissable_est_refuse_mais_consigne(api):
    corps = _corps("customer.subscription.updated", {"id": "sub_x", "status": "active"}, identifiant="evt_orphelin")
    response = api.post(
        "/api/v1/billing/webhook/stripe",
        data=corps,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=_entete(corps),
    )
    assert response.status_code == 200
    journal = EvenementPaiement.objects.get(identifiant="evt_orphelin")
    assert journal.traite is False and "compte" in journal.erreur.lower()


# ------------------------------------------------------------------ le cycle de vie réel


def test_paiement_en_retard_puis_sursis_puis_coupure(api, django_user_model):
    """Le seul test qui prouve la promesse commerciale : on ne coupe pas le lundi matin pour une
    banque qui répond le mardi, mais on ne laisse pas non plus le service ouvert gratis une semaine."""
    client = django_user_model.objects.create_user(
        email="retard@exemple.com", password="un-mot-de-passe-solide-5", plan="free"
    )
    url = "/api/v1/billing/webhook/stripe"
    actif = _corps(
        "customer.subscription.updated",
        {
            "id": "sub_late",
            "customer": "cus_late",
            "status": "active",
            "current_period_end": int((timezone.now() + dt.timedelta(days=30)).timestamp()),
            "metadata": {"user_id": str(client.pk), "palier": "premium"},
            "items": {"data": [{"price": {"id": "price_prem", "unit_amount": 899, "currency": "eur"}}]},
        },
        identifiant="evt_late_1",
    )
    api.post(url, data=actif, content_type="application/json", HTTP_STRIPE_SIGNATURE=_entete(actif))
    client.refresh_from_db()
    assert client.plan_effectif == "premium"

    echec = _corps(
        "invoice.payment_failed",
        {"customer": "cus_late", "metadata": {"user_id": str(client.pk), "palier": "premium"}},
        identifiant="evt_late_2",
    )
    api.post(url, data=echec, content_type="application/json", HTTP_STRIPE_SIGNATURE=_entete(echec))
    abo = Abonnement.objects.get(user=client)
    assert abo.statut == Statut.PAIEMENT_EN_RETARD and abo.en_registre is True
    assert abo.en_sursis_jusqu_a > timezone.now()
    client.refresh_from_db()
    assert client.a_droit_a("qr_dynamique"), "pendant le sursis, le service continue"

    Abonnement.objects.filter(user=client).update(en_sursis_jusqu_a=timezone.now() - dt.timedelta(minutes=1))
    client.refresh_from_db()
    assert client.plan_effectif == "free"
    assert not client.a_droit_a("qr_dynamique")


@pytest.mark.django_db(transaction=True)
def test_la_fin_d_abonnement_ne_detruit_aucun_qr(api, django_user_model, dynamic_qr):
    """Couper l'accès, ne pas casser le matériel : un flyer imprimé ne doit pas devenir une 410
    parce que le client a annulé son prélèvement.

    (La redirection d'un QR dynamique dont le compte est repassé en gratuit continue de fonctionner —
    le coût d'un slug mort est celui d'un client perdu, et la suppression reste un acte du client, pas
    une sanction.)
    """
    client = dynamic_qr.owner
    url = "/api/v1/billing/webhook/stripe"
    corps = _corps(
        "customer.subscription.deleted",
        {"id": "sub_del", "customer": "cus_del", "metadata": {"user_id": str(client.pk)}},
        identifiant="evt_del",
    )
    response = api.post(url, data=corps, content_type="application/json", HTTP_STRIPE_SIGNATURE=_entete(corps))
    assert response.status_code == 200
    client.refresh_from_db()
    assert client.plan == "free"
    import asyncio

    from django.test import RequestFactory

    from apps.redirect.views import scan_redirect

    requete = RequestFactory().get(f"/r/{dynamic_qr.slug}")
    requete.client_ip = "203.0.113.9"
    assert asyncio.run(scan_redirect(requete, dynamic_qr.slug)).status_code == 302
    dynamic_qr.refresh_from_db()
    assert dynamic_qr.pk is not None


def test_le_portail_refuse_un_compte_sans_client(auth_api):
    response = auth_api.post("/api/v1/billing/portail", {}, format="json")
    assert response.status_code == 409
    assert response.data["error"]["code"] == "client_inconnu"


def test_etat_de_licence(auth_api, user):
    Abonnement.objects.create(
        user=user,
        palier="premium",
        statut=Statut.ACTIF,
        fournisseur="stripe",
        periode_fin=timezone.now() + dt.timedelta(days=12),
    )
    corps = auth_api.get("/api/v1/billing/etat").data
    assert corps["acorde"] is True
    assert corps["abonnement"]["statut"] == "active"
    assert corps["licence"]["prix_eur"] == "8,99"


# ------------------------------------------------------------------ paiement mobile


def test_sans_agregateur_la_route_est_honnete(auth_api, user):
    """Mieux vaut un 503 explicite qu'un faux succès : « paiement accepté » sans agrégateur, c'est
    distribuer l'abonnement payant à qui appuie sur le bouton."""
    response = auth_api.post(
        "/api/v1/billing/mobile/demande", {"palier": "premium", "telephone": "+393331234567"}, format="json"
    )
    assert response.status_code == 503
    assert response.data["error"]["code"] == "mobile_provider_non_configure"
    assert not PaiementMobile.objects.exists()


def test_le_filament_de_simulation_donne_un_plan_apres_confirmation(api, django_user_model, settings):
    settings.MOBILE_MONEY_PROVIDER = "demo"
    settings.MOBILE_MONEY_ALLOW_DEMO = True
    settings.DEBUG = True
    settings.MOBILE_MONEY_WEBHOOK_SECRET = "mobile_secret"

    client = django_user_model.objects.create_user(
        email="mobile@exemple.com", password="un-mot-de-passe-solide-6", plan="free"
    )
    # Client neuf: `auth_api` porte deja un `force_authenticate(user)`, qui prime sur la session posee
    # par `force_login` — partager le client aurait facture le mauvais compte.
    from rest_framework.test import APIClient

    payeur = APIClient()
    payeur.force_login(client)
    demande = payeur.post(
        "/api/v1/billing/mobile/demande", {"palier": "premium", "telephone": "+393331234567"}, format="json"
    )
    assert demande.status_code == 201, demande.data
    reference = demande.data["reference"]
    assert demande.data["instruction"].startswith("Simulation")
    assert demande.data["montant_centimes"] == 899

    client.refresh_from_db()
    assert client.plan == "free", "la demande ne suffit pas"

    # Rappel sans signature valide -> refuse
    rappel = json.dumps({"reference": reference, "status": "confirmed", "amount_cents": 899}).encode()
    refus = api.post("/api/v1/billing/mobile/callback", data=rappel, content_type="application/json")
    assert refus.status_code == 403

    signature = hmac.new(b"mobile_secret", rappel, hashlib.sha256).hexdigest()
    ok = api.post(
        "/api/v1/billing/mobile/callback",
        data=rappel,
        content_type="application/json",
        HTTP_X_MOBILE_SIGNATURE=signature,
    )
    assert ok.status_code == 200, ok.data
    assert ok.data["accord"] == "premium"
    client.refresh_from_db()
    assert client.plan == "premium" and client.a_droit_a("analytics")

    rejeu = api.post(
        "/api/v1/billing/mobile/callback",
        data=rappel,
        content_type="application/json",
        HTTP_X_MOBILE_SIGNATURE=signature,
    )
    assert rejeu.data["doublon"] is True

    suivi = payeur.get(f"/api/v1/billing/mobile/{reference}")
    assert suivi.data["statut"] == "confirmed"
    assert PaiementMobile.objects.get(reference=reference).confirme_le is not None


def test_un_rappel_confirme_hors_delai_n_accorde_rien(api, django_user_model, settings):
    settings.MOBILE_MONEY_PROVIDER = "demo"
    settings.MOBILE_MONEY_ALLOW_DEMO = True
    settings.DEBUG = True
    settings.MOBILE_MONEY_WEBHOOK_SECRET = "mobile_secret"

    from rest_framework.test import APIClient

    client = django_user_model.objects.create_user(
        email="tardif@exemple.com", password="un-mot-de-passe-solide-7", plan="free"
    )
    payeur = APIClient()
    payeur.force_login(client)
    reference = payeur.post("/api/v1/billing/mobile/demande", {"palier": "premium"}, format="json").data["reference"]
    PaiementMobile.objects.filter(reference=reference).update(expire_le=timezone.now() - dt.timedelta(minutes=1))

    rappel = json.dumps({"reference": reference, "status": "confirmed", "amount_cents": 899}).encode()
    signature = hmac.new(b"mobile_secret", rappel, hashlib.sha256).hexdigest()
    response = api.post(
        "/api/v1/billing/mobile/callback",
        data=rappel,
        content_type="application/json",
        HTTP_X_MOBILE_SIGNATURE=signature,
    )
    assert response.status_code == 200 and response.data["expire"] is True
    client.refresh_from_db()
    assert client.plan == "free"


def test_le_montant_d_un_paiement_mobile_est_recoupe_aussi(api, django_user_model, settings):
    settings.MOBILE_MONEY_PROVIDER = "demo"
    settings.MOBILE_MONEY_ALLOW_DEMO = True
    settings.DEBUG = True
    settings.MOBILE_MONEY_WEBHOOK_SECRET = "mobile_secret"

    from rest_framework.test import APIClient

    client = django_user_model.objects.create_user(
        email="court@exemple.com", password="un-mot-de-passe-solide-8", plan="free"
    )
    payeur = APIClient()
    payeur.force_login(client)
    reference = payeur.post("/api/v1/billing/mobile/demande", {"palier": "premium"}, format="json").data["reference"]
    rappel = json.dumps({"reference": reference, "status": "confirmed", "amount_cents": 299}).encode()
    signature = hmac.new(b"mobile_secret", rappel, hashlib.sha256).hexdigest()
    response = api.post(
        "/api/v1/billing/mobile/callback",
        data=rappel,
        content_type="application/json",
        HTTP_X_MOBILE_SIGNATURE=signature,
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "montant_incoherent"
    client.refresh_from_db()
    assert client.plan == "free"
    assert PaiementMobile.objects.get(reference=reference).statut == PaiementMobile.Statut.ECHECHE


# ------------------------------------------------------------------ la vérification, en unité


@pytest.mark.parametrize(
    ("construire", "motif_attendu"),
    [
        (lambda corps, t: "v1=abc", "entete_malforme"),
        (lambda corps, t: f"t={t}", "entete_malforme"),
        (lambda corps, t: f"t={t - 4000},v1=abc", "horodatage_hors_tolerance"),
    ],
    ids=["sans_horodatage", "sans_signature", "horodatage_ancien"],
)
def test_entetes_recuses(construire, motif_attendu):
    corps = b"{}"
    t = int(timezone.now().timestamp())
    ok, motif = stripe_api.verifier_signature(corps, construire(corps, t), SECRET, maintenant=t)
    assert ok is False and motif == motif_attendu


def test_signature_validee_malgre_plusieurs_v1():
    """Stripe émet deux signatures après une rotation de secret. Un parseur qui ne lit que la première
    rend la rotation inopérante — et l'incident est découvert le jour où l'ancien secret est révoqué."""
    corps = _corps("customer.subscription.deleted", {"id": "sub_1"})
    t = int(timezone.now().timestamp())
    bonne = hmac.new(SECRET.encode(), f"{t}.{corps.decode()}".encode(), hashlib.sha256).hexdigest()
    entete = f"t={t},v1={'a' * 64},v1={bonne}"
    assert stripe_api.verifier_signature(corps, entete, SECRET, maintenant=t)[0] is True


def test_corps_altere_est_refuse():
    corps = _corps("customer.subscription.updated", {"id": "sub_1", "status": "active"})
    entete = _entete(corps)
    truque = corps.replace(b"active", b"canceled")
    assert stripe_api.verifier_signature(truque, entete, SECRET)[0] is False


def test_urls_de_retour_utilisent_l_origine_publique(db, monkeypatch, settings):
    """Le payeur est cree gratuit : `user` du fixture est premium, et reclamer Premium en Premium
    repond `palier_deja_actif` avant meme que les URLs de retour ne soient construites."""
    from rest_framework.test import APIClient

    from apps.billing import stripe_api

    payeur = User.objects.create_user(email="retour@e.com", password="un-mot-de-passe-solide-1", plan="free")
    auth = APIClient()
    auth.force_login(payeur)
    user = payeur

    settings.QR = {**settings.QR, "SHORT_BASE_URL": "https://qrstudio.kamcofarm.com/"}  # avec slash final
    settings.STRIPE_SECRET_KEY = "sk_test_pose"
    settings.STRIPE_PRICE_IDS = {"premium": "price_prem_1"}
    vus = {}

    def capture(**kwargs):
        vus.update(kwargs)
        return {"id": "cs_retour", "url": "https://checkout.stripe.com/c/pay/cs_retour", "customer": "cus_1"}

    monkeypatch.setattr(stripe_api, "session_checkout", capture)
    r = auth.post("/api/v1/billing/checkout", {"palier": "premium"}, format="json")
    assert r.status_code == 201, r.content
    assert vus["success_url"].startswith("https://qrstudio.kamcofarm.com/facturation/retour?session_id=")
    assert vus["cancel_url"] == "https://qrstudio.kamcofarm.com/facturation?annule=1"
    assert "localhost" not in vus["success_url"] + vus["cancel_url"]

    from apps.billing.models import Abonnement, Fournisseur, Statut

    Abonnement.objects.update_or_create(
        user=user,
        defaults={"fournisseur": Fournisseur.STRIPE, "statut": Statut.ACTIF, "palier": "premium", "client_id": "cus_1"},
    )
    vues_portail = {}
    monkeypatch.setattr(
        stripe_api,
        "session_portail",
        lambda **kwargs: (
            vues_portail.update(kwargs) or {"url": "https://portal.stripe.com/p/1", "expires_at": 1780000000}
        ),
    )
    r = auth.post("/api/v1/billing/portail", {}, format="json")
    assert r.status_code == 200, r.content
    assert vues_portail["return_url"] == "https://qrstudio.kamcofarm.com/facturation"
    assert vues_portail["return_url"].startswith("https://")  # Stripe rejette une URL relative


def test_sans_secret_tout_est_refuse():
    from django.test import override_settings

    corps = b"{}"
    entete = _entete(corps)
    with override_settings(STRIPE_WEBHOOK_SECRET=""):
        assert stripe_api.verifier_signature(corps, entete, "") == (False, "secret_de_webhook_absent")
