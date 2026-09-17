#!/usr/bin/env python3
"""Garde-fou d'expédition : `check_email_dns --json`, jugé, avec des codes de sortie qui veulent dire quelque chose.

Pourquoi un script et pas juste la commande Django : la commande rend un verdict (« prêt / pas prêt »),
mais ce qui casse un déploiement n'est pas ce verdict-là. Ce qui casse, c'est le domaine qui a changé de
`DEFAULT_FROM_EMAIL` sans que personne le dise, une politique DMARC retombée à `none` après une
réimport de zone, ou un sélecteur DKIM dont la clé s'est vidée. Le script vérifie ces trois-là *contre une
attente fournie par le déploiement*, pas contre ce que le code croit.

    python3 scripts/check_email_gate.py                          # verdict lisible, code 0/1/2/3
    python3 scripts/check_email_gate.py --attendu kamcofarm.com  # refuse si l'envoi pointe ailleurs
    python3 scripts/check_email_gate.py --politique quarantine   # exige au moins ce niveau de sévérité
    python3 scripts/check_email_gate.py --strict                 # les anomalies DNS font échouer le job
    python3 scripts/check_email_gate.py --json-out /tmp/email.json

Codes de sortie — pensés pour être lus par un CI, donc distincts :

    0  prêt (et, si demandé, politique au moins aussi sévère que l'attente)
    1  le minimum manque : pas de SPF utilisable, ou aucune clé DKIM derrière les sélecteurs attendus
    2  le contrôle n'a pas pu rendre de verdict (DNS injoignable, `DEFAULT_FROM_EMAIL` inexploitable)
    3  le courrier peut partir, mais le domaine n'est pas conforme à l'attente (`--attendu`, `--politique`,
       ou `--strict` avec des anomalies ouvertes)

Aucune dépendance hors stdlib : `jq` n'est pas garanti sur un VPS minimal, et un garde-fou qui tombe parce
qu'un binaire manque est un garde-fou qui sera retiré du pipeline au premier incident.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Severites DMARC, dans l'ordre. Comparer par chaine (`"quarantine" > "none"`) marcherait par accident et
# se casserait a la prochaine valeur; un rang explicite se lit et se prouve.
RANG_POLITIQUE = {None: -1, "none": 0, "quarantine": 1, "reject": 2}


def _racine() -> Path:
    return Path(__file__).resolve().parent.parent


def lancer_controle(racine: Path, domaine: str | None, *, reglages: str) -> tuple[int, dict | None, str]:
    """Joue `manage.py check_email_dns --json` et sépare le JSON du reste (Django écrit ses logs sur stderr).

    On ne réimplémente pas le jugement ici : le script n'ajoute que la comparaison a l'attente. Si les deux
    divergeaient, le controle en prod dirait une chose et le pipeline une autre — le pire des mondes.
    """
    # `--skip-checks` : un verdict DNS ne doit pas dependre de l'importabilite de toutes les integrations.
    # Sans ce drapeau, une dependance optionnelle cassee (webauthn, un SDK de paiement) fait echouer le
    # garde-fou avec un traceback d'import, alors que SPF/DKIM/DMARC se lisent sans charger les URLs.
    commande = [sys.executable, "manage.py", "check_email_dns", "--json", "--skip-checks"]
    if domaine:
        commande += ["--domaine", domaine]
    # On herite de l'environnement **recu** : un deploiement porte DJANGO_SECRET_KEY, DATABASE_URL,
    # EMAIL_HOST dans son env (ou dans .env, que `config/settings` relit). Repartir d'un environnement vide
    # ferait juger un domaine different de celui qui sert reellement — et le garde-fou passerait au vert.
    env = {**os.environ, "PYTHONPATH": "vendor", "DJANGO_SETTINGS_MODULE": reglages}
    proces = subprocess.run(commande, cwd=racine, capture_output=True, text=True, env=env, check=False)
    brut = proces.stdout.strip()
    if not brut:
        # On ne recopie pas un traceback de 200 lignes dans un journal de deploiement : la fin suffit,
        # c'est la que se trouve la cause.
        fin = " | ".join(ligne.strip() for ligne in proces.stderr.strip().splitlines()[-3:] if ligne.strip())
        return 2, None, (fin or "sortie vide")
    try:
        return proces.returncode, json.loads(brut), proces.stderr.strip()
    except json.JSONDecodeError as exc:
        return 2, None, f"sortie non parsable ({exc}) : {brut[:120]!r}"


def juger(bilan: dict, *, attendu: str | None, politique_min: str | None, strict: bool) -> tuple[int, list[str]]:
    blocages: list[str] = []
    avertissements: list[str] = []

    domaine = (bilan.get("domaine") or "").strip().lower()
    if attendu and domaine != attendu.lower():
        # Le cas le plus fréquent n'est pas une erreur DNS : c'est un .env ou DEFAULT_FROM_EMAIL pointe
        # sur le domaine de dev, et le controle vert qu'on vient de lire ne parle pas du domaine envoye.
        blocages.append(f"domaine d'expédition = {domaine or '—'}, attendu {attendu}")

    if not bilan.get("pret"):
        blocages.append("minimum non réuni : SPF autorisant un relais et/ou clé DKIM absente")

    dmarc = bilan.get("dmarc") or {}
    rang_attendu = RANG_POLITIQUE.get(politique_min.lower()) if politique_min else None
    if politique_min and rang_attendu is None:
        blocages.append(f"politique attendue invalide : {politique_min!r} (none | quarantine | reject)")
    elif politique_min:
        actuel = RANG_POLITIQUE.get(dmarc.get("politique"), -1)
        if actuel < rang_attendu:
            blocages.append(
                f"politique DMARC publiée = {dmarc.get('politique') or 'absente'}, "
                f"le déploiement exige au moins {politique_min}"
            )

    anomalies = list(dmarc.get("anomalies") or [])
    vides = list(bilan.get("dkim_selecteurs_sans_cle") or [])
    for a in anomalies:
        (blocages if strict else avertissements).append(f"DMARC : {a}")
    if vides:
        (blocages if strict else avertissements).append(
            "DKIM : sélecteurs publiés sans clé (" + ", ".join(vides) + ") — le DNS est là, la signature non"
        )

    if blocages:
        return (1 if not bilan.get("pret") else 3), blocages
    return 0, avertissements


def main(argv: list[str] | None = None) -> int:
    parse = argparse.ArgumentParser(
        description="Contrôle SPF/DKIM/DMARC de l'expéditeur, pour la CI et le déploiement."
    )
    parse.add_argument("--attendu", help="domaine que l'envoi est censé utiliser (DEFAULT_FROM_EMAIL).")
    parse.add_argument("--politique", help="sévérité minimale exigée du DMARC publié : none|quarantine|reject.")
    parse.add_argument(
        "--strict", action="store_true", help="les anomalies du DMARC (espace dans rua, pct=0, aspf=s…) font échouer."
    )
    parse.add_argument("--domaine", help="interroger ce domaine plutôt que celui lu dans DEFAULT_FROM_EMAIL.")
    parse.add_argument("--json-out", type=Path, help="écrire le JSON brut du contrôle (journal du déploiement).")
    parse.add_argument(
        "--reglages", default="config.settings.dev", help="DJANGO_SETTINGS_MODULE utilisé pour la commande."
    )
    args = parse.parse_args(argv)

    racine = _racine()
    if shutil.which("python3") is None and not (racine / "manage.py").exists():
        print("! manage.py introuvable : à lancer depuis le dépôt (api/) ou sur le VPS.", file=sys.stderr)
        return 2

    code_commande, bilan, err = lancer_controle(racine, args.domaine, reglages=args.reglages)
    if bilan is None:
        print(f"! le contrôle n'a pas rendu de verdict : {err}", file=sys.stderr)
        return 2
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(bilan, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"json écrit : {args.json_out}")

    code, messages = juger(bilan, attendu=args.attendu, politique_min=args.politique, strict=args.strict)
    domaine = bilan.get("domaine") or "?"
    dmarc = bilan.get("dmarc") or {}
    print(f"domaine   : {domaine}")
    print(
        f"SPF       : {'ok' if bilan.get('spf_present') else 'ABSENT'}"
        f"{' (aucun relais autorisé)' if bilan.get('spf_present') and not bilan.get('spf_autorise_le_relais') else ''}"
    )
    print(f"DKIM      : {', '.join(bilan.get('dkim_selecteurs') or []) or 'AUCUNE clé'}")
    print(
        f"DMARC     : p={dmarc.get('politique') or 'absent'}"
        f" (aspf={dmarc.get('aspf')} adkim={dmarc.get('adkim')} pct={dmarc.get('pct')})"
        f" ; rapports : {', '.join(dmarc.get('rua') or []) or 'aucun'}"
    )
    for m in messages:
        prefix = "! " if code else "- "
        print(f"{prefix}{m}")
    if code_commande not in (0, 1):
        print(f"! la commande a rendu {code_commande} (DNS injoignable ? à rejouer depuis le VPS)")
    if code == 0:
        print("-> garde-fou vert : le courrier peut partir, le domaine est conforme à l'attente.")
    elif not bilan.get("pret"):
        print("-> courrier non autorisé ou non signé : ne pas déployer l'envoi en l'état.", file=sys.stderr)
    else:
        print("-> le courrier passerait, mais n'est pas conforme à l'attente du déploiement.", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
