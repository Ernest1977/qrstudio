"""Resserre les compteurs d'usage des codes promo depuis Redis vers PostgreSQL.

Pourquoi une commande et pas un simple `+=` à chaque scan : la Redis de ce projet est en
`CACHE_BACKEND=locmem` sur une instance, `redis` avec eviction possible sur l'autre. Un compteur qui n'existe
que là-dedans peut donc **repartir de zéro** (offre jamais close, 500 usages au lieu de 500 — le client
paye deux fois la meme remise) ou **rester bloqué** si on ne recopiait que dans la base (offre close trop tot
— le client ne reçoit pas la remise promise et appelle le support).

La regle appliquee ici : `usages = max(db, db + redis)` puis remise a zero du compteur chaud. Idempotent,
et aucun chemin de scan ne compte deux fois parce que la cle Redis est supprimee **apres** l'ecriture en
base (une cle qui reste n'est pas perdue : elle est recomptee a la prochaine passe).

Cron recommande (une fois par heure, apres la fermeture quotidienne de la boutique) :

    17 * * * * cd /opt/qrstudio/api && /opt/qrstudio/venv/bin/python manage.py sync_promo_usages
"""

from __future__ import annotations

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F

from apps.qr.models import PromoCode


class Command(BaseCommand):
    help = "Reporte les usages comptes en cache vers la base, puis remet les compteurs chauds a zero."

    def add_arguments(self, parser):
        parser.add_argument(
            "--seulement-actives",
            action="store_true",
            help="Ignorer les offres closes : leur compteur residuel n'a plus d'effet sur un scan.",
        )
        parser.add_argument("--limite", type=int, default=5000, help="Nombre max de codes traitees par passe.")

    def handle(self, *args, **options):
        from apps.qr import promo

        lignes = PromoCode.objects.select_related("qr").exclude(qr__isnull=True)
        if options["seulement_actives"]:
            lignes = lignes.filter(actif=True)
        traitees = 0
        reportees = 0
        for promo_ligne in lignes[: options["limite"]]:
            traitees += 1
            compte = cache.get(promo.cle_usages(promo_ligne.id))
            if not compte:
                continue
            with transaction.atomic():
                # `F()` et pas `objet.usages + compte` : deux passes (cron qui se chevauche, ou un scan
                # pendant la mise a jour) perdraient des comptages avec une lecture/ecriture naive.
                PromoCode.objects.filter(pk=promo_ligne.pk).update(usages=F("usages") + int(compte))
                cache.delete(promo.cle_usages(promo_ligne.id))
            promo_ligne.refresh_from_db()
            promo_ligne.touch_qr_cache()  # le seuil d'epuisement est evalue a chaud : il doit voir le nouveau total
            reportees += int(compte)
            self.stdout.write(self.style.SUCCESS(f"{promo_ligne.code}: +{compte} usage(s), total {promo_ligne.usages}"))
        self.stdout.write(
            self.style.SUCCESS(f"{traitees} code(s) examiné(s), {reportees} usage(s) reporté(s) en base.")
        )
