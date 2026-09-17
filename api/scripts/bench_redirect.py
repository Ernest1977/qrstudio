#!/usr/bin/env python3
"""Banc de charge du chemin chaud `GET /r/{slug}` — sans dependance externe.

Le critere de la spec est `p99 < 15 ms a 1000 rps`. k6 ne tourne pas dans ce bac a sable, donc ce
script stdlib fait la mesure minimale honnete : un pool de connexions keep-alive, une file de
requetes, percentiles calcules cote client. Il mesure donc aussi la boucle event du serveur de dev :
les chiffres obtenus ici sont un *plancher de verification*, pas une prediction de production.

Usage :
    python3 manage.py runserver 0.0.0.0:8000 --settings=config.settings.dev &
    python3 scripts/bench_redirect.py --requests 20000 --concurrency 32
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import pathlib
import socket
import statistics
import sys
import threading
import time
from urllib.parse import urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def percentile(valeurs: list[float], p: float) -> float:
    if not valeurs:
        return 0.0
    idx = min(len(valeurs) - 1, round(p / 100 * (len(valeurs) - 1)))  # round() rend deja un entier
    return sorted(valeurs)[idx]


class Ouvrier(threading.Thread):
    def __init__(self, host: str, port: int, chemin: str, n: int, resultats: list, erreurs: list) -> None:
        super().__init__(daemon=True)
        self.host, self.port, self.chemin = host, port, chemin
        self.n, self.resultats, self.erreurs = n, resultats, erreurs

    def run(self) -> None:
        try:
            conn = http.client.HTTPConnection(f"{self.host}:{self.port}", timeout=5)
        except OSError as exc:  # pragma: no cover
            self.erreurs.append(str(exc))
            return
        dures = []
        for _ in range(self.n):
            t0 = time.perf_counter()
            try:
                conn.request("GET", self.chemin, headers={"User-Agent": "bench/1.0", "Connection": "keep-alive"})
                reponse = conn.getresponse()
                corps = reponse.read()
                if reponse.status not in (200, 301, 302, 410):
                    self.erreurs.append(f"status {reponse.status}")
                if len(corps) == 0 and reponse.status in (301, 302):
                    pass  # normal : une redirection n'a pas de corps utile
            except (OSError, http.client.HTTPException) as exc:
                self.erreurs.append(type(exc).__name__)
                try:
                    conn.close()
                finally:
                    conn = http.client.HTTPConnection(f"{self.host}:{self.port}", timeout=5)
                continue
            dures.append((time.perf_counter() - t0) * 1000)
        self.resultats.append(dures)


def creer_qr(chemin_cible: str) -> str:
    """Insere un QR de test par l'ORM (meme base que le serveur lance a cote)."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    import django

    django.setup()
    from apps.accounts.models import User
    from apps.qr.models import QrCode

    user, _ = User.objects.get_or_create(email="bench@localhost", defaults={"plan": "pro"})
    user.set_password("bench-bench-bench")
    user.save()
    qr = QrCode.objects.create(owner=user, kind="dynamic", target_url=chemin_cible, label="banc de charge")
    return qr.slug


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="http://127.0.0.1:8000")
    ap.add_argument("--slug", default="", help="slug existant; sinon un QR de test est cree")
    ap.add_argument("--requests", type=int, default=20_000)
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--cold", action="store_true", help="reserve: ne pas rechauffer le cache avant la salve")
    args = ap.parse_args()

    if args.slug:
        slug = args.slug
    else:
        slug = creer_qr("https://kamcofarm.com/boutique")
        print(f"QR de test cree: /r/{slug}", file=sys.stderr)

    url = urlparse(args.target)
    host, port = url.hostname or "127.0.0.1", url.port or 80
    chemin = f"/r/{slug}"

    # Verification prealable: si le serveur ne repond pas, on ne rend pas 20 000 erreurs.
    with socket.create_connection((host, port), timeout=3):
        pass

    par_ouvrier = max(1, args.requests // args.concurrency)
    resultats: list[list[float]] = []
    erreurs: list[str] = []
    ouvriers = [Ouvrier(host, port, chemin, par_ouvrier, resultats, erreurs) for _ in range(args.concurrency)]
    t0 = time.perf_counter()
    for ouvrier in ouvriers:
        ouvrier.start()
    for ouvrier in ouvriers:
        ouvrier.join()
    total_secondes = time.perf_counter() - t0

    toutes = [d for salve in resultats for d in salve]
    if not toutes:
        print(json.dumps({"erreur": "aucune mesure", "erreurs": erreurs[:5]}, ensure_ascii=False))
        return 2
    rapport = {
        "chemin": chemin,
        "cible": args.target,
        "requetes": len(toutes),
        "concurrency": args.concurrency,
        "duree_s": round(total_secondes, 2),
        "rps": round(len(toutes) / total_secondes, 1),
        "p50_ms": round(percentile(toutes, 50), 2),
        "p95_ms": round(percentile(toutes, 95), 2),
        "p99_ms": round(percentile(toutes, 99), 2),
        "max_ms": round(max(toutes), 2),
        "moyenne_ms": round(statistics.fmean(toutes), 2),
        "erreurs": len(erreurs),
        "types_erreur": sorted(set(erreurs))[:5],
    }
    print(json.dumps(rapport, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
