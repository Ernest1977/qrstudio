"""L'adaptateur Flutterwave : ce qui part au réseau, et ce qui doit rester bloqué ici.

Ces tests ne vérifient pas « le HTTP est bien formaté », ils vérifient les quatre décisions qui
protègent de l'argent :

1. la référence qui fait le lien est **la nôtre** (`tx_ref`), pas celle du fournisseur ;
2. une conversion de devise sans taux configuré est un refus, pas une improvisation ;
3. un rappel `successful` ne suffit jamais : sans re-interrogation confirmée, rien n'est accordé ;
4. la signature du rappel se compare en temps constant, et l'absence de secret = refus total.

Le réseau est simulé au niveau de `requests.request` : on voit l'URL, l'en-tête d'autorisation et le
corps exact, ce qui est le seul moyen de prouver qu'un montant n'est pas envoyé dans la mauvaise unité.
"""

import base64
import hashlib
import hmac
import json

import pytest

from apps.billing.models import Abonnement, PaiementMobile

pytestmark = pytest.mark.django_db

CLE = "FLWSECK-TEST-1"
HASH = "un-secret-hash-tres-long-et-aleatoire"


class Reponse:
    def __init__(self, status_code=200, corps=None):
        self.status_code = status_code
        self._corps = corps if corps is not None else {}

    def json(self):
        if self._corps is None:
            raise ValueError("pas de JSON")
        return self._corps


class Reseau:
    """Enregistre les appels sortants et renvoie ce qu'on lui a dit de renvoyer."""

    def __init__(self, reponses: dict[str, object] | None = None):
        self.appels: list[dict] = []
        self.reponses = reponses or {}

    def request(self, methode, url, *, params=None, json=None, headers=None, timeout=None, **_kwargs):
        self.appels.append(
            {"methode": methode, "url": url, "params": params, "corps": json, "entetes": headers, "timeout": timeout}
        )
        cle = "charges" if "charges" in url else ("verify" if "verify" in url else "autre")
        envoi = self.reponses.get(cle, {"status": "success", "data": {}})
        if isinstance(envoi, Exception):
            raise envoi
        return Reponse(200, envoi)


@pytest.fixture
def flutterwave(settings, monkeypatch):
    settings.MOBILE_MONEY_PROVIDER = "flutterwave"
    settings.MOBILE_MONEY_FLUTTERWAVE = {
        "base_url": "https://api.flutterwave.com",
        "secret_key": CLE,
        "secret_hash": HASH,
        "devise": "EUR",
        "type": "mobile_money_uganda",
        "reseau": "MTN",
        "pays": "",
        "taux_change": "",
        "arrondi": "0.01",
        "timeout": "12",
    }
    settings.MOBILE_MONEY_EXPIRE_MINUTES = 30

    def installe(reseau: Reseau):
        import requests

        monkeypatch.setattr(requests, "request", reseau.request)
        return reseau

    return installe


def _demande(client, palier="standard", telephone="+256 700 000000"):
    return client.post("/api/v1/billing/mobile/demande", {"palier": palier, "telephone": telephone}, format="json")


def test_la_demande_part_au_reseau_en_unites_et_garde_la_reference_flutterwave(auth_api, user, flutterwave):
    reseau = flutterwave(
        Reseau(
            {
                "charges": {
                    "status": "success",
                    "message": "Charge initiated",
                    "data": {
                        "id": 1234,
                        "flw_ref": "FLW-MOCK-9",
                        "status": "pending",
                        "amount": 2.99,
                        "currency": "EUR",
                    },
                }
            }
        )
    )
    reponse = _demande(auth_api)
    assert reponse.status_code == 201, reponse.data
    appel = reseau.appels[0]
    assert appel["methode"] == "POST"
    assert appel["url"] == "https://api.flutterwave.com/v3/charges?type=mobile_money_uganda"
    assert appel["entetes"]["Authorization"] == f"Bearer {CLE}"

    paiement = PaiementMobile.objects.get()
    # `tx_ref` = NOTRE reference : c'est elle qui retrouve la ligne en base sur un rappel. Le `flw_ref`
    # est conserve a cote, pour le rapprochement comptable.
    assert appel["corps"]["tx_ref"] == paiement.reference
    assert appel["corps"]["amount"] == 2.99, "les reseaux MoMo facturent en unites, pas en centimes"
    assert appel["corps"]["currency"] == "EUR"
    assert appel["corps"]["phone_number"] == "256700000000", "spaces et + enleves, sinon le reseau rejette"
    assert appel["corps"]["email"] == user.email, "le titulaire est celui du compte, pas une adresse generee"
    assert appel["corps"]["network"] == "MTN"
    assert reponse.data["montant_affiche"] == "2.99 EUR"
    assert paiement.detail["flw_ref"] == "FLW-MOCK-9"
    assert paiement.detail["montant_facture"] == "2.99"


