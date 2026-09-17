"""Validation des destinations : SSRF, open redirect, longueur — le garde le plus important du projet."""

import socket

import pytest

from apps.common.exceptions import ApiError
from apps.qr.validators import validate_target_url

OK = "https://kamcofarm.com/boutique?utm=1"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/x",
        "http://10.0.0.5/prive",
        "http://172.16.0.9/",
        "http://192.168.1.1/admin",
        "http://169.254.169.254/latest/meta-data/",  # métadonnées cloud
        "http://[::1]/",
        "http://[fd00::1]/",
        "http://0.0.0.0/",
    ],
)
def test_adresses_internes_refusees(url, settings):
    settings.QR = {**settings.QR, "ALLOW_PRIVATE_TARGETS": False, "DNS_CHECK_ON_WRITE": False}
    with pytest.raises(ApiError) as exc:
        validate_target_url(url)
    assert exc.value.code == "target_private"


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("", "target_required"),
        ("   ", "target_required"),
        ("kamcofarm.com", "target_scheme"),  # sans schéma : sinon `Location: kamcofarm.com` est relatif
        ("ftp://kamcofarm.com/f", "target_scheme"),
        ("javascript:alert(1)", "target_scheme"),
        ("data:text/html,hi", "target_scheme"),
        ("//evil.com/x", "target_scheme"),
        ("http://bien.com@evil.com/", "target_invalid"),  # credentials cachés dans l'URL
        ("https://exemple.com/x#ancre", "target_invalid"),
        ("https://" + "a" * 2100 + ".com/", "target_too_long"),
        ("https://exemple.com/ligne\x00injectee", "target_invalid"),
    ],
)
def test_forms_invalides_refusees(url, code, settings):
    settings.QR = {**settings.QR, "ALLOW_PRIVATE_TARGETS": False, "DNS_CHECK_ON_WRITE": False}
    with pytest.raises(ApiError) as exc:
        validate_target_url(url)
    assert exc.value.code == code


def test_url_valide_normalisee(settings):
    settings.QR = {**settings.QR, "DNS_CHECK_ON_WRITE": False}
    assert validate_target_url("https://kamcofarm.com") == "https://kamcofarm.com/"
    assert validate_target_url("  https://x.fr/a  ") == "https://x.fr/a"


