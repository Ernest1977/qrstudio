#!/bin/sh
# Demarrage : migrations -> statiques -> commande fournee.
#
# Pas de `createcachetable` : le cache applicatif est Redis (voir CACHES dans settings/prod.py) et la
# table de session est creee par les migrations. Pas de `--check-unchanged` non plus : un deploiement
# peut contenir une migration sans changement de schema.
set -eu

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    # Un seul conteneur fait les migrations, les autres attendent : trois `migrate` en parallele sur
    # le meme schema Postgres produisent des `DuplicateTable` aléatoires au premier demarrage.
    python3 manage.py migrate --noinput
else
    python3 manage.py migrate --check >/dev/null 2>&1 || {
        echo "RUN_MIGRATIONS=0 mais le schema est en retard: lancer `python3 manage.py migrate` une fois." >&2
        exit 1
    }
fi

python3 manage.py collectstatic --noinput --clear
exec "$@"