def test_un_numero_invalide_ne_part_pas_au_reseau(auth_api, flutterwave):
    reseau = flutterwave(Reseau())
    reponse = _demande(auth_api, telephone="0700")
    assert reponse.status_code == 400
    assert reponse.data["error"]["code"] == "mobile_telephone_invalide"
    assert reseau.appels == []


def test_sans_cle_ou_sans_secret_hash_la_route_refuse_de_simuler_un_paiement(auth_api, flutterwave, settings):
    reglages = dict(settings.MOBILE_MONEY_FLUTTERWAVE)
    reglages["secret_key"] = ""
    settings.MOBILE_MONEY_FLUTTERWAVE = reglages
    reponse = _demande(auth_api)
    assert reponse.status_code == 503
    assert reponse.data["error"]["code"] == "mobile_provider_non_configure"

    reglages = dict(settings.MOBILE_MONEY_FLUTTERWAVE)
    reglages["secret_key"] = CLE
    reglages["secret_hash"] = ""  # sans verification possible du rappel, aucun paiement ne sera jamais conclu
    settings.MOBILE_MONEY_FLUTTERWAVE = reglages
    reponse = _demande(auth_api)
    assert reponse.status_code == 503
    assert reponse.data["error"]["code"] == "mobile_provider_invalide"


def test_changer_de_devise_sans_taux_est_un_refus_pas_une_improvisation(auth_api, flutterwave, settings):
    reglages = dict(settings.MOBILE_MONEY_FLUTTERWAVE)
    reglages["devise"] = "UGX"
    reglages["taux_change"] = ""
    settings.MOBILE_MONEY_FLUTTERWAVE = reglages
    flutterwave(Reseau())
    reponse = _demande(auth_api)
    assert reponse.status_code == 503
    assert "taux" in reponse.data["error"]["message"] or "MOBILE_MONEY_FLUTTERWAVE" in reponse.data["error"]["message"]

    # Avec un taux, la conversion est explicite, arrondie, et c'est elle qui sera recoupee plus tard.
    reglages["taux_change"] = "4000"
    settings.MOBILE_MONEY_FLUTTERWAVE = reglages
    reponse = _demande(auth_api)
    assert reponse.status_code == 201, reponse.data
    paiement = PaiementMobile.objects.get()
    assert paiement.detail["montant_facture"] == "11960.00"
    assert paiement.detail["devise_facturee"] == "UGX"
    assert paiement.montant_centimes == 299, "nos livres restent en centimes d'euro : la grille est la"


def test_reseau_injoignable_ne_donne_pas_un_faux_succes(auth_api, flutterwave):
    import requests

    flutterwave(Reseau({"charges": requests.ConnectionError("reseau coupe")}))
    reponse = _demande(auth_api)
    assert reponse.status_code == 502
    assert reponse.data["error"]["code"] == "mobile_provider_injoignable"
    assert not PaiementMobile.objects.exists()
    assert not Abonnement.objects.exists()


# ------------------------------------------------------------------ signature du rappel


def _rappel(client, corps: bytes, entete: str | None):
    # `headers={"Verif-Hash": ...}` : le client de test convertit le nom en `HTTP_VERIF_HASH`, exactement
    # ce que la vue lit via `request.headers`. Ecrire `HTTP_VERIF_HASH` ici passerait a cote du contrat.
    entetes = {"Verif-Hash": entete} if entete is not None else {}
    return client.post("/api/v1/billing/mobile/callback", data=corps, content_type="application/json", headers=entetes)


def _demander_puis_rappeler(client, corps: dict, entete: str | None):
    reponse = _demande(client)
    assert reponse.status_code == 201, reponse.data
    reference = client.get(f"/api/v1/billing/mobile/{reponse.data['reference']}")
    assert reference.status_code == 200
    return _rappel(
        client,
        json.dumps({**corps, "data": {**corps.get("data", {}), "tx_ref": reponse.data["reference"]}}).encode(),
        entete,
    )


def test_les_deux_formes_de_signature_sont_acceptees_une_seule_rejetee(auth_api, flutterwave):
    flutterwave(
        Reseau(
            {
                "charges": {"status": "success", "data": {"id": 1, "flw_ref": "F1", "status": "pending"}},
                "verify": {
                    "status": "success",
                    "data": {"status": "successful", "amount": 2.99, "currency": "EUR", "flw_ref": "F1"},
                },
            }
        )
    )
    corps = {"event": "charge.completed", "status": "successful", "amount": 2.99, "currency": "EUR"}

    # Forme 1 : le secret hash recopie dans `Verif-Hash`.
    reponse = _demander_puis_rappeler(auth_api, dict(corps), HASH)
    assert reponse.status_code == 200, reponse.data
    assert PaiementMobile.objects.count() == 1 and PaiementMobile.objects.first().statut == "confirmed"

    PaiementMobile.objects.all().delete()
    Abonnement.objects.all().delete()

    # Forme 2 : HMAC-SHA256 du corps brut, en base64, dans `flutterwave-signature`.
    brut = json.dumps({**corps, "data": {"tx_ref": "ignore"}}).encode()
    signature = base64.b64encode(hmac.new(HASH.encode(), brut, hashlib.sha256).digest()).decode()
    from apps.billing import mobile

    fournisseur = mobile.fournisseur_actif()
    assert fournisseur.verifier_rappel(corps=brut, entete_signature=signature) is True
    assert fournisseur.verifier_rappel(corps=brut, entete_signature="n'importe-quoi") is False
    assert fournisseur.verifier_rappel(corps=brut, entete_signature=None) is False


