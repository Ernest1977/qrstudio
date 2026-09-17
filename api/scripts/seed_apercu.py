"""Joue la base de demonstration pour l'aperçu (SQLite ephemere, reglages dev).

Trois comptes, trois cas d'usage reels : un Entreprise avec campagne + codes promo (dont un clos), un
Gratuit pour voir la porte payante et le quota a 1, un superuser pour le back-office et django-admin.
Idempotent : relancer ne duplique rien.

Ce fichier s'execute directement (`python3 scripts/seed_apercu.py`) et n'est pas importe par l'application.
Les mots de passe ci-dessous sont ceux d'une base de demonstration locale, hors depot grace a `api/.gitignore`
qui exclut `db.sqlite3` ; ils ne doivent servir nulle part ailleurs.
"""

import os
import sys
from datetime import timedelta
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]


def main() -> None:
    # `django.setup()` doit precéder tout import qui touche aux modèles : c'est la raison pour laquelle
    # `django.utils` et `apps.*` sont importés ici et non en tête de fichier.
    sys.path.insert(0, str(RACINE))
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    django.setup()

    from django.utils import timezone

    from apps.accounts.models import User
    from apps.qr.models import PromoCode, QrCode

    def compte(email, mot_de_passe, **champs):
        u, _ = User.objects.get_or_create(email=email, defaults={"is_email_verified": True, **champs})
        u.set_password(mot_de_passe)
        for cle, valeur in champs.items():
            setattr(u, cle, valeur)
        u.save()
        return u

    biz = compte("smoke-biz@kamcofarm.com", "Smoke2026!Biz", plan="business")
    libre = compte("smoke-free@kamcofarm.com", "Smoke2026!Free", plan="free")
    admin = compte("root.smoke@kamcofarm.com", "Smoke2026!Valide", plan="free", is_staff=True, is_superuser=True)

    qr, _ = QrCode.objects.get_or_create(
        owner=biz,
        label="Cave de Naples — été",
        defaults={
            "kind": "dynamic",
            "type_id": "url",
            "target_url": "https://kamcofarm.example/cave",
            "is_public": True,
            "design": {"art": "naples", "animation": "bordure", "frames": 20, "liser": 28},
        },
    )
    QrCode.objects.get_or_create(
        owner=libre,
        label="Ardoise du jour",
        defaults={"kind": "static", "type_id": "text", "payload": "Pâtes du jour : 9 €"},
    )

    maintenant = timezone.now()
    offres = [
        ("PROMO-CAVE-ANMW", "Livraison offerte", "livraison", None, 9, 3, True),
        ("PROMO-CAVE-JB7K", "-25% sur la cave", "pourcentage", "25", 7, 2, True),
        ("PROMO-CAVE-H9RF", "-15% (fin de campagne)", "pourcentage", "15", -2, None, True),
    ]
    for code, libelle, type_remise, valeur, jours, usages_max, actif in offres:
        expire = maintenant + timedelta(days=jours)
        ligne = PromoCode.objects.filter(owner=biz, code=code).first()
        if ligne is None:
            ligne = PromoCode(owner=biz, code=code)
        ligne.qr = qr
        ligne.libelle = libelle
        ligne.remise_type = type_remise
        ligne.remise_valeur = valeur
        ligne.devise = "EUR"
        ligne.expire_le = expire
        ligne.usages_max = usages_max
        ligne.actif = actif
        ligne.notes = "Joué pour l'aperçu."
        ligne.save()

    print("comptes : business / free / superuser — identifiants en clair ci-dessous")
    print(f"  {biz.email} / Smoke2026!Biz        palier={biz.plan}  qr={qr.pk} slug={qr.slug}")
    print(f"  {libre.email} / Smoke2026!Free       palier={libre.plan}  quota_statiques={libre.quota_statique}")
    print(f"  {admin.email} / Smoke2026!Valide  superuser, back-office /manage/ et admin /manage-9f2/")
    for ligne in PromoCode.objects.filter(owner=biz).order_by("expire_le"):
        verdict = ligne.statut()
        etat = "valide" if verdict.valide else verdict.motif
        print(f"  {ligne.code}  {etat}  expire_le={ligne.expire_le:%d/%m %H:%M}")
    print(f"lien de scan (via le proxy du front, ou l'API directement) : /r/{qr.slug}?promo=PROMO-CAVE-JB7K")
    print("URL courte enregistree en base :", qr.short_url)


if __name__ == "__main__":
    main()
