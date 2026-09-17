#!/usr/bin/env python3
"""Re-materialise les dependances dans `vendor/` quand le poste de dev n'a pas de venv.

Pourquoi ce script existe, et pourquoi il ne doit pas atterrir dans l'image finale : ce poste
installe les paquets avec `pip install --target vendor` et la synthese d'etat du sandbox ne conserve
pas les fichiers binaires de ce repertoire entre les sessions. `cryptography` perd alors son
`_rust.abi3.so`, `pygments` perd `lexers/`, `ruff` perd son executable — et le projet refuse de
demarrer sur un `ImportError` dont la cause n'a rien a voir avec le code (il aboutit a `allauth.mfa`,
qui importe les flux webauthn, qui exigent `cryptography`).

Sur une machine reelle, ce fichier est inutile : `pip install -r requirements.txt` installe des roues
completes. Il n'est appele ni par Docker, ni par la CI (qui passe par pip), ni par `make test`.

Usage :  python3 scripts/repair_vendor.py [--avec-rendu] [paquet ...]
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
VENDOR = RACINE / "vendor"

# Paquets dont l'absence casse l'import du projet, pas seulement le confort.
PAR_DEFAUT = ["cryptography", "pygments", "ruff", "mypy", "librt", "mypy_extensions", "typing_extensions"]

#: Les paquets qui ne cassent pas l'import du projet mais **condamnent une fonctionnalité vendue** : sans
#: `zxingcpp`, `/rendu/?fmt=art|gif` repond 503 (`art_verifieur_absent`) et le client croit le service
#: en panne. Ils font ~45 Mo de binaire, donc on ne les retelécharge que sur demande explicite.
POUR_RENDU = ["zxing-cpp", "opencv-python-headless"]


def _versions_installees() -> dict[str, str]:
    """La version deja declarée dans `vendor/*.dist-info`, pour retélécharger *la même* roue.

    Importer une version differente de celle que le reste de l'arbre attend est exactement le genre de
    réparation qui casse autre chose plus tard : on fige donc sur ce qui est déclaré.
    """
    sorties: dict[str, str] = {}
    for info in sorted(VENDOR.glob("*.dist-info")):
        nom, _, reste = info.name[: -len(".dist-info")].rpartition("-")
        if reste and reste[0].isdigit():
            sorties[nom.lower().replace("_", "-")] = reste
    return sorties


def _deverser(roue: str) -> int:
    with zipfile.ZipFile(roue) as z:
        for nom in z.namelist():
            if nom.endswith("/") or nom.startswith(("..", "/")) or ".." in Path(nom).parts:
                continue  # garde anti-traversée de chemin : on ne fait pas confiance a une roue
            destination = VENDOR / nom
            # Les roues placent les executables sous `<dist>.data/scripts/` : ils doivent finir dans
            # `vendor/bin/` pour que `python3 -m ruff` les trouve.
            if ".data/scripts/" in nom:
                destination = VENDOR / "bin" / Path(nom).name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with z.open(nom) as source, open(destination, "wb") as cible:
                shutil.copyfileobj(source, cible)
            if destination.suffix == ".so" or destination.parent.name == "bin":
                os.chmod(destination, 0o755)
    return 1


def reparer(paquets: list[str]) -> list[str]:
    faits: list[str] = []
    versions = _versions_installees()
    with tempfile.TemporaryDirectory() as temp:
        for paquet in paquets:
            cible = paquet.lower().replace("_", "-")
            version = versions.get(cible, "")
            demande = f"{cible}=={version}" if version else cible
            commande = [sys.executable, "-m", "pip", "download", "--no-deps", "--quiet", "--dest", temp, demande]
            if subprocess.run(commande, capture_output=True, text=True).returncode != 0:
                # Pas de réseau, ou version déclarée introuvable : on retente sans la contrainte de
                # version. Un échec ici doit rester bavard — c'est le seul moment ou l'on sait que
                # l'environnement est coupé d'internet.
                commande.pop(commande.index(demande))
                commande.append(cible)
                if subprocess.run(commande, capture_output=True, text=True).returncode != 0:
                    print(f"  ! {paquet}: telechargement impossible (reseau ?)", file=sys.stderr)
                    continue
            roues = glob.glob(f"{temp}/{cible}-*.whl") or glob.glob(f"{temp}/*.whl")
            if roues:
                _deverser(sorted(roues)[-1])
                faits.append(paquet)
    return faits


def deja_ok(paquet: str) -> bool:
    tests = {
        "cryptography": "from cryptography.hazmat.bindings._rust import exceptions",
        "pygments": "import pygments.lexers.diff",
        "mypy": "import mypy.__main__",
        "librt": "import librt",
        "ruff": "import ruff",
        "mypy_extensions": "import mypy_extensions",
        "typing_extensions": "import typing_extensions",
        # Sans sonde, ces deux-la seraient retelécharges a chaque passage (45 Mo de roues) : le test
        # d'import est ce qui rend `--avec-rendu` idempotent.
        "zxing-cpp": "import zxingcpp",
        "opencv-python-headless": "import cv2",
    }
    instruction = tests.get(paquet)
    if not instruction:
        return False
    environnement = {**os.environ, "PYTHONPATH": str(VENDOR)}
    return subprocess.run([sys.executable, "-c", instruction], capture_output=True, env=environnement).returncode == 0


def binaire_ruff_ok() -> bool:
    # `exists()` ne suffit pas : une copie sans droit d'execution laisse `python -m ruff` chercher le
    # binaire ailleurs et echouer avec une liste de chemins qui contient... ce fichier.
    binaire = VENDOR / "bin" / "ruff"
    return binaire.is_file() and os.access(binaire, os.X_OK)


def main() -> int:
    if not VENDOR.is_dir():
        print("vendor/ absent : ce script ne sert que sans venv (pip install --target vendor).", file=sys.stderr)
        return 1
    arg = [a for a in sys.argv[1:] if not a.startswith("-")]
    avec_rendu = "--avec-rendu" in sys.argv
    # Les arguments en ligne de commande s'AJOUTENT a la liste par defaut : `repair_vendor.py zxing-cpp`
    # en remplacement laissait `cryptography` casse (le `ImportError` d'`allauth.mfa` ressurgit alors au
    # demarrage du serveur, trois etages plus loin que la cause).
    demandes = [*PAR_DEFAUT, *(a for a in arg if a not in PAR_DEFAUT)]
    if avec_rendu:
        demandes += [a for a in POUR_RENDU if a not in demandes]
    manquants = [p for p in demandes if not deja_ok(p)]
    if "ruff" in demandes and not binaire_ruff_ok() and "ruff" not in manquants:
        manquants.append("ruff")
    if not manquants:
        print("vendor: rien a reparer")
        return 0
    faits = reparer(manquants)
    print(f"vendor: {len(faits)} paquet(s) re-materialise(s) — {', '.join(faits) if faits else 'aucun'}")
    return 0 if len(faits) == len(manquants) else 2


if __name__ == "__main__":
    raise SystemExit(main())
