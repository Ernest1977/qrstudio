"""Les requêtes du back-office d'analytique — séparées de l'API client pour une raison de périmètre.

L'API (`/analytics/qr/<id>`) ne sait lire que **par QR**, parce que c'est ce qu'un client est autorisé à
voir. L'espace admin doit répondre à « combien de scans hier, tous clients confondus, et combien viennent
de nos propres QR de démonstration » : ces agrégats-là n'existent que dans `PlatformDailyStats`, et ils ne
doivent surtout pas devenir consultables par un compte payant. D'où ce module, appelé uniquement par
`apps/common/views_backoffice.py`.

Règle de performance appliquée ici : le chemin non filtré lit la table agrégée (une ligne par jour,
indépendante du nombre de clients). Dès qu'un filtre porteur (`kind`, `origine`) est posé, on redescend
dans `QrDailyStats` **avec une sous-requête SQL** sur `QrCode` — un seul aller-retour, et l'index
`qr_kind_active_idx` porte le filtrage. C'est le prix du filtre, il est écrit là pour qu'on ne le découvre
pas en production.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.db.models import Count, Q, Sum

from apps.analytics.aggregates import (
    COUNTRY_NAMES,
    repartition_pays,
    serie_plateforme,
    totaux_plateforme,
)

JOURS_DISPONIBLES = (7, 30, 90)


def periode(jours: int) -> tuple[date, date]:
    jours = jours if jours in JOURS_DISPONIBLES else 30
    until = date.today()
    return until - timedelta(days=jours - 1), until


def _filtre_qr(kind: str, origine: str) -> Q:
    conditions = Q()
    if kind in {"static", "dynamic"}:
        conditions &= Q(kind=kind)
    if origine in {"client", "admin", "import"}:
        conditions &= Q(origine=origine)
    return conditions


def serie(*, since: date, until: date, kind: str = "", origine: str = "") -> list[dict[str, Any]]:
    """Série journalière, avec ou sans filtre. Les trous sont comblés à zéro dans les deux cas."""
    if not (kind or origine):
        return serie_plateforme(since=since, until=until)

    from apps.qr.models import QrCode

    ids = QrCode.objects.filter(_filtre_qr(kind, origine)).values("pk")
    lignes = (
        _modele_stats()
        .filter(qr_id__in=ids, day__gte=since, day__lte=until)
        .values("day")
        .annotate(
            scans=Sum("scans"),
            uniques=Sum("unique_visitors"),
            mobile=Sum("mobile"),
            desktop=Sum("desktop"),
        )
        .order_by("day")
    )
    par_jour = {ligne["day"]: ligne for ligne in lignes}
    serie_vers = []
    jour = since
    while jour <= until:
        ligne = par_jour.get(jour)
        serie_vers.append(
            {
                "day": jour.isoformat(),
                "scans": (ligne or {}).get("scans") or 0,
                "unique": (ligne or {}).get("uniques") or 0,
                "mobile": (ligne or {}).get("mobile") or 0,
                "desktop": (ligne or {}).get("desktop") or 0,
                "dynamique": 0,
                "statique": 0,
                "admin": 0,
                "bots": 0,
                "qr_actifs": 0,
            }
        )
        jour += timedelta(days=1)
    return serie_vers


def _modele_stats():
    from apps.analytics.models import QrDailyStats

    return QrDailyStats.objects.all()


def totaux(*, since: date, until: date, kind: str = "", origine: str = "") -> dict[str, int]:
    if not (kind or origine):
        return totaux_plateforme(since=since, until=until)
    from apps.qr.models import QrCode

    agregats = (
        _modele_stats()
        .filter(qr_id__in=QrCode.objects.filter(_filtre_qr(kind, origine)).values("pk"), day__gte=since, day__lte=until)
        .aggregate(
            scans=Sum("scans"),
            visiteurs_uniques=Sum("unique_visitors"),
            mobile=Sum("mobile"),
            desktop=Sum("desktop"),
            tablette=Sum("tablet"),
            bots=Sum("bot_blocked"),
        )
    )
    resultat = {cle: valeur or 0 for cle, valeur in agregats.items()}
    resultat.setdefault("statiques", 0)
    resultat.setdefault("dynamiques", 0)
    resultat.setdefault("admin", 0)
    return resultat


def pays(*, since: date, until: date, kind: str = "", origine: str = "", limit: int = 10) -> list[dict[str, Any]]:
    if not (kind or origine):
        return repartition_pays(since=since, until=until, limit=limit)
    from apps.analytics.models import QrDailyStats
    from apps.qr.models import QrCode

    lignes = (
        QrDailyStats.objects.filter(
            qr_id__in=QrCode.objects.filter(_filtre_qr(kind, origine)).values("pk"), day__gte=since, day__lte=until
        )
        .exclude(country_code="")
        .values("country_code")
        .annotate(scans=Sum("scans"), visiteurs=Sum("unique_visitors"))
        .order_by("-scans")[:limit]
    )
    return [
        {
            "code": ligne["country_code"],
            "nom": COUNTRY_NAMES.get(ligne["country_code"], ligne["country_code"]),
            "scans": ligne["scans"],
            "visiteurs": ligne["visiteurs"],
        }
        for ligne in lignes
    ]


def top_qrs(*, since: date, until: date, limit: int = 20, kind: str = "", origine: str = "") -> list[dict[str, Any]]:
    """Les QR qui portent le volume, avec leur propriétaire et leur provenance.

    On agrège d'abord par `qr_id` (borné par `limit` une fois trié), puis on rattrape les libellés en une
    requête : l'inverse — JOIN + groupage sur les deux tables — ferait porter le tri à `QrCode`.
    """
    from apps.analytics.models import QrDailyStats
    from apps.qr.models import QrCode

    base = QrDailyStats.objects.filter(day__gte=since, day__lte=until)
    if kind or origine:
        base = base.filter(qr_id__in=QrCode.objects.filter(_filtre_qr(kind, origine)).values("pk"))
    rangs = list(
        base.values("qr_id").annotate(scans=Sum("scans"), visiteurs=Sum("unique_visitors")).order_by("-scans")[:limit]
    )
    if not rangs:
        return []
    meta = {
        qr["pk"]: qr
        for qr in QrCode.objects.filter(pk__in=[r["qr_id"] for r in rangs]).values(
            "pk", "label", "slug", "kind", "origine", "owner__email", "is_active"
        )
    }
    hors = [r["qr_id"] for r in rangs if r["qr_id"] not in meta]  # QR effaces depuis : ligne orpheline
    resultat = []
    for rang in rangs:
        qr = meta.get(rang["qr_id"])
        if qr is None:
            continue
        resultat.append(
            {
                "id": rang["qr_id"],
                "label": qr["label"] or qr["slug"],
                "slug": qr["slug"],
                "kind": qr["kind"],
                "origine": qr["origine"],
                "owner": qr["owner__email"],
                "actif": qr["is_active"],
                "scans": rang["scans"],
                "visiteurs": rang["visiteurs"],
            }
        )
    if hors:
        resultat.append(
            {
                "id": None,
                "label": f"{len(hors)} QR supprimes (lignes d'agregat orphelines)",
                "slug": "",
                "kind": "",
                "origine": "",
                "owner": "",
                "actif": False,
                "scans": sum(r["scans"] for r in rangs if r["qr_id"] in hors),
                "visiteurs": sum(r["visiteurs"] for r in rangs if r["qr_id"] in hors),
            }
        )
    return resultat


def volumetrie_creations(*, since: date, until: date) -> dict[str, Any]:
    """Ce qui a été créé sur la période, séparé par nature et par provenance.

    C'est la vue qui répond à « l'espace admin gratuit est-il un trou dans les revenus » : le nombre de QR
    `admin` créés, et combien de comptes clients ont été touchés par la même exemption.
    """
    from apps.qr.models import QrCode

    base = QrCode.objects.filter(created_at__date__gte=since, created_at__date__lte=until)
    par_cle = dict(base.values("kind").annotate(n=Count("id")).values_list("kind", "n"))
    par_origine = dict(base.values("origine").annotate(n=Count("id")).values_list("origine", "n"))
    comptes = base.values("origine", "owner").annotate(n=Count("id"))
    comptes_admin = {ligne["owner"] for ligne in comptes if ligne["origine"] == "admin"}
    return {
        "total": sum(par_cle.values()),
        "statiques": par_cle.get("static", 0),
        "dynamiques": par_cle.get("dynamic", 0),
        "admin": par_origine.get("admin", 0),
        "client": par_origine.get("client", 0),
        "import": par_origine.get("import", 0),
        "comptes_admin": len(comptes_admin),
        # Un QR n'est jamais supprime physiquement sur la periode : on suit aussi les archives, sinon le
        # solde « cree - archive » paraitrait etre du churn la ou il n'y a que du menage.
        "archives": base.filter(archived_at__isnull=False).count(),
    }


def par_qr(qr) -> dict[str, Any]:
    """Le drill-down d'un QR : ce que voit le client, plus ce que seul l'admin a le droit d'ajouter."""
    from apps.analytics.aggregates import country_breakdown, daily_series, totals_for
    from apps.qr.models import QrVersion

    since, until = periode(90)
    # `totals_for` est le cumul tout horizon : c'est ce qu'un administrateur veut en tete d'un drill-down ;
    # la periode filtree est, elle, dans `serie` et `pays`.
    totaux_qr = totals_for(qr_id=qr.pk)
    return {
        "since": since,
        "until": until,
        "serie": daily_series(qr_id=qr.pk, since=since, until=until),
        # `country_breakdown` repond `name` (contrat de l'API client, gelé) ; les gabarits du back-office
        # parlent `nom` comme `pays()` ci-dessus. La traduction se fait ici, pas dans chaque template.
        "pays": [
            {"code": ligne["code"], "nom": ligne["name"], "scans": ligne["scans"], "visiteurs": ligne["scans"]}
            for ligne in country_breakdown(qr_id=qr.pk, since=since, limit=10)
        ],
        "totaux": totaux_qr,
        "versions": list(
            QrVersion.objects.filter(qr=qr)
            .select_related("actor")
            .order_by("-created_at")[:12]
            .values("created_at", "actor__email", "change")
        ),
        "scan_total": qr.scan_count_total,
    }
