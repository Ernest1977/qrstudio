# Chaque cible doit fonctionner sur le poste de dev comme dans la CI : les variables d'environnement
# sont posees ici et nulle part ailleurs.
VENV := PYTHONPATH=vendor
DJ  := $(VENV) DJANGO_SETTINGS_MODULE=config.settings.dev python3 manage.py
TEST := $(VENV) DJANGO_SETTINGS_MODULE=config.settings.test python3 -m pytest
# Attentes du garde-fou d'e-mail, surchargeables sans editer le Makefile :
#   make check-email EMAIL_ATTENTE="--attendu kamcofarm.com --politique quarantine"
EMAIL_ATTENTE ?=
MYPY_EXPOSE ?= qrcode PIL numpy redis

.PHONY: help install run test lint typecheck security check-deploy check-email check-email-json bench up down migrate shell clean
help:
	@grep -hE '^[a-z-]+:.*## ' Makefile | sed -E 's/^([a-z-]+):.*## /\1\t/' | awk -F'\t' '{printf "  %-16s %s\n", $$1, $$2}'

install:  ## installer les deps de dev dans ./vendor (sans venv, pour le bac a sable etrangle)
	python3 -m pip install --quiet --target vendor -r requirements-dev.txt

run:  ## serveur de dev (http://127.0.0.1:8000)
	$(DJ) runserver 0.0.0.0:8000

migrate:  ## appliquer les migrations (dev)
	$(DJ) migrate

shell:
	$(DJ) shell

test:  ## suite complete
	$(TEST) -q

lint:  ## ruff, y compris sur les tests
	$(VENV) python3 -m ruff check apps config tests scripts
	$(VENV) python3 -m ruff format --check apps config tests scripts

typecheck:  ## mypy sur les modules critiques
	# `vendor/` est un `pip install --target` : il contient des paquets entiers, dont Django. Le donner
	# tel quel comme chemin de recherche fait redescendre mypy dans le code de l'hote, qui remonte ses
	# propres erreurs (et un INTERNAL ERROR sur `db/models/expressions.py`). On ne lui expose donc que le
	# materiel qui nous sert a resoudre nos imports ; Django et DRF viennent du site-packages, non verifie.
	# La liste est volontairement courte et nommee : exposer tout `vendor/` faisait remonter les erreurs
	# des `.pyi` des autres (zxingcpp, rpds) et masquait les notres.
	@rm -rf .mypy-path && mkdir -p .mypy-path && for n in $(MYPY_EXPOSE); do \
		[ -e "vendor/$$n" ] && ln -sfn "$(CURDIR)/vendor/$$n" ".mypy-path/$$n" || true; done
	$(VENV) MYPYPATH=.mypy-path python3 -m mypy apps/common apps/qr/validators.py apps/analytics \
		apps/qr/promo.py apps/qr/art.py apps/qr/animation.py apps/qr/rendus.py apps/qr/promo_views.py \
		apps/qr/management/commands/sync_promo_usages.py --ignore-missing-imports --follow-imports=silent

security:  ## bandit, sans les avis de test
	$(VENV) python3 -m bandit -rq apps -x tests -ll

check-email:  ## garde-fou SPF/DKIM/DMARC de l'expediteur (1 = minimum manquant, 3 = non conforme)
	$(VENV) python3 scripts/check_email_gate.py $(EMAIL_ATTENTE)

check-deploy:  ## les avertissements que la prod refuse
	# Cle aleatoire, pas `'x'*64` : `security.W009` refuse une cle a un seul caractere distinct, et le but
	# de la cible est de mesurer l'application, pas l'echafaudage du Makefile. Les commentaires restent
	# hors de la ligne continuee : une ligne `#` au milieu d'une recette `\` coupe la suite, et la cible
	# partait alors avec les reglages de developpement — 5 avertissements qui n'existaient pas.
	$(VENV) DJANGO_SETTINGS_MODULE=config.settings.prod \
	DJANGO_SECRET_KEY=$$(python3 -c "import secrets; print(secrets.token_urlsafe(50))") \
	DATABASE_URL=postgres://qrs:qrs@127.0.0.1:5432/qrs REDIS_URL=redis://127.0.0.1:6379/0 \
	DJANGO_ALLOWED_HOSTS=qrstudio.kamcofarm.com \
	DJANGO_CSRF_TRUSTED_ORIGINS=https://qrstudio.kamcofarm.com \
	python3 manage.py check --deploy --fail-level WARNING

check-email-json:  ## le JSON brut du controle (journal, parsing CI)
	$(DJ) check_email_dns --json --skip-checks

bench:  ## banc de charge du chemin chaud (lance son propre serveur, puis le tue)
	$(VENV) DJANGO_SETTINGS_MODULE=config.settings.dev python3 manage.py runserver 127.0.0.1:8123 --noreload & \
	SERVER=$$!; sleep 3; \
	$(VENV) DJANGO_SETTINGS_MODULE=config.settings.dev python3 scripts/bench_redirect.py --target http://127.0.0.1:8123 --requests 20000 --concurrency 32; \
	kill $$SERVER

up:  ## pile complete (Docker requis)
	docker compose -f docker/compose.yml --env-file .env up --build

down:
	docker compose -f docker/compose.yml down -v
