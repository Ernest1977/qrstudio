"""`GET /r/<slug>` — le seul endpoint public, et le seul qui porte la charge du produit.

Trois invariants, chacun testé :

1. **la redirection ne dépend pas de l'ingestion** : un `XADD` qui échoue est loggué, le 302 part
   quand même ; le scan sera reconstitué depuis l'accès Caddy sinon (journal hors bande).
2. **pas de session, pas de CSRF, pas de cookie** : `del request.session` serait pire qu'inutile, on
   ne les demande simplement pas (la vue est `@csrf_exempt` et n'utilise pas `AuthenticationMiddleware`).
3. **une panne de cache ne doit pas transformer le site en 500** : `read_async` renvoie `None` sur
   exception, et la vue relit la base. Inversement, une panne de base avec un cache chaud continue de
   servir — c'est exactement le comportement qu'on veut un dimanche de pic.
"""

from __future__ import annotations

import logging
import time

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponsePermanentRedirect, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.common.shortid import slug_is_safe
from apps.qr import cache as qr_cache

logger = logging.getLogger(__name__)

STATUS_GONE_TITLE = "Lien indisponible"


def _ttl_for_negative() -> int:
    return 10


@csrf_exempt
@require_http_methods(["GET", "HEAD"])
async def scan_redirect(request: HttpRequest, slug: str) -> HttpResponse:
    """Redirige vers la destination enregistrée, en comptant le scan hors bande."""
    started = time.monotonic()
    if not slug_is_safe(slug):
        # Format invalide : on ne touche ni cache ni base (un bot qui balance des payloads ne doit
        # pas pouvoir transformer notre requêteur en DDoS interne).
        return _unavailable(status=404, reason="not_found")

    entry = await qr_cache.read_async(slug)
    cache_hit = entry is not None
    if entry is None:
        from asgiref.sync import sync_to_async

        # `thread_sensitive=True` : l'accès ORM est sérialisé sur le thread de l'événement, ce qui
        # évite deux surprises — le partage de connexion SQLite en test, et un pool psycopg saturé par
        # des threads d'exécuteurs non bornés sous forte charge. Le coût est une attente, pas une requête.
        entry = await sync_to_async(_lookup)(slug)
        await qr_cache.write_async(slug, entry if entry is not None else qr_cache.negative_entry())
    if entry is None or entry.get("missing"):
        return _unavailable(status=410, reason="gone")
    if not entry.get("is_active") or not entry.get("target_url"):
        return _unavailable(status=410, reason="paused")

    # Un scanner de liens envoie `HEAD` : il doit voir la ressource vivre, sans polluer les stats.
    if request.method == "HEAD" or request.GET.get("_probe"):
        response = HttpResponse(status=200)
        _decorate(response, cache_hit=cache_hit)
        return response

    if await _rate_limited(request, slug):
        response = JsonResponse(
            {"error": {"code": "too_many_scans", "message": "Trop de sollicitations depuis votre adresse."}},
            status=429,
        )
        response["Retry-After"] = "30"
        _decorate(response, cache_hit=cache_hit)
        return response

    from apps.common import streams

    # Une offre attachée au QR se verifie **ici**, a partir de l'entree de cache : aucun JOIN, aucune
    # ecriture SQL. Un code qui a rendu est une page, pas une redirection — rediriger vers une page
    # produit avec un code mort reviendrait a laisser le client decouvrir l'erreur au comptoir.
    offre, _explicite = _offre_presentee(request, entry)
    if offre is not None:
        motif = _motif_promo(entry, offre)
        if motif:
            # « Offre terminee » est une **page**, pas une redirection : un 302 vers la page normale
            # ferait croire au client que la remise s'applique, et le differend n'eclaterait qu'au
            # comptoir. Le scan est compte (status 410) : une campagne qui meure est une donnee utile.
            await streams.push_scan(_scan_event(request, entry, status=410))
            response = render(
                request,
                "qr/promo.html",
                {
                    "title": "Offre terminée",
                    "code": offre["code"],
                    "libelle": offre.get("libelle") or "",
                    "motif": motif,
                    "destination": entry["target_url"],
                    "qr_id": entry.get("qr_id"),
                },
                status=200,
            )
            _decorate(response, cache_hit=cache_hit, promo=motif)
            return response

    # `await` mais tolérant : `push_scan` avale ses propres exceptions (contrat testé).
    await streams.push_scan(_scan_event(request, entry))

    status_code = int(entry.get("redirect_mode") or 302)
    destination = _destination(entry, offre)
    if status_code == 301:
        response = HttpResponsePermanentRedirect(destination)
    else:
        response = HttpResponse(status=status_code if status_code in {302, 303, 307} else 302)
        response["Location"] = destination
    _decorate(response, cache_hit=cache_hit)
    response["X-Scan-Duration-Ms"] = f"{(time.monotonic() - started) * 1000:.2f}"
    return response


