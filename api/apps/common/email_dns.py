"""Contrôle DNS de l'expéditeur : ce qui fait la différence entre « envoyé » et « reçu ».

Un e-mail de vérification qui part et n'arrive jamais est, dans cette ordre de fréquence : pas de
SPF sur le domaine d'envoi, DKIM signé sur un autre domaine que celui du `From`, DMARC absent sur un
domaine qui envoie du transactionnel (les filtres le traitent alors plus sévèrement, pas moins), ou
le serveur qui envoie n'est pas celui que le SPF autorise.

La commande `check_email_dns` fournit le verdict ; ce module ne fait que **juger** des enregistrements
qu'on lui passe, ce qui le rend testable sans réseau — et utilisable depuis n'importe quel résolveur.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from django.conf import settings

#: Defaut, surchargeable par `settings.EMAIL_DKIM_SELECTEURS` : la liste des selecteurs a ne pas durcir en
#: constante, sinon changer de relais transforme un domaine bien regle en « non signe » a chaque controle.
SELECTEURS_DKIM_ATTENDUS = ("hostingermail-a", "hostingermail-b", "hostingermail-c")


def domaine_de(expediteur: str) -> str:
    """`QR Studio <itsupport@kamcofarm.com>` -> `kamcofarm.com` (et non l'angle du nom d'affichage)."""
    brut = (expediteur or "").strip()
    if "<" in brut:
        brut = brut[brut.index("<") + 1 :].rstrip(">")
    if "@" not in brut:
        return ""  # pas d'arobase, pas de domaine : `DEFAULT_FROM_EMAIL` est mal forme, on le dit
    _, _, domaine = brut.rpartition("@")
    return domaine.strip().lower().removesuffix(".")


@dataclass
class Constat:
    domaine: str
    spf: list[str] = field(default_factory=list)
    dkim: dict[str, list[str]] = field(default_factory=dict)
    dmarc: list[str] = field(default_factory=list)

    def bilan(self) -> dict:
        """Verdict lisible, avec la réparation quand elle manque.

        Chaque critère est évalué séparément : un `p=none` est un choix, pas une erreur, et le dire
        comme tel évite la réponse « tant que ce n'est pas reject, ça ne marche pas ».
        """
        alertes: list[str] = []
        spf_ok = any(e.startswith("v=spf1") for e in self.spf)
        if not spf_ok:
            alertes.append(
                "Aucun enregistrement SPF (`v=spf1`) à la racine du domaine : les destinataires "
                "rejettent ou classent en spam une large part du courrier transactionnel."
            )
        spf_brut = " ".join(self.spf)
        # On ne devine pas « un relais est autorise »: on compare au relais que le service utilise
        # réellement (`EMAIL_HOST`). Sans ca, un `v=spf1 -all` passerait pour correct des qu'il contient
        # le mot `include:`, et un domaine qui autorise Google alors qu'on envoie par un autre
        # hebergeur — l'erreur la plus fréquente de toutes — ne serait jamais signale.
        autorise = False
        if spf_ok:
            meca = re.search(r"\b(include:|ip4:|ip6:|a\b|mx\b|redirect=)", spf_brut, re.I)
            autorise = bool(meca) and not re.search(r"v=spf1\s*(-all|\?all)\s*$", spf_brut.strip(), re.I)
        relais = (getattr(settings, "EMAIL_HOST", "") or "").lower()
        if spf_ok and relais and autorise:
            # Le relais n'a pas a apparaitre textuellement (l'`include:` pointe une zone gernee par
            # l'hebergeur) : on ne signale que l'absence totale d'indice vers lui, en demandant
            # verification, sans affirmer un echec qu'on ne peut pas prouver depuis un TXT.
            indice = relais.split(".")[0].removesuffix("smtp") if "." in relais else relais
            if indice and indice not in spf_brut.lower() and "include:" not in spf_brut.lower():
                alertes.append(
                    f"Le SPF n'contient aucun `include:` alors que le service envoie via {relais} : "
                    "à vérifier à la source."
                )
        if spf_ok and not autorise:
            alertes.append(
                f"Le SPF publié n'autorise aucun hôte d'envoi (`{spf_brut[:80]}`) : le contrôle SPF "
                "échouera pour tout message sortant."
            )
        selecteurs, selecteurs_vides = classer_selecteurs(self.dkim)
        if selecteurs_vides:
            alertes.append(
                "Des sélecteurs DKIM publient `v=DKIM1;p=` **sans clé** ("
                + ", ".join(selecteurs_vides)
                + ") : l'enregistrement DNS est là, la signature non. Activez le DKIM pour ces sélecteurs "
                "dans la console de l'hébergeur, ou retirez-les."
            )
        if not selecteurs and not selecteurs_vides:
            alertes.append(
                "Aucun sélecteur DKIM résolu : les messages partiront non signés. Vérifiez aussi que "
                "la signature est bien activée dans la console de messagerie — l'enregistrement DNS "
                "seul ne signe rien."
            )
        elif not selecteurs:
            # Tous les selecteurs publient un enregistrement sans cle : l'alerte ci-dessus le dit deja, et
            # ajouter « aucun selecteur resolu » juste en dessous contredit la mesure (elle, est bien passee).
            pass
        dmarc = analyser_dmarc(self.dmarc, domaine=self.domaine, spf_brut=spf_brut)
        politique = dmarc.politique or ""
        alertes.extend(dmarc.anomalies)
        if not self.dmarc:
            alertes.append(
                "Pas d'enregistrement `_dmarc` : recommande, surtout quand le domaine sert d'expéditeur "
                "à un service. Commencez par `p=none` pour observer, puis durcissez."
            )
        elif politique == "none":
            alertes.append("`p=none` : le DMARC observe sans appliquer. Étape correcte au début, à clore.")
        return {
            "domaine": self.domaine,
            "spf_brut": spf_brut if spf_ok else "",
            "spf_present": spf_ok,
            "spf_autorise_le_relais": autorise,
            "dkim_selecteurs": selecteurs,
            "dkim_selecteurs_sans_cle": selecteurs_vides,
            "dmarc_politique": politique or None,
            "dmarc": asdict(dmarc),
            "pret": spf_ok and bool(selecteurs),
            "alertes": alertes,
        }


def classer_selecteurs(dkim: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """Sépare les sélecteurs exploitables de ceux qui publient une **clé vide**.

    Mesure réelle sur le domaine de l'instance (2026-09-15) : `hostingermail-a` porte la clé RSA 2048,
    `hostingermail-b` et `-c` répondent `v=DKIM1;p=` — un enregistrement publié, sans clé. Compter ceux-là
    comme des sélecteurs « résolus » faisait passer le contrôle pour vert alors qu'un `_domainkey` sans clé
    ne signe rien : le message part non signé et le destinataire ne dit pas pourquoi.

    Un enregistrement qui ne ressemble pas à une clé (`v=DKIM1` absent — typiquement la cible d'un CNAME
    récupérée telle quelle) n'est **pas** jugé : on ne peut pas affirmer une absence depuis une chaîne qu'on
    n'a pas lue jusqu'au TXT. Il est donc compté comme exploitable, sans éloge.
    """
    exploitables: list[str] = []
    vides: list[str] = []
    for selecteur, valeurs in dkim.items():
        brut = " ".join(valeurs).strip()
        if not brut:
            continue
        if re.search(r"\bv=DKIM1\b", brut, re.I):
            cle = re.search(r"\bp=([^;]*)", brut, re.I)
            if cle and cle.group(1).strip():
                exploitables.append(selecteur)
            else:
                vides.append(selecteur)
        else:
            exploitables.append(selecteur)
    return exploitables, vides


def _sous_domaine(candidat: str, racine: str) -> bool:
    return candidat == racine or candidat.endswith(f".{racine}")


def _include_tiers(spf_brut: str, domaine: str) -> bool:
    """Le SPF publié délègue-t-il à un domaine qui n'est pas le nôtre (relais mutualisé) ?"""
    cibles = re.findall(r"include:([^\s]+)", spf_brut or "", re.I)
    if not cibles:
        return False
    return not any(_sous_domaine(cible.lower().removesuffix("."), domaine) for cible in cibles)


