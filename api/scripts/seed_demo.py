#!/usr/bin/env python3
"""Jeu de données de démonstration — pour qu'une instance vide ne montre rien.

Un preview sans QR, sans scan et sans compte ne prouve rien : la liste est vide, les graphes sont
plats, l'admin ne montre que des tables à zéro. Ce script pose ce qu'il faut pour cliquer partout, y
compris un QR déjà scanné depuis trois pays, et il est **idempotent** (relancer ne duplique rien).

Réservé au dev. En prod, la même chose passerait par `django-admin` ou un fixture, jamais par ce
chemin : il écrit des consentements et des mots de passe connus.
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django

django.setup()

from django.utils import timezone  # noqa: E402

from apps.accounts.models import EmailVerificationCode, User  # noqa: E402
from apps.analytics.aggregates import rebuild_for_day  # noqa: E402
from apps.analytics.ingest import persist_many  # noqa: E402
from apps.qr.models import QrCode  # noqa: E402

MOT_DE_PASSE = "demo-qrstudio-2026"


def compte() -> User:
    user, cree = User.objects.get_or_create(
        email="demo@kamcofarm.com",
        defaults={"plan": "pro", "first_name": "Kamco", "last_name": "Farm", "locale": "fr"},
    )
    user.set_password(MOT_DE_PASSE)
    user.is_email_verified = True
    user.consent_tracking_at = timezone.now()
    if cree:
        user.is_active = True
    user.save()

    staff, _ = User.objects.get_or_create(
        email="ops@kamcofarm.com",
        defaults={"plan": "team", "is_staff": True, "is_superuser": True, "first_name": "Exploitation"},
    )
    staff.set_password(MOT_DE_PASSE)
    staff.is_email_verified = True
    staff.is_staff = staff.is_superuser = True
    staff.save()
    EmailVerificationCode.objects.filter(user__email__endswith="@kamcofarm.com").update(consumed_at=timezone.now())
    return user


def qrs(user: User) -> list[QrCode]:
    modeles = [
        {
            "kind": "dynamic",
            "type_id": "url",
            "label": "Boutique — vitrine",
            "target_url": "https://kamcofarm.com/boutique?utm_source=flyer",
            "notes": "À recoller sur le flyer A5 du marché.",
            "design": {"ecc": "M", "margin": 2},
        },
        {
            "kind": "dynamic",
            "type_id": "url",
            "label": "Menu du midi",
            "target_url": "https://kamcofarm.com/menu",
            "notes": "Change tous les lundis : c'est le cas d'usage du lien redirectable.",
        },
        {
            "kind": "static",
            "type_id": "wifi",
            "label": "Wifi libre-service",
            "payload": 'WIFI:T:WPA;S:KamcoGuest;p"a,s\\:5G;;',
        },
    ]
    sortie = []
    for modele in modeles:
        qr = QrCode.objects.filter(owner=user, label=modele["label"]).first()
        if qr is None:
            qr = QrCode.objects.create(owner=user, **modele)
        else:
            for cle, valeur in modele.items():
                setattr(qr, cle, valeur)
            qr.save()
        sortie.append(qr)
    return sortie


def scans(qrs: list[QrCode]) -> int:
    """Des scans sur 6 jours, 3 pays, deux classes d'appareil et un peu de bruit de bot."""
    from apps.qr.models import Kind

    dynamiques = [qr for qr in qrs if qr.kind == Kind.DYNAMIC]
    if not dynamiques:
        return 0
    maintenant = timezone.now()
    evenements = []
    pays = [("IT", 12), ("FR", 5), ("CH", 3), ("", 1)]
    for jour in range(6):
        for qr in dynamiques:
            for code_pays, volume in pays:
                for i in range(volume):
                    horodatage = maintenant - timedelta(days=jour, hours=i)
                    evenements.append(
                        {
                            "scan_id": f"demo-{qr.pk}-{jour}-{code_pays}-{i}",
                            "ts": horodatage.isoformat(),
                            "day": horodatage.date().isoformat(),
                            "qr_id": qr.pk,
                            "owner_id": qr.owner_id,
                            "ip_trunc": f"203.0.113.{i}",
                            "ip_prefix_len": 24,
                            "device_class": "mobile" if i % 3 else "desktop",
                            "is_bot": "1" if code_pays == "" else "0",
                            "status": 302,
                            "user_agent_hash": f"{(jour * 31 + i) ** 3 % 10**32:032x}",
                            "referer_domain": "facebook.com" if i % 4 == 0 else "",
                            "geo": {"country_code": code_pays or None, "region": None, "city": None},
                            "source": "seed",
                        }
                    )
    persist_many(evenements)
    ecrits = 0
    for jour in range(6):
        ecrits += rebuild_for_day((maintenant - timedelta(days=jour)).date())
    return ecrits


def main() -> int:
    user = compte()
    qr = qrs(user)
    lignes = scans(qr)
    print(f"comptes      : demo@kamcofarm.com / ops@kamcofarm.com — mot de passe {MOT_DE_PASSE}")
    for objet in qr:
        print(f"qr           : {objet.label} -> /r/{objet.slug}")
    print(f"agregats     : {lignes} lignes de stats reconstituees")
    print(f"redirection  : curl -i http://127.0.0.1:8000/r/{qr[0].slug}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