def _offre_presentee(request: HttpRequest, entry: dict) -> tuple[dict | None, bool]:
    """`(offre, code_demande_explicitement)` — l'offre concernee par ce scan.

    Deux cas, un seul qui compte : le visiteur **presente** un code (alors c'est celui-la, et aucun
    autre), ou le flyer porte une offre unique (alors on l'applique a tout scan sur ce QR).

    Le code arrive par `?promo=` parce que c'est **lui** que le commerçant imprime (un lien court differents
    par segment de clients, meme QR de destination). Sans parametre, on retombe sur l'offre attachee au QR :
    la campagne « une impression, une offre » reste le cas le plus frequent et ne doit rien exiger de plus.
    """
    from apps.qr import promo as promo_rules

    codes = entry.get("codes_promo") or {}
    demande = promo_rules.cle_normalisee(request.GET.get("promo") or "")
    if demande:
        # Un code inconnu n'est pas une erreur a signaler au visiteur : c'est le scan normal, et un 404
        # apprendrait aux scalibres quels codes existent. Mais l'offre du flyer ne s'applique pas
        # par-dessus : qui presente un code veut ce code-la, pas la remise du comptoir.
        return codes.get(demande), True
    return entry.get("promo"), False


def _motif_promo(entry: dict, offre: dict) -> str:
    """`expire` | `epuise` | `inactif`, ou chaine vide si l'offre est bonne.

    Regroupe les trois verdicts pour que la redirection n'ait qu'une decision a prendre ; la logique reste
    dans `apps.qr.promo.statut` cote ORM et est reproduite ici sur l'entree de cache, a l'identique.
    """
    from django.utils import timezone

    maintenant = int(timezone.now().timestamp())
    if offre.get("expire_ts") and maintenant > int(offre["expire_ts"]):
        return "expire"
    plafond = offre.get("usages_max")
    if plafond:
        from apps.qr import promo as promo_rules

        consommes = int(offre.get("usages") or 0) + promo_rules.compteurs(int(offre["id"])).get(int(offre["id"]), 0)
        if consommes >= int(plafond):
            return "epuise"
    return ""


def _promo_expiree(entry: dict) -> dict | None:  # conserve pour les appels existants (tests, back-office)
    """Ancienne entree unique : l'offre du flyer si elle n'est plus bonne.

    La comparaison de date se fait sur un epoch entier (`expire_ts`) plutot que sur une chaine ISO : une
    comparaison lexicale sur des ISO 8601 fonctionne tant que tout le monde est en UTC, et casse
    silencieusement des qu'un fuseau se glisse dans la serie.
    """
    promo = entry.get("promo")
    if not promo:
        return None
    return promo if _motif_promo(entry, promo) else None