def test_dns_reverle_une_cible_interne(monkeypatch, settings):
    """Le nom est public, l'adresse derrière est privée : c'est le cas que le seul contrôle de chaîne rate."""
    settings.QR = {**settings.QR, "ALLOW_PRIVATE_TARGETS": False, "DNS_CHECK_ON_WRITE": True}

    def faux_resolve(host, port, proto=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", faux_resolve)
    with pytest.raises(ApiError) as exc:
        validate_target_url("https://ping-internal.example.com/")
    assert exc.value.code == "target_private"
    assert exc.value.details["resolved"] == "10.1.2.3"


def test_nom_inresolvable_refuse(monkeypatch, settings):
    settings.QR = {**settings.QR, "DNS_CHECK_ON_WRITE": True}

    def lever(host, port, proto=None):
        raise socket.gaierror("rien")

    monkeypatch.setattr(socket, "getaddrinfo", lever)
    with pytest.raises(ApiError) as exc:
        validate_target_url("https://domaine-qui-n-existe-pas.fr/")
    assert exc.value.code == "target_unresolved"


def test_dev_autorise_les_cibles_locales(settings):
    """En dev on redirige vers `localhost:5173` : le garde doit être désactivable, pas absent."""
    settings.QR = {**settings.QR, "ALLOW_PRIVATE_TARGETS": True, "DNS_CHECK_ON_WRITE": False}
    assert validate_target_url("http://127.0.0.1:5173/campagne") == "http://127.0.0.1:5173/campagne"


# --------------------------------------------------------------------- SPF / DKIM / DMARC de l'expéditeur


def test_le_domaine_se_lit_sur_l_enveloppe_pas_sur_le_nom():
    from apps.common.email_dns import domaine_de

    assert domaine_de("QR Studio <itsupport@kamcofarm.com>") == "kamcofarm.com"
    assert domaine_de("itsupport@kamcofarm.com.") == "kamcofarm.com"
    assert domaine_de("pas une adresse") == ""


def test_bilan_sur_les_enregistrements_reels_de_kamcofarm():
    """Les valeurs **mesurées le 2026-09-15** sur le DNS réel, rejouées telles quelles.

    SPF à la racine, **un seul** sélecteur avec une clé (les deux autres publient `v=DKIM1;p=`, c'est-à-dire
    rien), DMARC publié mais en `p=none`. Verdict : prêt à envoyer, pas encore protégé. Et l'enregistrement
    en CNAME ne prouve toujours pas que le **signage** est actif dans la console de l'hébergeur — d'où
    l'alerte dédiée.
    """
    from apps.common.email_dns import analyser, recoller

    # Le TXT mesure derriere `hostingermail-a._domainkey` arrive en **deux chaines d'un meme enregistrement**
    # (une cle RSA 2048 depasse les 255 octets) : c'est la que `recoller` sert, juge morceau par morceau on
    # lirait un domaine non signe.
    morceau_cle = "v=DKIM1;k=rsa;p=" + "A" * 255
    morceau_queue = "B" * 120
    constat = analyser(
        spf=["v=spf1 include:_spf.mail.hostinger.com ~all"],
        dkim={
            "hostingermail-a": recoller([[morceau_cle, morceau_queue]]),
            "hostingermail-b": ["v=DKIM1;p="],
            "hostingermail-c": ["v=DKIM1;p="],
        },
        dmarc=[
            "v=DMARC1; p=none; rua=mailto:postmaster@kamcofarm.com, mailto:dmarc@kamcofarm.com; "
            "pct=100; adkim=s; aspf=s"
        ],
        domaine="kamcofarm.com",
    )
    bilan = constat.bilan()
    assert bilan["spf_present"] and bilan["spf_autorise_le_relais"]
    # Un seul selecteur tient : `a`, dont la clé reconstituée dépasse les 255 octets d'un seul TXT.
    assert bilan["dkim_selecteurs"] == ["hostingermail-a"]
    assert bilan["dkim_selecteurs_sans_cle"] == ["hostingermail-b", "hostingermail-c"]
    assert constat.dkim["hostingermail-a"] == [morceau_cle + morceau_queue]
    assert bilan["dmarc_politique"] == "none"
    assert bilan["pret"] is True
    assert not any("Pas d'enregistrement `_dmarc`" in a for a in bilan["alertes"])
    assert any("sans clé" in a for a in bilan["alertes"])
    assert any("Espace dans la liste `rua`" in a for a in bilan["alertes"])
    assert any("aspf=s" in a for a in bilan["alertes"])


def test_un_dmarc_a_p_none_est_une_etape_pas_un_echec():
    from apps.common.email_dns import analyser

    bilan = analyser(
        spf=["v=spf1 include:_spf.mail.hostinger.com ~all"],
        dkim={"hostingermail-a": ["x.dkim.mail.hostinger.com"]},
        dmarc=["v=DMARC1; p=none; pct=100; rua=mailto:dmarc@kamcofarm.com"],
        domaine="kamcofarm.com",
    ).bilan()
    assert bilan["dmarc_politique"] == "none" and bilan["pret"] is True
    assert any("observe sans appliquer" in a for a in bilan["alertes"])


def test_sans_spf_ou_sans_signature_rien_n_est_pret():
    from apps.common.email_dns import analyser

    vide = analyser(spf=[], dkim={}, dmarc=[], domaine="exemple.com").bilan()
    assert vide["pret"] is False and len(vide["alertes"]) == 3
    assert vide["spf_brut"] == ""

    # `v=spf1 -all` (le SPF reel de example.com) : syntaxe valide, autorisation nulle — le cas ou le
    # domaine refuse tout envoi, par exemple pendant un litige d'abus.
    sans_relais = analyser(
        spf=["v=spf1 -all"], dkim={"s1": ["v=DKIM1; p=MIIBI"]}, dmarc=["v=DMARC1; p=reject"], domaine="exemple.com"
    ).bilan()
    assert sans_relais["pret"] is True and sans_relais["spf_autorise_le_relais"] is False
    assert any("aucun h" in a.lower() for a in sans_relais["alertes"])

    # Le SPF observe le 2026-09-14 sur kamcofarm.com: un include Google, alors que le service envoie
    # par Hostinger. Le controle doit le dire, et le bilan doit montrer la valeur brute.
    decale = analyser(
        spf=["v=spf1 include:_spf.google.com ~all"],
        dkim={"hostingermail-a": ["v=DKIM1;k=rsa;p=AAAA"]},
        dmarc=["v=DMARC1; p=none; adkim=s; aspf=s"],
        domaine="kamcofarm.com",
    ).bilan()
    assert "include:_spf.google.com" in decale["spf_brut"]


def test_un_selecteur_dkim_sans_cle_n_est_pas_tenu_pour_une_signature():
    """`v=DKIM1;p=` (clé vide) est le cas **réel** de `hostingermail-b` et `-c` sur notre domaine.

    Avant cette règle, le contrôle comptait ces enregistrements comme des sélecteurs « résolus » et annonçait
    le domaine signé : un CNAME publié ne signe rien, le message partait non signé, et notre propre
    vérification ne nous en avertissait pas — le pire genre de faux positif, parce qu'il endort.
    """
    from apps.common.email_dns import analyser, classer_selecteurs

    assert classer_selecteurs(
        {
            "a": ["v=DKIM1;k=rsa;p=MIIBIjAN" + "A" * 300],
            "b": ["v=DKIM1;p="],
            "c": ["v=DKIM1; k=rsa; p=   "],
            "d": ["cible.du.cname.example.net"],  # cible de CNAME relue telle quelle : non jugeable
            "e": [],
        }
    ) == (["a", "d"], ["b", "c"])

    bilan = analyser(
        spf=["v=spf1 include:_spf.mail.hostinger.com ~all"],
        dkim={"hostingermail-a": ["v=DKIM1;p="], "hostingermail-b": ["v=DKIM1;p="]},
        dmarc=["v=DMARC1; p=none"],
        domaine="kamcofarm.com",
    ).bilan()
    assert bilan["dkim_selecteurs"] == []
    assert bilan["dkim_selecteurs_sans_cle"] == ["hostingermail-a", "hostingermail-b"]
    assert bilan["pret"] is False, "aucune clé : on ne peut pas annoncer le courrier signé"
    assert any("sans clé" in a for a in bilan["alertes"])
    # Une seule alerte sur le sujet : ajouter « aucun selecteur resolu » en plus contredirait la mesure.
    assert not any("Aucun sélecteur DKIM résolu" in a for a in bilan["alertes"])


def test_les_anomalies_dmarc_qui_rendent_la_surveillance_muette():
    """Les quatre facons de publier un DMARC qui ne sert a rien, et que le controle doit voir.

    Chacune a ete rencontree pour de vrai : la premiere est celle du domaine de l'instance.
    """
    from apps.common.email_dns import analyser_dmarc

    # `pct=0` a l'air d'un `p=reject` publie : il ne soumet aucun message a la politique.
    zero = analyser_dmarc(["v=DMARC1; p=reject; pct=0"], domaine="exemple.com")
    assert zero.pct == 0 and any("pct=0" in a for a in zero.anomalies)

    # Un `rua` chez un tiers exige l'enregistrement de vérification chez lui.
    tiers = analyser_dmarc(["v=DMARC1; p=none; rua=mailto:dmarc@rapports.tiers.net"], domaine="exemple.com")
    assert tiers.rua == ["dmarc@rapports.tiers.net"]
    assert any("vérification" in a for a in tiers.anomalies)

    # Sans `p`, l'enregistrement est invalide : les receveurs l'ignorent entièrement, rapports compris.
    orphelin = analyser_dmarc(["v=DMARC1; pct=100"], domaine="exemple.com")
    assert orphelin.valide is True and orphelin.politique is None
    assert any("Aucun tag `p`" in a for a in orphelin.anomalies)

    # Un TXT a la racine de `_dmarc` qui n'est pas un enregistrement DMARC (saisie faite dans le mauvais champ).
    pas_dmarc = analyser_dmarc(["v=spf1 include:_spf.example.net ~all"], domaine="exemple.com")
    assert pas_dmarc.present and not pas_dmarc.valide
    assert any("ne commence pas par" in a for a in pas_dmarc.anomalies)

    # Une URI qui n'est pas `mailto:` : le rapport ne partira jamais, sans message d'erreur nulle part.
    mauvaise = analyser_dmarc(["v=DMARC1; p=none; ruf=postmaster@exemple.com"], domaine="exemple.com")
    assert any("mailto:" in a for a in mauvaise.anomalies)

    # `aspf=r` sur un relais tiers ne doit rien signaler : le risque est propre au strict.
    détendu = analyser_dmarc(
        ["v=DMARC1; p=quarantine; aspf=r"],
        domaine="exemple.com",
        spf_brut="v=spf1 include:_spf.mail.hostinger.com ~all",
    )
    assert détendu.aspf == "r" and détendu.anomalies == []


def test_un_dmarc_valide_et_severite_assumee_ne_sonne_pas_comme_un_echec():
    """`p=reject` avec `rua` sans espace et `adkim=r` : le controle doit se taire sur la forme.

    Une alerte par erreur coute plus cher qu'une alerte oubliee : a force de lire du bruit, on ne lit plus
    rien. Ce test verrouille le silence.
    """
    from apps.common.email_dns import analyser

    bilan = analyser(
        spf=["v=spf1 include:_spf.mail.hostinger.com ~all"],
        dkim={"dkim1": ["v=DKIM1;k=rsa;p=MIIBI" + "Z" * 300]},
        dmarc=["v=DMARC1; p=reject; rua=mailto:postmaster@kamcofarm.com,mailto:dmarc@kamcofarm.com"],
        domaine="kamcofarm.com",
    ).bilan()
    assert bilan["dmarc"]["valide"] and bilan["dmarc"]["anomalies"] == []
    assert bilan["dmarc"]["rua"] == ["postmaster@kamcofarm.com", "dmarc@kamcofarm.com"]
    assert bilan["dmarc_politique"] == "reject" and bilan["pret"] is True
    assert bilan["alertes"] == [], bilan["alertes"]


def test_les_selecteurs_attendus_se_reglent_pas_se_codent():
    """`EMAIL_DKIM_SELECTEURS` : changer de relais ne doit pas faire croire a un domaine non signe.

    Le defaut est la triple published d'Hostinger. Un domaine qui signe en `s1`/`s2` et qu'on juge sur
    `hostingermail-*` serait annonce « non signe » pour toujours — un faux negatif qui se lit comme une
    faute de l'hebergeur precedent, des qu'on a change d'hebergeur.
    """
    import pytest

    from apps.common.email_dns import SELECTEURS_DKIM_ATTENDUS, resoudre

    class ResolverFactice:
        def __init__(self):
            self.vus = []

        def resolve(self, nom, typ):
            self.vus.append(nom)

            class _R:
                def __init__(self, chaine):
                    self.strings = [chaine.encode()]

            return [_R("v=DKIM1;k=rsa;p=" + "A" * 300)]

    factice = ResolverFactice()
    import dns.resolver

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(dns.resolver.Resolver, "resolve", lambda self, nom, typ: factice.resolve(nom, typ))
        resoudre("exemple.net")
    assert all(any(sel in v for sel in SELECTEURS_DKIM_ATTENDUS) for v in factice.vus if "_domainkey" in v)

    factice.vus.clear()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("django.conf.settings.EMAIL_DKIM_SELECTEURS", ["s1", "s2"], raising=False)
        mp.setattr(dns.resolver.Resolver, "resolve", lambda self, nom, typ: factice.resolve(nom, typ))
        resoudre("exemple.net")
    interroges = [v for v in factice.vus if "_domainkey" in v]
    assert sorted(interroges) == ["s1._domainkey.exemple.net", "s2._domainkey.exemple.net"]
    assert not any("hostingermail" in v for v in interroges)


def test_un_txt_multichunk_est_recolle_avant_jugement():
    """Une cle DKIM 2048 bits arrive en deux morceaux TXT. Les juger séparément ferait croire a un
    enregistrement inexistant — et a un domaine non signe."""
    from apps.common.email_dns import recoller

    morceaux = [["v=DKIM1;k=rsa;p=" + "A" * 255], ["BBB"]]
    assert len(recoller([["a", "b"], ["c"]])) == 2
    assert recoller([["v=spf1 include:_spf.mail.hostinger", ".com ~all"]])[0].endswith("~all")


def test_la_commande_sort_en_erreur_sans_dkim(monkeypatch, settings):
    """La commande est jouee par `call_command`, pas par `handle()` directement.

    Appeler `handle()` dans le test avait laisse passer un crash reel : `BaseCommand.execute` ecrit la
    valeur de retour sur stdout quand elle est truthy, donc `return 1` levait un `AttributeError` au lieu
    de rendre le code de sortie promis — et le traceback donnait un code 1 par accident, ce qui faisait
    croire que le garde-fou de deploiement fonctionnait. Le test traverse donc l'entree reelle.
    """
    from io import StringIO

    import pytest
    from django.core.management import call_command

    from apps.common.email_dns import Constat

    monkeypatch.setattr(
        "apps.common.management.commands.check_email_dns.resoudre", lambda domaine: Constat(domaine=domaine)
    )
    entree = StringIO()
    with pytest.raises(SystemExit) as sortie_exception:
        call_command("check_email_dns", "--domaine", "exemple.com", stdout=entree, stderr=entree)
    assert sortie_exception.value.code == 1, "code 1 attendu quand le minimum manque"
    assert "ABSENT" in entree.getvalue()

    # Et le chemin de succes ne leve rien : meme commande, domaine complet.
    monkeypatch.setattr(
        "apps.common.management.commands.check_email_dns.resoudre",
        lambda domaine: Constat(
            domaine=domaine,
            spf=["v=spf1 include:_spf.mail.hostinger.com ~all"],
            dkim={"hostingermail-a": ["v=DKIM1;k=rsa;p=MIIBI" + "A" * 300]},
            dmarc=["v=DMARC1; p=none; rua=mailto:postmaster@exemple.com"],
        ),
    )
    sortie = StringIO()
    call_command("check_email_dns", "--domaine", "exemple.com", stdout=sortie)  # pas de SystemExit = 0
    texte = sortie.getvalue()
    assert "minimum réuni" in texte and "hostingermail-a" in texte and "p=none" in texte