@dataclass
class PolitiqueDmarc:
    """Un enregistrement `_dmarc` jugé, pas seulement lu.

    Une dataclass plutot qu'un dict : les champs sont fixes, `bilan()` le serialise avec `asdict`, et un
    `dict[str, str | list | bool | None]` ne se verifie pas — mon premier jet rendait 38 erreurs de typage,
    ce qui est exactement le signal qu'une cle pouvait recevoir une liste selon l'endroit.
    """

    present: bool = False
    valide: bool = False
    politique: str | None = None
    rua: list[str] = field(default_factory=list)
    ruf: list[str] = field(default_factory=list)
    aspf: str = "r"
    adkim: str = "r"
    pct: int | None = None
    sp: str | None = None
    anomalies: list[str] = field(default_factory=list)


def analyser_dmarc(enregistrements: list[str], *, domaine: str, spf_brut: str = "") -> PolitiqueDmarc:
    """Lit un enregistrement DMARC sans rien inventer, et signale ce qui rend les rapports muets.

    Le but n'est pas de réécrire un parseur de RFC : c'est de ne pas laisser passer les trois erreurs qui
    font croire qu'une surveillance DMARC fonctionne. Une liste `rua` séparée par des virgules suivies d'un
    espace — des receveurs rejettent alors la liste entière, et vous n'avez jamais de rapport, sans que
    rien ne sonne. Une adresse de rapport chez un tiers sans l'enregistrement de vérification chez lui.
    Un `pct=0`, qui soumet zéro trafic à la politique tout en ayant l'air d'un `p=reject` actif.
    """
    detail = PolitiqueDmarc(present=bool(enregistrements))
    if not enregistrements:
        return detail

    brut = " ".join(enregistrements).strip()
    if not re.match(r"^v\s*=\s*DMARC1\b", brut, re.I):
        detail.anomalies.append(
            "L'enregistrement `_dmarc` ne commence pas par `v=DMARC1;` : il est ignoré par les receveurs."
        )
        return detail

    tags: dict[str, str] = {}
    for morceau in brut.split(";")[1:]:
        if not morceau.strip():
            continue
        cle, sep, valeur = morceau.partition("=")
        if not sep:
            detail.anomalies.append(f"Morceau sans `=` dans l'enregistrement DMARC : `{morceau.strip()[:40]}`.")
            continue
        tags[cle.strip().lower()] = valeur

    detail.valide = True
    politique = (tags.get("p") or "").strip().lower()
    if politique not in {"none", "quarantine", "reject"}:
        detail.anomalies.append(
            "Aucun tag `p` valable (`none`, `quarantine`, `reject`) : sans politique l'enregistrement est "
            "invalide, et les receveurs l'ignorent entièrement — y compris les rapports que vous attendez."
        )
    detail.politique = politique or None

    for champ in ("rua", "ruf"):
        liste = tags.get(champ)
        if liste is None:
            continue
        for uri_brute in liste.split(","):
            if uri_brute != uri_brute.strip():
                detail.anomalies.append(
                    f"Espace dans la liste `{champ}` : écrivez `{champ}=mailto:a@x,mailto:b@y` sans espace "
                    "après la virgule. Toléré par l'ABNF de la RFC 7489, mais des receveurs découpent sur "
                    "`,` et rejettent alors l'URI suivante — le rapport parti, plus personne ne sonne."
                )
            uri = uri_brute.strip()
            if not uri.lower().startswith("mailto:"):
                detail.anomalies.append(f"`{champ}` doit être une URI `mailto:` (reçu : `{uri[:40]}`).")
                continue
            adresse = uri[len("mailto:") :].strip()
            getattr(detail, champ).append(adresse)
            _, _, domaine_rapport = adresse.rpartition("@")
            domaine_rapport = domaine_rapport.lower().removesuffix(".")
            if domaine and domaine_rapport and not _sous_domaine(domaine_rapport, domaine):
                detail.anomalies.append(
                    f"`{champ}` pointe une adresse d'un autre domaine ({domaine_rapport}) : ce receveur "
                    "n'enverra les rapports qu'après avoir lu l'enregistrement de vérification publié chez lui."
                )

    for tag in ("aspf", "adkim"):
        valeur = (tags.get(tag) or "r").strip().lower()
        if valeur not in {"r", "s"}:
            detail.anomalies.append(f"`{tag}={valeur}` n'est pas une valeur valable (`r` ou `s`).")
            valeur = "r"
        setattr(detail, tag, valeur)

    if (pct_brut := tags.get("pct")) is not None:
        try:
            pct = int(pct_brut.strip())
        except ValueError:
            detail.anomalies.append(f"`pct` n'est pas un entier : `{pct_brut.strip()[:12]}`.")
            pct = None
        if pct is not None and not 0 <= pct <= 100:
            detail.anomalies.append(f"`pct={pct}` hors de l'intervalle 0-100 : l'enregistrement est invalide.")
            pct = None
        elif pct == 0:
            detail.anomalies.append(
                "`pct=0` : la politique ne s'applique à aucun message. Surveillé sans être appliqué — le "
                "piège est de lire plus tard « p=reject publié » sans se souvenir de ce zéro."
            )
        detail.pct = pct

    if (sp := tags.get("sp")) is not None:
        detail.sp = sp.strip().lower()

    # `aspf=s` est la source de faux positifs la plus fréquente sur un relais mutualisé : l'alignement SPF
    # se juge sur le Return-Path, que l'hébergeur remplace par le sien. `adkim=s` exige de son côté que `d=`
    # de la signature soit exactement le domaine. Aucun des deux ne se vérifie depuis un TXT : ce qu'on peut
    # établir, c'est que le SPF publié délègue à un tiers, donc que la sévérité mérite un regard.
    if detail.aspf == "s" and domaine and _include_tiers(spf_brut, domaine):
        detail.anomalies.append(
            "`aspf=s` (strict) avec un SPF qui `include:` un domaine tiers : le Return-Path est "
            "vraisemblablement celui du relais, donc l'alignement SPF échouera même quand le contrôle SPF "
            "passe. Les rapports le diront (`spf=fail (alignment mismatch)`) : ne durcissez pas avant "
            "d'avoir vu `dkim=pass` aligné sur le volume qui compte."
        )
    return detail


