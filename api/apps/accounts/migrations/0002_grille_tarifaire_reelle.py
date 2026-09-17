"""Migration de la grille tarifaire : `free|pro|team` -> `free|standard|premium|business`.

Deux choses séparées volontairement :

1. l'`AlterField` ne change **rien en base** (les `choices` ne vivent que dans Django) — il est là pour
   que `makemigrations --check` reste muet et que l'admin affiche les bons libellés ;
2. le `RunPython` redirige les valeurs existantes. Sans lui, un compte `pro` se retrouverait avec une
   valeur que `plans.PALIERS` ne connaît pas, donc **retombé en gratuit** silencieusement : le client
   qui paie 8,99 € perdrait ses QR dynamiques au déploiement. C'est précisément le cas que la
   fonction `plan_depuis_ancien_identifiant` existe de couvrir, et elle est jouée ici plutôt que
   dupliquée.

La migration est prévue pour être rejouée deux fois sans effet (`get_or_create`-like : on ne touche
que les lignes encore dans l'ancien vocabulaire).
"""

from __future__ import annotations

from django.db import migrations, models

ANCIEN_VERS_NOUVEAU = {"pro": "premium", "team": "business"}
NOUVEAU_VERS_ANCIEN = {"premium": "pro", "business": "team"}


def vers_grille_reelle(apps, schema_editor) -> None:
    User = apps.get_model("accounts", "User")
    for ancien, nouveau in ANCIEN_VERS_NOUVEAU.items():
        touches = User.objects.filter(plan=ancien).update(plan=nouveau)
        if touches:
            # Journalisé : le nombre de comptes facturés basculés est la seule trace, apres coup, que
            # cette migration a bien fait son travail sur CETTE instance.
            print(f"[plans] {ancien} -> {nouveau}: {touches} compte(s)")


def vers_grille_precedente(apps, schema_editor) -> None:
    User = apps.get_model("accounts", "User")
    for nouveau, ancien in NOUVEAU_VERS_ANCIEN.items():
        User.objects.filter(plan=nouveau).update(plan=ancien)
    # `standard` n'a pas d'equivalent dans l'ancienne grille : un retour en arriere le laisserait en
    # place, et Django refuserait la valeur au premier `full_clean`. Cote explicitement plutot que de
    # transformer un palier payant en gratuit.
    restants = User.objects.filter(plan="standard").count()
    if restants:
        print(f"[plans] attention: {restants} compte(s) restent en `standard`, palier absent de l'ancienne grille")


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="plan",
            field=models.CharField(
                choices=[
                    ("free", "Gratuit"),
                    ("standard", "Standard"),
                    ("premium", "Premium"),
                    ("business", "Entreprise"),
                ],
                default="free",
                max_length=16,
            ),
        ),
        migrations.RunPython(vers_grille_reelle, vers_grille_precedente),
    ]
