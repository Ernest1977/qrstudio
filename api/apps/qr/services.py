"""Écritures : création, modification, duplication, suppression.

Elles vivent ici (et pas dans les vues) parce que l'admin Django, un import CSV et la future API
publique doivent passer par **le même** chemin : quota, vérification d'URL, invalider le cache,
tracer une version.
"""

from __future__ import annotations

import logging

from django.db import transaction

from apps.common.exceptions import ApiError
from apps.qr import cache as qr_cache
from apps.qr import quota
from apps.qr.models import QrCode, QrVersion

logger = logging.getLogger(__name__)


@transaction.atomic
def create_qr(*, owner, serializer) -> QrCode:
    # Le plafond global du palier est verifie **avant** celui des QR dynamiques : un compte
    # qui a depasse son nombre total de QR ne doit pas recevoir « passez au palier superieur » alors que le
    # probleme est le volume, pas la fonction.
    quota.assert_can_create_static(owner)
    if serializer.validated_data.get("kind") == "dynamic":
        quota.assert_can_create_dynamic(owner)
    qr = serializer.save(owner=owner)
    logger.info("qr cree id=%s kind=%s user_id=%s", qr.pk, qr.kind, owner.pk)
    return qr


@transaction.atomic
def update_qr(*, qr: QrCode, actor, serializer) -> QrCode:
    """Un `PATCH` qui change la destination doit invalider le cache **après** le commit.

    Sinus: invalider avant laisserait la requête concurrente relire l'ancienne valeur et la remettre
    en cache pour 60 s. On s'appuie donc sur `transaction.on_commit`.
    """
    before = {field: getattr(qr, field) for field in ("target_url", "kind", "is_active", "label", "payload")}
    qr = serializer.save()
    changes = {k: [old, getattr(qr, k)] for k, old in before.items() if getattr(qr, k) != old}
    if changes:
        QrVersion.objects.create(qr=qr, actor=actor, change=changes)
    slug = qr.slug
    # Résolution du nom au moment de l'appel (et non un lien importé une fois pour toutes) :
    # c'est ce qui rend l'invalidation observable dans les tests et remplaçable en cas de incident.
    transaction.on_commit(lambda: qr_cache.invalidate(slug))
    return qr


def duplicate(source: QrCode, *, actor) -> QrCode:
    clone = QrCode(
        owner=source.owner,
        kind=source.kind,
        type_id=source.type_id,
        label=f"{source.label} (copie)".strip(),
        notes=source.notes,
        payload=source.payload,
        target_url=source.target_url,
        design=dict(source.design or {}),
        is_public=source.is_public,
        redirect_mode=source.redirect_mode,
        utm_mode=source.utm_mode,
    )
    clone.save()
    QrVersion.objects.create(qr=clone, actor=actor, change={"duplicated_from": source.pk})
    return clone


def soft_delete(qr: QrCode, *, actor=None) -> None:
    qr.soft_delete(actor=actor)


def hard_delete(qr: QrCode, *, actor=None) -> None:
    """Purge définitive (RGPD) : le QR n'existe plus, **et** le slug ne sera jamais réémis.

    Le slug part avec la ligne ; si un flyer imprimé porte encore `/r/Ab3xY`, le visiteur tombera sur
    410 « introuvable » — c'est le comportement voulu par l'utilisateur qui a demandé l'effacement.
    """
    from apps.analytics.models import QrDailyStats, ScanEvent

    slug = qr.slug
    ScanEvent.objects.filter(qr_id=qr.pk).delete()
    QrDailyStats.objects.filter(qr_id=qr.pk).delete()
    qr.delete()
    qr_cache.invalidate(slug)
    logger.info("qr purgé id=%s par user_id=%s", qr.pk, getattr(actor, "pk", None))


def assert_owner(user, qr: QrCode) -> None:
    if qr.owner_id != getattr(user, "pk", None):
        raise ApiError("not_found", "Ressource introuvable.", status_code=404)
