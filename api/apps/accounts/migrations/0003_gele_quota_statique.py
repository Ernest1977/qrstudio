"""Gèle le niveau acquis des licences émises avant la nouvelle grille, puis ajoute les deux champs.

Deux choses se passent ici, dans cet ordre :

1. les champs `quota_statique_gele` / `quota_statique_palier` arrivent ;
2. **avant** que quiconque lise la nouvelle valeur du Gratuit (20 → 1 QR statique), chaque compte *déjà
   créé* reçoit le niveau qu'on lui avait vendu : 20, taggé `free`.

Le tag est ce qui rend l'opération sans risque. `User.quota_statique` ne retient le gel que si le palier
enregistré est le palier courant (`apps/accounts/models.py`) : un compte payant portant un gel hérité n'est
   donc pas raboté, et un compte qui monte en palier repart du plafond du palier supérieur. On peut donc
geler largement — y compris un compte dont `plan` est obsolète — sans promettre quoi que ce soit à
personne. Les comptes créés **après** la bascule, eux, ne sont pas touchés : ils achètent le Gratuit à 1.

Sens inverse : on efface les gels. Perdre l'information vaut mieux qu'un champ orphelin qui continuerait
de favoriser une cohorte fantôme après un retour en arrière.
"""

from __future__ import annotations

from django.db import migrations, models

#: Date de bascule de la grille (Premium/Entreprise += art et animation, Gratuit = 1 QR statique).
BASCULE = "2026-09-16T00:00:00+00:00"
ANCIEN_QUOTA_GRATUIT = 20


def gele_les_licences_existantes(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    from datetime import datetime, timezone

    from django.db.models import Q

    date_bascule = datetime.fromisoformat(BASCULE).astimezone(timezone.utc)

    # Un seul UPDATE, pas une boucle sur 1 M de comptes : `quota_statique` est lu a chaque creation de QR,
    # et une migration qui durerait une heure au meme ordre de grandeur est une fenetre d'incident.
    #
    # Le critere est « ne paie plus a la bascule », exprime avec les seuls champs de `User` :
    # `plan_until` nul = jamais paye, `plan_until` passe = redescendu en gratuit. C'est exactement la
    # regle de `User.plan_effectif` (ligne 120 de `apps/accounts/models.py`) — et pas un JOIN vers
    # `billing` : en etat historique, ce nom inverse n'existe pas, et une migration qui depend de
    # l'ordre des apps est une migration qu'on ne peut plus rejouer.
    User.objects.filter(date_joined__lt=date_bascule, quota_statique_gele__isnull=True).filter(
        Q(plan_until__isnull=True) | Q(plan_until__lt=date_bascule)
    ).update(quota_statique_gele=ANCIEN_QUOTA_GRATUIT, quota_statique_palier="free")


def degeler(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(quota_statique_gele__isnull=False).update(quota_statique_gele=None, quota_statique_palier="")


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_grille_tarifaire_reelle"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="quota_statique_gele",
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name="QR statiques (niveau acquis)"),
        ),
        migrations.AddField(
            model_name="user",
            name="quota_statique_palier",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.RunPython(gele_les_licences_existantes, degeler, elidable=False),
    ]
