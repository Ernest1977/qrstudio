"""`manage.py check_email_dns` — l'état réel de SPF / DKIM / DMARC pour l'adresse qui envoie.

À jouer **avant** d'envoyer le premier code de vérification à un client, et après tout changement de
relais. La commande sort en code 1 quand le minimum (SPF + au moins un sélecteur DKIM) n'est pas réuni,
donc elle peut fermer un pipeline de déploiement au lieu de faire jaser un humain.
"""

from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.common.email_dns import domaine_de, resoudre


class Command(BaseCommand):
    help = "Vérifie SPF, DKIM et DMARC du domaine d'expédition (DEFAULT_FROM_EMAIL)."

    def add_arguments(self, parser):
        parser.add_argument("--domaine", help="Surcharger le domaine lu dans DEFAULT_FROM_EMAIL.")
        parser.add_argument("--json", action="store_true", help="Sortie machine, pour la CI.")

    def handle(self, *args, **options):
        domaine = options.get("domaine") or domaine_de(settings.DEFAULT_FROM_EMAIL)
        if not domaine:
            self.stderr.write(self.style.ERROR("DEFAULT_FROM_EMAIL ne contient pas de domaine exploitable."))
            raise SystemExit(2)
        try:
            constat = resoudre(domaine)
        except RuntimeError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            raise SystemExit(2) from exc  # B904 : la cause de la sortie est cette erreur, pas le code lui-meme
        bilan = constat.bilan()
        if options.get("json"):
            self.stdout.write(json.dumps(bilan, ensure_ascii=False, indent=2))
        else:
            self.stdout.write(f"domaine exploré : {domaine}")
            relais = "" if bilan["spf_autorise_le_relais"] else " (mais aucun relais autorisé)"
            self.stdout.write(f"  SPF    : {'présent' if bilan['spf_present'] else 'ABSENT'}{relais}")
            self.stdout.write(f"  DKIM   : {', '.join(bilan['dkim_selecteurs']) or 'AUCUN sélecteur résolu'}")
            if bilan["dkim_selecteurs_sans_cle"]:
                self.stdout.write(f"         sans clé publiée : {', '.join(bilan['dkim_selecteurs_sans_cle'])}")
            dmarc = bilan["dmarc"]
            if dmarc["present"]:
                reglages = f"aspf={dmarc['aspf']} adkim={dmarc['adkim']}"
                if dmarc["pct"] is not None:
                    reglages += f" pct={dmarc['pct']}"
                rapports = ", ".join(dmarc["rua"]) or "aucun rapport demandé"
                self.stdout.write(f"  DMARC  : p={dmarc['politique'] or '—'} ({reglages}) ; rua : {rapports}")
            else:
                self.stdout.write("  DMARC  : absent")
            for alerte in bilan["alertes"]:
                self.stdout.write(self.style.WARNING(f"  ! {alerte}"))
            if bilan["pret"]:
                self.stdout.write(self.style.SUCCESS("  -> minimum réuni ; le courrier peut être signé et autorisé."))
        # `sys.exit`/`SystemExit`, pas `return 1` : `BaseCommand.execute` fait `if output: self.stdout.write(output)`,
        # donc un entier de retour **truthy** est passé à `stdout.write` et la commande meurt en
        # `AttributeError: 'int' object has no attribute 'endswith'`. Mesuré : `return 0` passait (0 est
        # falsé), `return 1` cassait — soit exactement le cas où un déploiement doit se terminer proprement.
        # Le traceback donnait bien un code 1 par accident, ce qui masquait la panne derrière un « ça a marché ».
        if not bilan["pret"]:
            raise SystemExit(1)
        return None