def test_un_rappel_reussit_sans_confirmation_du_reseau_n_accorde_rien(auth_api, flutterwave):
    flutterwave(
        Reseau(
            {
                "charges": {"status": "success", "data": {"id": 2, "flw_ref": "F2", "status": "pending"}},
                # `verify_by_reference` repond `pending` : le rappel seul ne prouve rien.
                "verify": {"status": "success", "data": {"status": "pending", "amount": 2.99, "currency": "EUR"}},
            }
        )
    )
    reponse = _demander_puis_rappeler(
        auth_api, {"event": "charge.completed", "status": "successful", "amount": 2.99, "currency": "EUR"}, HASH
    )
    assert reponse.status_code == 200, reponse.data
    assert reponse.data.get("attente_reverification") is True
    paiement = PaiementMobile.objects.get()
    assert paiement.statut == "pending", "le paiement reste en attente, ni accorde ni refuse"
    assert not Abonnement.objects.exists()


def test_le_montant_est_recoupe_dans_la_devise_facturee(auth_api, flutterwave, settings):
    reglages = dict(settings.MOBILE_MONEY_FLUTTERWAVE)
    reglages["devise"] = "UGX"
    reglages["taux_change"] = "4000"
    settings.MOBILE_MONEY_FLUTTERWAVE = reglages
    reseau = flutterwave(
        Reseau(
            {
                "charges": {"status": "success", "data": {"id": 3, "flw_ref": "F3", "status": "pending"}},
                # 299 centimes EUR a 4000 = 11 960,00 UGX. Le reseau confirme 11 900 : ce n'est pas pareil.
                "verify": {"status": "success", "data": {"status": "successful", "amount": 11900, "currency": "UGX"}},
            }
        )
    )
    reponse = _demander_puis_rappeler(auth_api, {"event": "charge.completed"}, HASH)
    assert reponse.status_code == 400
    assert "montant" in reponse.data["error"]["message"].lower()
    assert PaiementMobile.objects.get().statut == "failed"
    assert not Abonnement.objects.exists()
    assert "11960" in reponse.data["error"]["message"]

    # Le bon montant, cette fois : le palier est accorde, et l'URL du reseau est restee la notre.
    reseau.reponses["verify"] = {
        "status": "success",
        "data": {"status": "successful", "amount": 11960, "currency": "UGX"},
    }
    PaiementMobile.objects.all().delete()
    reponse = _demander_puis_rappeler(auth_api, {"event": "charge.completed"}, HASH)
    assert reponse.status_code == 200, reponse.data
    abo = Abonnement.objects.get()
    assert abo.palier == "standard" and abo.statut == "active"


def test_un_evenement_de_reseau_inconnu_est_journalise_sans_accorder(auth_api, flutterwave):
    flutterwave(Reseau({"charges": {"status": "success", "data": {"id": 4, "status": "pending"}}}))
    reponse = _demander_puis_rappeler(auth_api, {"event": "transfer.completed", "status": "successful"}, HASH)
    assert reponse.status_code == 200
    assert reponse.data.get("recu") is True
    assert not Abonnement.objects.exists(), "un evenement qui ne nous concerne pas ne change pas un palier"


def test_la_requete_de_statut_utilise_la_reinterrogation(auth_api, flutterwave):
    from apps.billing import mobile

    flutterwave(
        Reseau(
            {
                "verify": {
                    "status": "success",
                    "data": {"status": "successful", "amount": 2.99, "currency": "EUR", "flw_ref": "F9"},
                }
            }
        )
    )
    fournisseur = mobile.fournisseur_actif()
    assert fournisseur.statut("QRM-TEST") == "confirmed"
    constatee = fournisseur.constater("QRM-TEST")
    assert constatee is not None and constatee.ref_fournisseur == "F9" and str(constatee.montant) == "2.99"
    # Une reponse vide n'est pas une autorisation : `None` = « je ne sais pas », et le service attend.
    flutterwave(Reseau({"verify": {"status": "error", "message": "not found"}}))
    assert mobile.fournisseur_actif().constater("QRM-INCONNUE") is None
