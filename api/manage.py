#!/usr/bin/env python
"""Point d'entrée unique : le module de réglages se choisit par l'environnement.

    DJANGO_SETTINGS_MODULE=config.settings.dev  manage.py runserver
    DJANGO_SETTINGS_MODULE=config.settings.prod manage.py migrate
"""
import os
import sys
from pathlib import Path

# Installe les paquets du projet posés dans ./vendor (cas des environnements sans venv).
_vendor = Path(__file__).resolve().parent / "vendor"
if _vendor.is_dir() and str(_vendor) not in sys.path:
    sys.path.insert(0, str(_vendor))

def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Django est introuvable. Activer le venv, ou `pip install -r requirements.txt`."
        ) from exc
    execute_from_command_line(sys.argv)

if __name__ == "__main__":
    main()