def _destination(entry: dict, offre: dict | None = None) -> str:
    """L'URL de destination, augmentee du code de l'offre encore valide.

    Pas de repli sur `entry["promo"]` ici : le choix de l'offre appartient a `_offre_presentee`, qui sait
    si un code a ete demande. Deux endroits qui decident lequel des codes s'applique, c'est une offre
    appliquee a un visiteur qui n'a rien demande.
    """
    destination = entry["target_url"]
    promo = offre
    if not promo or _motif_promo(entry, promo):
        return destination
    from urllib.parse import urlencode, urlsplit, urlunsplit

    from apps.qr import promo as promo_rules

    # Le comptage se fait **ici**, au moment ou l'on s'engage a appliquer la remise : un compteur
    # incremente sur une page d'avis « offre terminee » brûlerait une part de l'offre a chaque visite.
    promo_rules.marquer_utilise(int(promo["id"]))
    morceaux = urlsplit(destination)
    joint = (
        f"{morceaux.query}&{urlencode({'promo': promo['code']})}"
        if morceaux.query
        else urlencode({"promo": promo["code"]})
    )
    return urlunsplit((morceaux.scheme, morceaux.netloc, morceaux.path, joint, morceaux.fragment))


def _lookup(slug: str) -> dict | None:
    """Ligne de base de données derrière le cache. Index unique sur `slug` : une requête, ~1 ms."""
    from apps.qr.models import QrCode

    qr = QrCode.objects.select_related("owner").filter(slug=slug).exclude(deleted_at__isnull=False).first()
    if qr is None:
        return None
    return qr_cache.serialize_for_cache(qr)


def _scan_event(request: HttpRequest, entry: dict, *, status: int = 302) -> dict:
    from apps.analytics.ingest import build_event

    return build_event(
        request,
        qr_id=entry.get("qr_id"),
        owner_id=entry.get("owner_id"),
        consent=bool(entry.get("consent")),
        status=status,
    )


async def _rate_limited(request: HttpRequest, slug: str) -> bool:
    """Fenêtre d'une minute par (IP, slug). Le cache est seul impliqué : zéro écriture SQL."""
    limit = int((settings.QR or {}).get("SCAN_RATE_PER_IP", 300))
    ident = getattr(request, "client_ip", None) or "inconnue"
    key = f"qrs:rate:scan:{ident}:{slug}"
    from apps.common import redis_client

    client = redis_client.get_redis_async()
    if client is None:
        return False
    try:
        count = await client.incr(key)
        if int(count) == 1:
            await client.expire(key, 60)
    except Exception as exc:  # noqa: BLE001 - on ne refuse jamais un scan pour une panne de compteur
        logger.warning("rate-limit indisponible (%s) : contrôle ignoré", exc)
        return False
    return int(count) > limit


def _decorate(response: HttpResponse, *, cache_hit: bool, promo: str = "") -> None:
    """En-tetes communs du chemin chaud. `promo` trace le verdict d'une offre, pour le debug comptoir.

    L'en-tete est informatif et volontairement depourvu de secret : il dit « expire » ou « epuise », jamais
    « ce compte a tel budget » — un en-tete de redirection est lu par n'importe quel scanner de liens.
    """
    response["Cache-Control"] = "no-store, max-age=0"
    response["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["X-Redirect-Cache"] = "hit" if cache_hit else "miss"
    if promo:
        response["X-Promo"] = promo
    # Le cache du navigateur ne doit pas retenir la redirection : sinon nos compteurs s'effondrent
    # silencieusement le jour où quelqu'un ajoute un `max-age` optimiste.
    response["Vary"] = "Accept-Language"


def _unavailable(*, status: int, reason: str) -> HttpResponse:
    """Page de marque pour un QR mort : l'utilisateur qui scanne comprend, au lieu de jeter l'étiquette."""
    context = {"reason": reason, "title": STATUS_GONE_TITLE}
    try:
        body = render(None, "qr/unavailable.html", context, status=status).content
    except Exception as exc:  # noqa: BLE001 - pas de contexte de requête dans une vue asynchrone
        logger.warning("gabarit indisponible en echec (%s) : repli texte", exc)
        body = b""
    response = HttpResponse(
        body or f"<h1>{STATUS_GONE_TITLE}</h1>", status=status, content_type="text/html; charset=utf-8"
    )
    _decorate(response, cache_hit=False)
    return response