def recoller(enregistrements: list[list[str]]) -> list[str]:
    """Un enregistrement TXT de plus de 255 octets est renvoye **en morceaux** par les resolvers.

    C'est le cas de toute cle DKIM RSA 2048 (et de certains SPF longs). Juger morceau par morceau
    ferait croire a un enregistrement absent ou malforme : les morceaux d'un meme enregistrement sont
    d'abord reunis.
    """
    return ["".join(morceaux) for morceaux in enregistrements if morceaux]


def analyser(spf: list[str], dkim: dict[str, list[str]], dmarc: list[str], *, domaine: str) -> Constat:
    return Constat(domaine=domaine, spf=list(spf), dkim={k: list(v) for k, v in dkim.items()}, dmarc=list(dmarc))


def resoudre(domaine: str) -> Constat:
    """Lit le DNS réel. Nécessite `dnspython` (déjà requis pour cette commande) ; sinon, erreur claire.

    Le choix d'une dépendance pour *un* contrôle de configuration se justifie par ce qu'il évite :
    sans lecture de TXT, le déploiement d'un service qui envoie des codes de vérification repose sur
    l'espoir que le SPF est bon, et l'échec est silencieux côté client.
    """
    try:
        import dns.resolver
    except ImportError as exc:  # pragma: no cover - environnement sans résolveur
        raise RuntimeError(
            "Cette commande a besoin de `dnspython` (pip install dnspython) ou doit être jouée sur le "
            "VPS, où `dig +short TXT <domaine>` donne la même lecture."
        ) from exc

    resolver = dns.resolver.Resolver()
    resolver.lifetime = resolver.timeout = 6.0

    def _txt(nom: str) -> list[str]:
        try:
            return recoller([[b.decode("utf-8", "replace") for b in r.strings] for r in resolver.resolve(nom, "TXT")])
        except Exception:  # noqa: BLE001 - NXDOMAIN, timeout, SERVFAIL: l'absence est la réponse
            return []

    selecteurs = getattr(settings, "EMAIL_DKIM_SELECTEURS", None) or list(SELECTEURS_DKIM_ATTENDUS)
    dkim = {selecteur: _txt(f"{selecteur}._domainkey.{domaine}") for selecteur in selecteurs}
    # Les hébergeurs publient parfois le DKIM en CNAME vers leur zone : le TXT suit la cible.
    return analyser(_txt(domaine), dkim, _txt(f"_dmarc.{domaine}"), domaine=domaine)
