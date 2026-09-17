# ARCHITECTURE — QR Coding (version dynamique, multi-utilisateurs)

Statut : **à valider avant la première ligne de code** (sprint 0).
Périmètre : compte utilisateur (e-mail + Google), espace client (voir / modifier / supprimer ses QR),
QR **dynamiques** avec statistiques de scan et **pays d'origine**, back-office d'administration,
`django-admin`. Base : **PostgreSQL**. Front : le projet React déjà livré (`qr-coding-react/`),
auquel on ajoute l'authentification et le tableau de bord.

Choix retenus par vous : **Django 5 + DRF** · **statique + dynamique** · **dimensionnement chiffré
des trois scénarios de concurrence** · **cette spec d'abord**.

---

## 0. Avertissement de cadrage (à lire avant tout)

« 1 million de personnes connectées et utilisant le service **en simultané** » n'est pas une
contrainte de *framework* : c'est une contrainte d'**architecture** et de **budget matériel**.
Aucun back-end, Django ou Go, ne tient un million de connexions vivantes sur une machine ; ce qui
tient, c'est :

1. un service **sans état** (aucune session en mémoire processus) → n'importe quel nombre de
   répliques derrière un équilibreur ;
2. du **cache** et du **CDN** devant tout ce qui est lu souvent et écrit rarement ;
3. des **écritures hors du chemin critique** (file d'attente, insertions en lot) ;
4. un **pool de connexions** base de données (PgBouncer), sinon Postgres meurt à ~500 clients ;
5. la certitude que le front **ne frappe pas le serveur** quand il n'y a rien à chercher
   (aujourd'hui, la génération de QR est 100 % côté client : **ça reste vrai**, et c'est ce qui rend
   le scénario « 1 M d'utilisateurs » abordable du tout — le serveur ne voit jamais un pixel de QR).

Le point 5 est décisif : sur ce produit, un utilisateur *inscrit* qui crée 20 QR ne génère qu'une
dizaine de requêtes d'API. Le trafic réel, ce sont les **scans** (le redirect) et le **tableau de
bord**. On dimensionne donc ces deux flux, pas « le site ».

---

## 1. Pile technique retenue

| Couche | Choix | Pourquoi celui-là |
|---|---|---|
| Langage | Python 3.13 | écosystème Django, coût de maintenance bas |
| Cadre | **Django 5.2 LTS** (support ~avril 2028) | une seule base pour auth + ORM + migrations + **`django-admin`** que vous demandez + DRF ; Django 6.0 existe mais n'est pas LTS |
| API | **Django REST Framework** + `drf-spectacular` (OpenAPI 3) | schéma généré = client TypeScript front auto-généré |
| Point chaud | **vues asynchrones** (`async def`) Django ≥ 4.1, servies par `uvicorn` (workers `gunicorn -k UvicornWorker`) | le redirect n'a pas besoin du ORM synchrone : il lit Redis |
| Base | **PostgreSQL 17** (16 = repli hébergeur managé), **tableaux partitionnés par mois** | stats = écritures append + agrégats, le territoire de Postgres |
| Pool | **PgBouncer** (mode `transaction`) | 200 répliques × 10 connexions ne doivent pas arriver telles quelles à Postgres |
| Cache / files | **Redis 7** (ou Valkey) : cache, verrous, compteurs, `SESSION_ENGINE=cached_db`, limitation de débit | 1 M de compteurs incrémentés ≈ 100 k ops/s sur un nœud : très large |
| Tâches | **Celery** (broker Redis) + beat, ou `django-tasks` | agrégats, e-mails, purge RGPD, import/export |
| Stockage fichiers | **S3 compatible** (Hetzner Object Storage / OVH / MinIO en dev) | logos importés, exports CSV, jamais sur disque local |
| Geo-IP | **MaxMind GeoLite2-City** (`.mmdb` mappé en mémoire, `geoip2`) | 1 lookup ≈ 50–100 µs, aucune base SQL, aucune fuite hors UE si la base est locale |
| Auth Google | **django-allauth** (flux code + PKCE, **côté serveur**, jamais d'ID token accepté depuis le client seul) | éprouvé, gère e-mail déjà pris, 2FA (TOTP), vérification d'e-mail |
| Mots de passe | hachage **argon2** (`argon2-cffi`) | recommendation OWASP |
| Emails | `django-anymail` (Postmark/Brevo/SES au choix) | réinitialisation, vérification, alertes de quota |
| Front | le React existant, servi en **statique sur CDN** ; `fetch` en cookies same-site | pas d'évolution du front à déployer en conteneur |
| Conteneurs | `docker compose` (dev + petit prod mono-nœud), `Dockerfile` unique, **prêt pour Kubernetes/ECS** sans changer le code | vos 3 scénarios de charge sont les mêmes à un nombre de répliques près |
| Observabilité | `django-structlog`, Prometheus (`django-prometheus`), Grafana, **Sentry**, `silk` en dev seulement | un incident de scan-ingest doit se voir en 30 s |
| Sauvegardes | **pgBackRest** (PITR), exports objets chiffrés, rétention 30 j | une base de stats sans PITR est une base joueuse |

### Pourquoi pas Flask / FastAPI / Go

* **Flask** : il faut empiler 8 extensions (SQLAlchemy, Alembic, marshmallow, Flask-Login,
  Flask-Migrate…) et **il n'y a pas d'admin** ; à volume égal, plus de code à écrire que Django.
* **FastAPI** : excellent en pur asynchrone, mais l'admin, l'auth, les migrations et le back-office
  marketing sont à écrire à la main. Coût de sortie le plus élevé pour votre cahier des charges.
* **Go** : imbattable sur le *flux de redirect* (~5–10× le débit d'une vue Django pour 3–5× moins
  de RAM), et **c'est le plan B prévu** (§8) : on extraît `qredged`, un binaire Go de ~800 lignes,
  **uniquement** si les mesures montrent que Django sature. Tout le reste (admin, CRUD, stats,
  e-mails, RGPD) resterait en Django.

> Décision : **Django d'abord**. Le coût du « et si ça ne suffit pas » est de ~1 semaine de travail,
> pas une réécriture, grâce au découpage §3.

---

## 2. Ce que le serveur fait — et ne fait pas

| Action utilisateur | Où elle se passe | Requêtes serveur |
|---|---|---|
| Choisir un type, remplir, voir l'aperçu, télécharger PNG/SVG | **100 % navigateur** (code livré) | 0 |
| Créer un compte, se connecter (e-mail ou Google) | API | 1 à 3 |
| « Enregistrer ce QR » (mode dynamique) | API (`POST /api/qr/`) | 1 |
| Modifier l'URL cible d'un QR existant | API (`PATCH /api/qr/{id}/`) | 1 |
| Ouvrir son tableau de bord, ses stats | API + cache | 2 à 6 |
| **Quelqu'un scanne un QR dynamique** | `GET /r/{slug}` → **302** | **1, ~1 ms, 0 écriture SQL synchrone** |
| Scinder les stats par pays | ingest asynchrone | 0 côté requêteur |

Le QR **statique** reste tel quel : personne ne peut le compter (aucune donnée ne transite par le
serveur), c'est dit à l'utilisateur dans l'interface pour éviter toute déception.

---

## 3. Découpage qui rend l'extension possible

```
                    ┌──────────────── CDN (Cloudflare / Hetzner) ──────────┐
  navigateur ───────┤  assets React (statique) + QR PNG mis en cache        │
                    └───────────────────────┬─────────────────────────────┘
                                            │ only /api, /admin, /r/, /account
                    ┌───────────────────────▼─────────────────────────────┐
                    │ nginx / traefik  (TLS, gzip/brotli, rate-limit, XFF) │
                    └───────┬───────────────────────────────┬─────────────┘
                            │ /api, /admin, /account        │ /r/{slug}
                  ┌─────────▼──────────┐        ┌───────────▼──────────────┐
                  │ Django (N répliques)│        │  view redirect (async)   │
                  │ DRF · sessions ·    │        │  Redis MGET → 302        │
                  │ admin · Celery beat │        │  XADD scan → file        │
                  └───────┬─────┬───────┘        └───────────┬──────────────┘
                          │     │                            │
                  ┌───────▼─┐ ┌─▼─────────┐   ┌─────────────▼──────────────┐
                  │PgBouncer│ │  Redis     │   │ worker d'ingestion (Go/Py) │
                  └───┬───┘ │ cache,compt.│   │ COPY batch → scan_events   │
              ┌───────▼───┐ │ flux XADD   │   │ + upsert qr_daily_stats    │
              │ Postgres 17│ └────────────┘   └────────────────────────────┘
              │ partitions│
              │ répl. lecture│──► API stats (agrégats) : les gros graphiques ne
              └────────────┘     touchent jamais le nœud primaire
```

**Règle d'or** : le chemin `GET /r/{slug}` (celui qui porte toute la charge) ne fait **ni ORM, ni
SQL, ni journal en écriture synchrone**. Il lit un cache Redis (TTL 60 s + invalidation par signal),
répond 302, et pousse un événement dans un **Redis Stream**. Tout le reste (géo, compteurs, agrégats,
répartition par pays) est consommé en arrière-plan et **peut avoir du retard sans casser le scan**.

---

## 4. Modèle de données

Noms et types, tels qu'ils seront en migrations (`ulid`/`shortid` = base62, pas de UUID dans une URL
de redirect : trop long).

### `accounts.User` (abstract `AbstractUser`)

| champ | type | notes |
|---|---|---|
| `id` | BIGSERIAL | |
| `email` | CITEXT unique | identifiant de connexion |
| `username` | — | supprimé (`USERNAME_FIELD='email'`) |
| `password` | varchar | argon2, vide si compte 100 % Google |
| `date_joined`, `last_login` | timestamptz | |
| `is_email_verified` | bool | |
| `is_active`, `is_suspended`, `suspension_reason` | | |
| `locale`, `timezone` | | front React piloté par `locale` |
| `plan`, `plan_until` | enum | `free/pro/team` — quotas (§7) |
| `totp_secret` (chiffré), `backup_codes` | | 2FA optionnelle |
| `consent_tracking` | timestamptz NULL | **base légale du suivi par pays** (§9) |
| `marketing_opt_in` | bool | |

`UserSocialAuth` (fourni par allauth) stocke le `uid` Google + e-mail vérifié. Un compte Google dont
l'e-mail correspond à un compte existant est **rattaché**, pas dupliqué.

### `qr.QrCode`

| champ | type | notes |
|---|---|---|
| `id` | ULID | |
| `owner` | FK User, index | |
| `kind` | `static` \| `dynamic` | mode choisi à la création (votre choix n°3) |
| `slug` | varchar(10) unique, index | `base62(8)` → 47 bits × refonte sur collision ; **jamais** auto-incrémenté (anti-énumération) |
| `type_id` | varchar(16) | une des 22 clés du front (`url`, `bcard`, `wifi`, …) |
| `payload` | text | **contenu statique** (vCard, Wi-Fi…) — jamais exposé en clair dans une URL publique |
| `target_url` | text | **destination du redirect** (mode dynamique) |
| `design` | jsonb | couleurs, style, ecc, logo (référence objet S3), marge… — le front régénère l'image lui-même |
| `label`, `notes` | text | organisation par l'utilisateur |
| `is_public`, `password` (hash NULL) | | QR partagé publiquement / protégé |
| `is_active`, `archived_at`, `deleted_at` | | suppression **logique** (un QR imprimé peut ressusciter) |
| `redirect_mode` | `302` \| `301` \| `307` | 302 par défaut (le comptage marche, le cache n'y survit pas) |
| `utm_mode` | bool | ajout UTM au vol, si l'utilisateur le demande |
| `created_at`, `updated_at` | timestamptz | |
| `scan_count_total` | bigint | **compteur dénormalisé** (Redis → DB toutes les 60 s) |

Contraintes : `CHECK (kind='static' OR target_url ~ '^https?://')` — **pas d'`//evil.com` ni de
`javascript:`** (protection open-redirect, §7).

### `qr.QrVersion` (historique de modification)

`qr_id`, `changed_at`, `actor`, `diff jsonb` → « mon QR est cassé depuis hier » devient
diagnostiquable ; permet un `GET /r/{slug}?at=` interne et l'annulation d'une modification.

### `analytics.ScanEvent` — partitionné par mois, sous-partition pays

| champ | type | notes |
|---|---|---|
| `ts` | timestamptz | clé de partition |
| `qr_id` | int (index) | |
| `day` | date (généré) | pour les agrégats |
| `country_code` | char(2) NULL | `geoip2`, **seule granularité stockée par défaut** |
| `region` / `city` | | **opt-in**, désactivé si `consent_tracking` est absent |
| `ip_trunc` | inet | IPv4 masquée /24, IPv6 /48, **purgée après 24 h** |
| `user_agent_hash` | bit(128) | empreinte, pas la chaîne brute (contenu variable = donnée perso) |
| `referer_domain` | varchar(128) NULL | |
| `device_class` | enum | mobile / desktop / tablette / bot |
| `is_bot` | bool | listes + heuristic UA ; **exclu des stats affichées** |
| `status` | smallint | 302/404/410/429 |
| `scan_id` | ulid unique | déduplication des rejeux (retry client) |

Écriture : **`COPY` en lot** (1 000 lignes ou 200 ms). Pas d'`INSERT` unitaire sur un chemin chaud.

### `analytics.QrDailyStats` (agrégat, la source des graphiques)

`qr_id`, `day`, `country_code`, `scans`, `unique_visitors_approx` (HyperLogLog Redis → bigint),
`scans_mobile`… **UNIQUE (qr_id, day, country_code)**. Reconstruit par Celery beat, et par
`REFRESH` incrémental. Un tableau de bord sur 12 mois lit **cette table** (réplique de lecture),
jamais `scan_events`.

### Index clés

```sql
CREATE INDEX qr_owner_active_idx  ON qr_qrcode (owner_id, deleted_at) WHERE deleted_at IS NULL;
CREATE INDEX qr_slug_idx          ON qr_qrcode (slug) WHERE is_active;
CREATE INDEX scan_day_country_idx ON analytics_qrdailystats (qr_id, day DESC, country_code);
CREATE INDEX scan_events_ts_qr_idx ON "analytics_scanevent_202609" (qr_id, ts DESC);
```

---

## 5. API (contrat)

Préfixe `/api/v1`, versions sémantiques, OpenAPI sur `/api/schema`. Cookies : session Django
(`SameSite=Lax`, `Secure`, `HttpOnly`) + **CSRF** (`X-CSRFToken` lu du cookie, même domaine — pas de
JWT par défaut, cf. §10 pour la vraie raison).

### Auth (`/api/v1/auth/`, django-allauth + DRF)

| Méthode & route | Description |
|---|---|
| `POST /auth/register` | e-mail, mot de passe (politique zxcvbn ≥ 4), `consent_tracking` booléen |
| `POST /auth/verify/verify` | code 6 chiffres, 15 min, 5 essais, puis verrouillage 30 min |
| `POST /auth/login` | e-mail + mot de passe ; **10 essais / IP / 5 min** (Redis) |
| `GET /auth/google/url` → `GET /auth/google/callback` | **flux code + PKCE, state anti-CSRF, nonce**, échange côté serveur uniquement ; `POST /auth/google/exchange` si vous préférez un front OAuth (alors vérification `aud`+`iss`+ JWKS côté serveur) |
| `POST /auth/password/reset/*` | 2 routes, e-mail générique identique que le compte existe ou non (anti-énumération) |
| `POST /auth/logout` | rotation de session, purge Redis |
| `GET /auth/me` | profil + quota restant + projet de facturation |

### QR (`/api/v1/qr/`)

| Route | Rôle |
|---|---|
| `GET /qr/?type=&q=&archived=&page=` | liste paginée (curseur `ulid`, pas d'offset profond) |
| `POST /qr/` | crée statique ou dynamique ; **quota** vérifié (§7) |
| `GET /qr/{id}/` | détail : payload, design, `target_url`, URL courte, mini-stats 7 j (du cache) |
| `PATCH /qr/{id}/` | **la mise à jour** (URL cible, design, libellé, pause) ; écrit `QrVersion`, invalide le cache Redis du slug en < 100 ms |
| `DELETE /qr/{id}/` | suppression logique (`deleted_at`) ; `?hard=true` sur demande expresse = purge RGPD |
| `POST /qr/{id}/restore/` | remise en service (le QR imprimé redevient valide) |
| `POST /qr/{id}/duplicate/` | clonage avec autre design |
| `GET /qr/{id}/render.png?scale=4` | **option** : rendu serveur pour e-mails/PDF (le PNG de l'aperçu continue d'être fait par le navigateur, gratuitement) |

### Statistiques (`/api/v1/qr/{id}/stats/`)

```
GET /qr/{id}/stats/?from=2026-08-11&to=2026-09-11&group_by=day|hour|country|device|ref
→ {
  "series": [{"day":"2026-09-01","scans":412,"unique":388}],
  "countries": [{"code":"IT","name":"Italie","scans":1204,"share":0.41},
                {"code":"FR","name":"France","scans":903,"share":0.31}],
  "devices": {...}, "refs": [...],
  "cache_until": "2026-09-11T09:31:00Z"
}
```
`group_by=country` est **exactement** votre demande « les pays depuis lesquels chaque QR a été
scanné » : il sort de `QrDailyStats` (agrégat), donc la réponse coûte ~1 `SELECT` sur quelques
centaines de lignes quel que soit le volume de scans bruts. Cache 30 s par (QR, plage, groupe).

### Le redirect (le seul endpoint public)

```
GET /r/{slug}
  1) Redis GET "qr:target:{slug}"        (TTL 60 s, invalidé à l'écriture)
  2) miss → SELECT is_active, target_url, redirect_mode, owner.consent_tracking
             FROM qr_qrcode WHERE slug = %s   (via un pooling, requête < 1 ms, index unique)
  3) XADD "stream:scans" {slug, ts, ip, ua, referer}   (fire and forget, 1 commande Redis)
  4) 302 Location: target_url + headers :
       Cache-Control: no-store
       Referrer-Policy: strict-origin-when-cross-origin
       X-Robots-Tag: noindex
```
Cas tordus traités explicitement : slug inconnu → **410** + page de marque (pas de 500) ; QR en
pause → page « temporairement indisponible » ; QR supprimé → 410 ; quota utilisateur dépassé → le
scan **compte** mais l'ingest agrégé est échantillonné (le compteur primaire reste exact) ;
`HEAD /r/{slug}` → 200 sans ingest (les scanners de liens d'entreprise ne polluent pas les stats) ;
`?_probe=1` → pas d'ingest (monitoring).

### Back-office et `django-admin`

| Zone | Qui | Contenu |
|---|---|---|
| **`/admin/`** (django-admin, celui que vous demandez) | équipe, 2FA **imposée** | modèles ci-dessus avec : `readonly` sur `ScanEvent`, `list_filter` par pays/plan, **actions** : suspendre un compte, geler un QR abusif, renvoyer l'e-mail de vérification, reconstruire les agrégats d'un QR, exporter une sélection en CSV (asynchrone, limité à 500 k lignes, lien S3 signé 15 min), impersonation **audité** (`AdminImpersonationLog`, sortie après 30 min, bannière rouge sur le front) |
| `/manage/` back-office métier | équipe | pages dédiées là où l'admin ne suffit pas : top QR par scans, modération des destinations signalées, états des files Celery, litiges RGPD (export/suppression d'un compte en un clic, avec piste d'audit), bascule de plans, bannières d'incident |
| `management commands` | ops | `rebuild_daily_stats --since`, `purge_expired_ip`, `prune_partitions`, `check_redirect_consistency` |

Sécurité de l'admin : URL renommée (`/manage-9f2/`) **en plus** de l'authentification (le renommage
n'est pas une sécurité, c'est un filtre à bots), `LoginRequiredMiddleware`, IP allowlist possible,
CSRF durci, sessions admin séparées des sessions utilisateurs (`SESSION_COOKIE_PATH`), audit de
chaque `LogEntry`.

---

## 6. Front : ce qu'on ajoute à l'app livrée

`qr-coding-react/` reste la base (le studio est déjà testé). Ajouts :

```
src/api/            client fetch (CSRF, refresh, erreurs typées), schémas TS générés depuis OpenAPI
src/auth/           AuthProvider (session, quota, 2FA), <RequireAuth>, pages /register /login
                    /forgot /reset /verify + bouton « Continuer avec Google » (callback route)
src/dashboard/      liste des QR (recherche, archivage, duplication, restauration),
                    édition d'une destination, mise en pause
src/stats/          graphique scans/jour, table des pays (drapeaux + %), filtres de période
src/account/        profil, sécurité (changer mdp, activer TOTP, déconnecter les appareils),
                    RGPD (mes données / supprimer mon compte), facturation (plus tard)
src/studio/         le composant actuel, avec en plus : [ ] QR dynamique (URL courte + verrou de
                    modification) et [x] QR statique (comme aujourd'hui)
```

Trois exigences produits qui en découlent, à valider :

1. **Un QR dynamique doit être éternellement stable** : `slug` ne change jamais, même si l'URL cible
   change 200 fois. Toute l'utilité du produit tient à ça.
2. **Une impression ratée ne doit pas être une catastrophe** : `GET /r/{slug}` renvoie une page HTML
   lisible si le QR est suspendu (l'utilisateur comprend au lieu de jeter l'étiquette).
3. **Le mode statique reste sans compte** : on ne crée pas un compte pour un QR Wi-Fi personnel.
   Seuls les QR dynamiques/stats exigent l'inscription.

---

## 7. Quotas, anti-abus, et ce qui coûte cher à protéger

| Sujet | Règle |
|---|---|
| QR créés | free 25 dynamiques / illimité en statique-local ; pro 5 000 ; durcissement possible par plan |
| Scans | pas de plafond par QR (sinon le service ment) ; **plafond par IP** de requêtes `/r/` : 300/min, puis 429 + `Retry-After` |
| Scans en rafale / scraping | le même `(slug, ip_trunc, ua_hash)` dans une minute = 1 seul scan compté (fenêtre Redis) |
| Bots | liste de 250 UA + `botdetection` légère ; ils ne sont pas comptés, et `HEAD` n'ingère pas |
| Open redirect | `target_url` validée : schéma http/https, pas de `//`, pas de localhost/**ni de plages privées** (on résout le nom à l'enregistrement et à chaque modif : `169.254.169.254`, `10/8`, `127/8`, IPv6 ULA refusés — **SSRF**, parce que les scanners de sécurité d'entreprise viendront taper cette URL) |
| Contenus illicites | le QR public ne stocke pas de fichier ; la destination peut être signalée → file de modération admin (`QrReport`), gel temporaire automatique après 5 signalements |
| Inscriptions de masse | e-mail vérifié obligatoire + hCaptcha/Turnstile seulement si le débit d'inscription dépasse 5/min/IP |
| API publique | limitation par clé (token personnel `qrc_…`) pour les clients mobiles, sinon mêmes quotas |
| Secrets | `django-environ`, jamais de `.env` commité ; rotation des clés Google documentée |
| Dépendances | `pip-audit` + Dependabot + `safety` en CI, verrou `uv`/`pip-tools` |

---

### Grille retenue (décision du 2026-09-14, remplacant les valeurs provisoires du tableau)

| Palier | Prix mensuel | Contenu |
|---|---|---|
| Gratuit | 0 € | studio, QR statiques, export PNG/SVG — 20 QR |
| Standard | 2,99 € | + score de scannabilité, cadres CTA, export PDF, thèmes sectoriels — 500 QR |
| Premium | 8,99 € | + QR dynamiques et analytics — 5 000 QR, 100 dynamiques/30 j, historique 90 j |
| Entreprise | 15,99 € | + cartes de visite connectées, QR multi-liens, API développeurs — dynamique 2 000/30 j, historique 365 j, statiques illimités |

Ce qui en découle pour l'infrastructure, et c'est la raison pour laquelle la grille est écrite avant le
code : **le poste qui coûte cher commence au palier Premium**. Gratuit et Standard ne touchent ni le
slug, ni la redirection, ni l'ingest — 700 req/s de/scans ne peuvent donc venir que des comptes payants,
ce qui rend le scénario A soutenable sans marge heroic. Les 402 `plan_required` / `quota_exceeded` sont
la traduction API de cette grille (implémentés, testés dans `api/tests/test_plans.py`).

## 8. Dimensionnement chiffré — les trois scénarios

Estimations d'ingénierie, à confirmer par un banc de charge (le sprint 1 en inclut un, cf. §11) ; les
ordres de grandeur viennent de mesures publiques usuelles (nginx ~1 M de connexions oisives en RAM
légère, Redis ~100 k ops/s par nœud, PgBouncer + `COPY` en lot sur Postgres ~50–200 k lignes/s).

### Scénario A — « 1 M de comptes inscrits », pics ~2–5 k req/s  ⟵ **le plus probable**

Trafic retenu : 100 k utilisateurs actifs/jour et **1,2 M de scans/jour**, soit 14 req/s en
moyenne et, avec une concentration de 50× sur l'heure de pointe, **~700 req/s de redirect**
(+ ~50 req/s d'API, ~20 req/s de stats).

| Composant | Chiffre | Verdict |
|---|---|---|
| Redirect | 700 rps × 1 ms (Redis) | **3 workers uvicorn** suffisent (marge ×10) ; CPU < 30 % |
| Ingest | 1,2 M lignes/j = 14 lignes/s en moyenne | rien de spécial : Postgres les avale en un `COPY` |
| API CRUD | 50 rps, dont 90 % de cache | négligeable |
| Postgres | 16 Go RAM, 4 vCPU, SSD NVMe 200 Go (12 mois de bruts ≈ 45 Go compressés) | un nœud + 1 réplique de lecture |
| Redis | 1,5 Go (compteurs + cache + flux) | un nœud, AOF tous les 60 s |
| Coût indicatif | VPS/serveur dédié Hetzner ou OVH + Cloudflare gratuit : **~80–250 €/mois**, + e-mails et sauvegardes | |

**Conclusion : Django + Postgres + Redis, 3 machines, sans microservice Go.** Aucun risque de
ralentissement à ce niveau ; c'est le cas où l'architecture du §3 est déjà surdimensionnée.

### Scénario B — 1 M de *scans par jour* (≈ 14 rps moyens, ~200–700 en pic)

Encore plus simple : 1 réplique Django (2 workers), Redis, Postgres 8 Go. **Coût total ≈ 40–90
€/mois.** Le seul vrai travail ici est la **partition mensuelle + purge**, sinon `scan_events`
gonfle (1 M/j × 250 o ≈ 90 Go/an en brut) et les agrégats ralentissent. Enveloppe : rétention brute
**13 mois** puis agrégats seuls, partitions détachées et archivées en Parquet sur S3.

### Scénario C — 1 M de **connexions navigateur ouvertes en même temps** (compteurs « live »)

C'est le seul scénario qui change d'échelle, et **le débit de requêtes est le piège, pas les
sessions** :

* 1 M de sessions HTTP oisives tenues par nginx ≈ **1–2 Go de RAM** : faisable, mais sans intérêt.
* 1 M de **sondeurs** qui rafraîchissent les stats toutes les 30 s = **33 333 req/s** → là, il faut
  ~25–40 workers d'API (ou du cache edge) et Postgres en répliques multiples : **2 000–4 000 €/mois**.
* 1 M de **websockets** (compteur qui monte en direct) : ~100–200 kB/connexion en Django
  Channels → **100–200 Go** : impossible. En Go (`nhooyr`/`gorilla`) ~25–50 kB → **25–50 Go**, soit
  4 à 6 machines dédiées + une couche de fan-out (Redis `SUBSCRIBE` par shard de 100 k) →
  **3 000–6 000 €/mois** avant le reste.

Recommandation chiffrée pour le live : **ne pas tenir 1 M de sockets**. Diffuser un compteur public
**depuis le bord** :

1. les totaux deviennent un **fichier JSON statique** écrit par le worker d'ingest toutes les 5 s
   (`/live/{slug}.json`, `Cache-Control: max-age=5, CDN`), lu par le front → le serveur d'origine ne
   voit plus qu'une requête par seconde et par objet chaud, et 1 M d'onglets deviennent gratuits ;
2. le détail (graphes, pays) reste sur l'API, avec un **rafraîchissement conditionnel** (`ETag`) et
   une fenêtre glissante de 30 s : l'utilisateur du tableau de bord est rarement 1 M à la fois ;
3. **si** un vrai temps réel s'impose (alerte « 10 000ᵉ scan »), on ajoute `qredged` **en Go**
   (§1) : SSE + Redis pub/sub, 2–3 machines de 16 Go. C'est un projet séparé d'une semaine, activé
   **sur mesure**, pas une hypothèse de départ.

Seuil de bascule explicite, à mesurer : si `GET /r/` dépasse **~5 000 rps** soutenus (ou si le p99
dépasse 30 ms avec 1 worker Redis à plus de 60 % CPU), on extrait le redirect dans `qredged`.

---

## 9. RGPD (vous êtes dans l'UE — ce n'est pas décoratif)

Une **adresse IP est une donnée personnelle** (CJUE C-582/14). Un scan par un visiteur UE de votre
QR = **traitement de données personnelles pour de la mesure d'audience**. Donc :

1. **Finalité et base légale** : le suivi par pays n'est activé que si (a) le propriétaire du QR a
   coché `consent_tracking` à la création **et** dans ses réglages de compte, (b) le visiteur a
   accepté les cookies de mesure sur la **page de destination** quand elle est nôtre — si la
   destination est un site tiers, la responsabilité du scan côté tiers leur incombe, pas à nous, et
   la mention l'explique.
2. **Minimisation par défaut** : **pays** uniquement (`country_code`), pas de ville, pas d'UA brut,
   pas de cookie publicitaire, pas d'empreinte persistante côté visiteur.
3. **Pseudonymisation** : IP tronquée (/24 v4, /48 v6), purgée après **24 h** (job Celery,
   vérifiable) ; l'empreinte UA est un `bit(128)` non réversible.
4. **Durée de vie** : agrégats quotidiens 24 mois, bruts 13 mois, journal d'audit admin 3 ans,
   compte supprimé → purge des QR et de leurs scans **sous 72 h** (`soft` → `hard`), export
   JSON/CSV de ses données en un clic (article 20).
5. **Registre des traitements** (`records.json` généré depuis les modèles — `django-gdpr` ou
   fichier versionné), **DPA** à proposer aux clients pro, sous-traitants listés (hébergeur =
   Hetzner/OVH **UE**, MaxMind = fournisseur de base, e-mails = UE si possible : Brevo/Postmark UE).
6. **Transferts hors UE** : si le Google OAuth est utilisé, `prompt=select_account` + hébergement du
   callback en UE ; **aucune donnée de scan n'est envoyée à Google Analytics** (pas de GA par défaut
   — nos stats le remplacent).
7. **Sécurité** : chiffrement au repos (chiffres LUKS du volume ou chiffrement géré par
   l'hébergeur), TLS 1.3 + HSTS + `includeSubDomains`, argon2, TOTP admin obligatoire, secrets
   hors image, CI qui refuse `DEBUG=True`, audits de dépendances.
8. **Politique de confidentialité + mentions** : je livre le texte à adapter (§11 livrable) + bandeau
   de consentement **sans cookie** pour la mesure (nos mesures fonctionnent sans cookie, c'est un
   argument commercial réel ici).

`Cookie` : seules les sessions en posent ; pas de cookie tiers → pas de bandeau pour la
fonctionnalité, bandeau requis seulement si vous ajoutez un service de pub/mesure tiers.

---

## 10. Décisions tracées (et leurs renoncements)

| Décision | Renoncement assumé |
|---|---|
| Cookies de session same-site plutôt que JWT | on renonce à « la même API pour une app mobile »… en fait non : **clés API personnelles** pour les clients, sans JWT. Moins de surface (pas de refresh volé, pas de révocation impossible). Le jour où vous avez 50 k devices mobiles, on ajoute des JWT courts + deny-list Redis. |
| Redis devant Postgres pour le redirect | on ajoute un mode dégradé : si Redis tombe, la vue tape Postgres (lent mais vivant) et l'ingest s'accumule dans une file disque. Décision explicite : **le scan ne doit jamais échouer** parce que le cache est KO. |
| `302` plutôt que `301` | on renonce au cache CDN des redirects (301 se mettrait en cache **et** ne serait plus compté — le produit mourrait). `Cache-Control: no-store` est donc obligatoire. |
| Pas de rendu serveur du QR par défaut | le navigateur le fait déjà parfaitement (tests 39/39) → on économise un parc CPU ; le rendu serveur reste disponible pour les e-mails. |
| `scan_events` dans Postgres | on renonce à ClickHouse… **jusqu'à ~50 M de scans/jour** (≈ 600 rps d'ingest) : à ce palier, TimescaleDB (extension Postgres, même requêtes) est le premier palier, puis ClickHouse. Le modèle (§4) est déjà compatible Timescale. |
| Une base PostgreSQL unique en dev | en prod : PgBouncer + réplique de lecture + PITR dès le premier jour, pas « plus tard ». |
| Admin Django plutôt que back-office React dédié | gain de vitesse énorme ; on l'étend (`/manage/`) plutôt que de le réécrire. |

---

## 11. Plan d'exécution (sprints), avec critères d'acceptation mesurables

| # | Livrables | Critères d'acceptation |
|---|---|---|
| **0 (ce document)** | spec validée | vos 5 signatures : quotas, granularité géo par défaut (pays), rétention, plan de coûts choisi (A/B/C), domaine des liens courts |
| **1** | `api/` : projet Django + compose (Postgres, Redis, PgBouncer, MinIO), `User`, allauth Google, inscription/vérification/réinitialisation, `/admin`, CI (ruff, mypy, bandit, `pytest`), **banc de charge `k6` du `/r/`** | 200 tests unitaires+d'intégration, `docker compose up` fonctionne à froid, p99 `/r/` < 15 ms à 1 000 rps sur une machine de dev |
| **2** | CRUD QR (statique + dynamique), `slug`, `QrVersion`, quota, invalidation de cache, rendu PNG serveur | « je modifie la destination, le QR déjà imprimé renvoie la nouvelle URL en < 1 s » vérifié par test e2e navigateur |
| **3** | `GET /r/{slug}` asynchrone + Stream Redis + worker d'ingest + partitions + `QrDailyStats` | 1 M de scans simulés en 1 h sur une machine ; 0 perte ; agrégats exacts à ±0 (dédup `scan_id`) ; `pg_stat` < 20 % d'attente |
| **4** | Endpoint stats + tableau de bord React (liste, édition, pays, graphes, période) | stats par pays affichées et **testées contre un IP fixe connu** (jeu de données de référence MaxMind) |
| **5** | Back-office `manage/` (modération, litiges RGPD, export, impersonation auditée) + RGPD (purge 24 h, suppression en cascade, export) | les 8 exigences §9 cochées, revue de sécurité checklist passée, suppression de compte → 0 ligne restante (test) |
| **6** | Durcissement à la charge choisie : cache edge du JSON live, `qredged` Go si §8.C, réplique de lecture, alerting | rapport de bench de **3 paliers** fourni + plan de bascule écrit |

Chaque sprint arrive avec ses tests, ses migrations réversibles, et une note de déploiement.

### Statut du sprint 1 (exécuté, mesuré le 2026-09-11)

Le squelette complet est écrit dans `api/` : `config/settings/{base,dev,prod,test,bench,ci}.py`,
`apps/{accounts,qr,redirect,analytics,common}`, les migrations des 3 apps, l'admin durci, le
back-office `/manage/`, les 5 commandes d'exploitation, `docker/`, `.github/workflows/ci.yml`,
`scripts/bench_redirect.py`, `README.md` (déploiement Hostinger).

Critères d'acceptation, tels qu'ils tiennent réellement :

| Critère | État |
| --- | --- |
| Suite de tests verte | **135 tests passés**, plus `ruff`, `mypy` (56 fichiers, 0 erreur), `bandit -ll` (0), `check --deploy` sous réglages prod (0 problème), `makemigrations --check` (aucun changement) |
| `docker compose up` à froid | fichiers écrits (`docker/{Dockerfile,compose.yml,entrypoint.sh,conf/*}`), **non exécutés** : pas de Docker dans l'environnement de développement |
| `p99 < 15 ms` à 1 000 req/s sur `/r/` | **non prouvé ici**. Mesuré : 0,6–0,8 ms de travail applicatif par scan en hit de cache (≈1 290 req/s par cœur), mais le harnais de ce poste (2 cœurs, `runserver`, client dans le même bac à sable) plafonne à ~90 req/s quel que soit l'endpoint, y compris une vue de contrôle — le chiffre à rejouer sur le VPS avec k6 |
| Aucun SQL synchrone sur le chemin chaud | **prouvé par test** : `CaptureQueriesContext` compte **0 requête** sur le deuxième scan d'un même slug, et 0 requête pour un slug mal formé |

Ce que la phase de tests a corrigé dans le code (et non dans les tests) : `matches()` renvoyait
`True` au lieu du résultat de la comparaison de code — n'importe quel code de vérification validait un
compte ; les adresses IP littérales privées (`127.0.0.1`, `169.254.169.254`) passaient quand le
contrôle DNS était désactivé ; l'enregistrement d'inscription révélait l'existence d'un compte par la
**forme** du JSON ; la garde MFA de l'admin n'était pas montée dans la chaîne de middlewares ; le
fichier `.env` écrasait les variables d'environnement réelles (dont `DJANGO_ENV`) ; la voie SQL
d'agrégation PostgreSQL n'était branchée nulle part ; la sonde `/healthz` serait morte en 301 à
cause de la redirection TLS ; le `noqa` et l'annotation `QuerySet` de `soon_to_expire` mentaient.

### Statut des sprints 2 à 5 (exécuté, mesuré le 2026-09-13)

| Sprint | Ce qui tient | Ce qui reste |
| --- | --- | --- |
| **2** — CRUD, slug, `QrVersion`, quota, cache, rendu | Le critère d'acceptation est **mesuré** : `test_le_QR_imprime_suivant_la_nouvelle_destination_en_moins_d_une_seconde` chronomètre le premier scan après la modification et échoue au-delà de 1 s (il passe en quelques millisecondes, l'invalidation se faisant au commit). Rendu serveur `png`/`svg` posé (`apps/qr/render.py`, `/api/v1/qr/{id}/image/`), dimensions vérifiées dans l'en-tête IHDR, et une propriété testée : **l'image d'un QR dynamique est identique octet pour octet avant et après changement de destination** — c'est ce qui rend un flyer re-imprimable inutile | Le test navigateur (Playwright) du parcours complet arrive avec le câblage du front sur l'API (§ ci-dessous) |
| **3** — `/r/` asynchrone, stream, worker, partitions, agrégats | Chemin chaud à **0 requête SQL** en hit de cache (compté par `CaptureQueriesContext`), ingest idempotent par `(scan_id, ts)`, `XACK` seulement après écriture, agrégats alignés entre la voie Python et la voie SQL (`COUNT(*) FILTER (WHERE NOT is_bot)` des deux côtés) | Les 1 M de scans simulés en 1 h, `pg_stat`, et le DDL de partition **exécuté** : ni Docker ni Postgres ici |
| **4** — endpoint stats + tableau de bord | `GET /api/v1/qr/{id}/stats/` lit l'agrégat, comble les trous de la série, traduit les codes pays, et expose `tracking_allowed` (le pays n'apparaît que si le propriétaire a consenti) | Le test « IP fixe connue ↔ pays attendu » avec le jeu de référence MaxMind, et **l'UI React** : le front livré tourne encore sur `localStorage`, il n'appelle pas l'API |
| **5** — back-office + RGPD | Export de portabilité JSON versionné, effacement complet avec re-authentification et **0 ligne restante** vérifiée sur les 7 tables, purge IP à 24 h, `DROP PARTITION` au-delà de 13 mois, journal d'impersonnalisation | Les 8 exigences du §9 passées en revue humaine, et la modération côté back-office (file de signalements) n'a pas son écran de traitement |

Le périmètre ajouté ce jour-là au-delà du tableau : la garde MFA de l'admin est **réellement
montée** dans la chaîne de middlewares (elle était écrite mais absente de `MIDDLEWARE`), le plafond de
frein s'applique désormais même si une vue oublie son `throttle_scope`, et l'expéditeur e-mail du
service est `itsupport@kamcofarm.com` avec `Auto-Submitted`/`Precedence` sur les deux messages
transactionnels.

## 12. Ce dont j'ai besoin de votre côté (blocages réels)

1. **Domaine pour les liens courts** (ex. `qr.kamco.farm`) : c'est **imprimé**, donc irréversible —
   prévoir le domaine dédié, la zone DNS, et le TLS. Alternative : un domaine secondaire jamais
   utilisé pour le marketing.
2. **Écran de consentement Google** : projet Google Cloud + identifiant/secret OAuth (scopes
   `openid email profile`), domaine autorisé, et l'URL de callback que je vous donne.
3. **Hébergeur et région** (Hetzner/OVH/Scaleway/Seeweb — vous êtes en Italie : `mil-1`/`de`),
   budget mensuel acceptable, qui paie.
4. **E-mail** : nom d'expédition + SPF/DKIM/DMARC (sinon les e-mails de vérification partent en spam).
5. **Plan facturable ou gratuit** : les quotas §7 dépendent de cette réponse.
6. **Marque/couleurs** pour le tableau de bord et la page de destination (410/503 de marque).

---

## 14. Paiement mobile branché, et l'espace admin qui crée gratuitement (mesuré le 2026-09-15)

**Mobile : Flutterwave remplace le 503.** `apps/billing/mobile.py` définit le contrat d'un agrégateur
(`entetes_signature`, `reverification_obligatoire`, `normaliser_rappel`, `constater`, `montant_attendu`,
`demander_paiement`) et deux implémentations : `demo` (fil de simulation, dev seulement) et `flutterwave`
(`POST /v3/charges?type=…`, `GET /v3/transactions/verify_by_reference`). Trois décisions valent le détour
parce qu'elles touchent à l'argent :

- la référence qui fait le lien est **la nôtre** (`tx_ref = QRM-…`) ; `flw_ref` n'est que du `detail` ;
- le montant attendu est **gravé à la demande** (`detail["montant_facture"]` + `devise_facturee`) et le rappel
  est jugé là-dessus, pas sur le taux du jour — sinon une variation de change entre la demande et la
  confirmation transforme un paiement légitime en trop-perçu, et un client qui a payé ne reçoit rien ;
- un rappel `successful` **n'accorde rien** sans re-interrogation confirmée. C'est la recommandation explicite
  de Flutterwave, traduite en `reverification_obligatoire = True` + `constater()` ; si la re-interrogation est
  impossible, la vue répond `{"recu": true, "attente_reverification": true}` et le paiement reste en attente.

Les rails MoMo facturent en monnaie locale alors que la grille est en euros : la conversion existe à un seul
endroit (`montant_attendu`), avec `MOBILE_MONEY_FLUTTERWAVE_TAUX` obligatoire dès que la devise diffère,
sinon refus 503. Le front affiche `montant_affiche` (ce que le client valide sur son téléphone) et garde la
valeur de la grille à côté, en centimes.

**L'espace admin (`/manage/`) : analytique + création gratuite.** Quatre ajouts, un seul chemin de création :

- `/manage/analytique/` : série 7/30/90 jours multi-QR (scans, visiteurs, mobile/desktop), mélange
  statique/dynamique, **part du volume portée par l'espace admin lui-même** (la ligne qui doit rester plate),
  répartition par pays, top QR, volume des créations — et un drill-down `/manage/analytique/qr/<id>/`
  (série 90 jours, pays, versions, aperçu SVG regénéré à la volée). Les graphiques sont de l'SVG écrit côté
  serveur (`apps/common/charts.py`) : pas de bundler, pas de CDN, pas de JavaScript dans le back-office.
- **`analytics_platformdailystats`**, une ligne par jour : à 1 M d'utilisateurs, agréger
  `analytics_qrdailystats` (une ligne par QR, par jour, par pays) à chaque rafraîchissement de page d'admin
  serait un incident. La table est tenue par `aggregates.aggreger_plateforme()`, appelé en fin de
  `rebuild_for_day`, donc par la tâche de nuit, par le bouton de recalcul (borné à 30 jours, parce qu'une vue
  HTTP n'a rien à faire de tenir deux minutes de SQL agrégé) et par `rebuild_daily_stats`.
- `/manage/qr/creer/` : **le même `QrSerializer` et le même `apps.qr.services.create_qr()`** que l'API — mêmes
  validations (taille de payload, types connus, sûreté de la destination), mêmes versions, même invalidation
  de cache. `origine = "admin"` est écrit *après* le service, jamais accepté du serializer.
- La règle d'exemption vit dans **un seul fichier** (`apps/accounts/exemption.py`) : superutilisateur, ou
  permission nommée `qr.creer_sans_facturation`. Elle est lue par `qr/quota.py` (portes 402), par
  `User.a_droit_a` (export PDF, analytics — sinon l'admin peut créer mais pas mesurer) et publiée par
  `/auth/me`. `is_staff` seul ne donne rien, volontairement : « fait partie du personnel » ne doit pas vouloir
  dire « ne paie pas ».

Ce que la demande « les super admins créent des QR dynamiques gratuitement » implique et qui a été vérifié, pas
supposé : un compte **free** exonéré crée un QR **dynamique** (normalement réservé Premium) sans qu'aucun
`Abonnement` soit créé ni que `plan_effectif` bouge — `tests/test_backoffice.py` met en regard le même envoi
côté client non habilité, qui reçoit 402 `plan_required`. Le lien `/manage/qr/creer/` affiché sur `/facturation`
quand le compte est exonéré est la traduction visible de la même règle.


## 15. QR artistiques, GIF animés, codes promo (mesuré le 2026-09-16)

### Ce qui a été ajouté au front

| Fichier | Rôle |
| --- | --- |
| `src/lib/rendus.js` | Construction des URL de rendu (`urlRendu`, `urlApercu`), bornes (frames 8–48, duree 40–400 ms, liseré 0–64 px, taille 240–1024 px), lecture des **en-têtes de preuve** (`lirePreuve`) et leur mise en phrase (`decrirePreuve`). Aucune dépendance au DOM : testable sous `node --test`. |
| `src/components/RendusServeur.jsx` | Le bloc sous l'aperçu du studio : sélecteur de style (lu depuis `/api/v1/qr/styles/`), animation du liseré, curseurs frames/liseré/taille, deux téléchargements. |
| `src/components/CodesPromo.jsx` | La table des codes d'une campagne sur `/facturation` : création unitaire, lot de 1 à 500, prolongation +7 j, retrait, copie du lien `?promo=`. |
| `src/lib/api.js` | `chargerRendu(url)` (binaire + mêmes erreurs typées que le JSON), `api.styles()`, le groupe `codesPromo`. |

### Le choix qui structure tout : l'aperçu est le fichier

L'aperçu affiché dans le panneau n'est pas un dessin canvas local, c'est l'URL de rendu du serveur
(`<img src="/api/v1/qr/apercu/?…">`). Une divergence entre « ce que je vois » et « ce que j'imprime » se
paie en flyers, pas en tickets : le canvas local reste la réactivité immédiate du studio (hors ligne,
aucun octet envoyé), le serveur apporte la relecture qui prouve que le QR se décode.

Conséquences assumées : l'`<img>` n'est monté que si le service a répondu (la table des styles vient de
l'API) — une image dont la requête échoue imprime une erreur de console, et nos tests de navigation
tiennent une console bruitée pour un échec, à juste titre ; l'`onError` retire le bloc plutôt que de le
faire réessayer en boucle.

### Ce que le front ne décide pas

La capacité (`qr_artistique_ia`, `qr_anime`, `codes_promo`), les bornes d'un GIF et la validité d'un code
promo s'évaluent côté serveur ; le 402 arrive avec `palier_requis` et le prix, et ouvre le panneau d'abonnement existant
(`useFacturation`) — le front ne duplique ni prix ni règle de validité. Le libellé de chaque capacité est
contrôlé par un test de parité côté API (`tests/test_parity_front.py`) : une capacité de la grille sans
entrée dans `PALIERS_LABELS` rougit le build, alors qu'à l'écran elle n'aurait montré que `qr_artistique_ia`.

### Le seul point d'injection HTML du front est validé, pas seulement échappé

`src/components/Preview.jsx` fait `dangerouslySetInnerHTML={{ __html: result.preview.svg }}`. C'est le seul
endroit du front où du HTML construit à la main atterrit dans le DOM, et le contrat du générateur a été serré
en même temps que ce lot :

- la légende passe par `escapeXml` (`& < > "`), c'est ce que le test historique `&lt;` vérifiait déjà ;
- les couleurs d'arrière-plan, de module et de repère passent par `couleurValide` (`#rgb` → `#rrggbbaa`) :
  une valeur hors format retombe sur le défaut du composant et n'est **pas** interpolée ;
- l'`href` du logo passe par `urlImageValide` : `data:image/…` ou `http(s)://…` uniquement, tout caractère
  pouvant fermer un attribut (`" ' < > ` \` et espace) fait refuser l'URL, et le refus est annoncé dans
  `warnings` plutôt que tu.

Rien d'extérieur n'atteignait ces valeurs avant ce durcissement (les couleurs viennent des
`<input type="color">`, le logo d'un `FileReader.readAsDataURL`, et `useStudio` ne lit aucun paramètre d'URL —
vérifié) ; le composant ne doit pas tenir uniquement parce que l'appelant est sage. La goupille est
`scripts/svg-echappement.test.mjs` (4 tests, dans `npm run test:lib`) : une saisie hostile ne doit produire ni
`<script>`, ni `onload`/`onerror`, ni `<img>` dans le SVG rendu.

**Et autour, la politique de contenu.** Depuis ce tour, les pages rendues par le serveur portent une CSP
appliquée avec un nonce par requête (`api/apps/common/csp.py`, documentée dans `../api/README.md` § « Politique
de contenu (CSP) ») : `script-src 'self' 'nonce-…'`, sans `'unsafe-inline'`. **Ce SPA n'est pas couvert par
cette politique** — il est servi en statique, et Caddy ne publie sur ce périmètre qu'une ligne
`Content-Security-Policy-Report-Only`. Pour l'activer côté front, deux choses sont indispensables, et elles ne
sont pas décoratives :

- sortir le `<script>` de résolution du thème de `index.html` dans un module chargé normalement (c'est le seul
  script en ligne du document ; le prix est visible : une frame avec le mauvais thème au chargement) ;
- garder `img-src 'self' data: blob:` — le studio construit ses aperçus en `data:` (logo lu par `FileReader`)
  et ses téléchargements en `blob:` (`URL.createObjectURL`) — plus `style-src-attr 'unsafe-inline'`, et
  `connect-src 'self'` tant que l'API reste derrière le proxy du même domaine.

Tant que ces deux points ne sont pas faits, ne pas « activer la CSP du front pour cocher la case » : une
politique qui casse l'export PNG est un régressus plus grave que le trou qu'elle ferme.

### Ce que la machine de test n'a pas pu prouver ici

`scripts/browser.test.mjs` et `scripts/facturation.api.test.mjs` (21 tests) exigent une Chrome lançable :
sur cette instance, `libnss3.so` est absent et non installable (pas de root) : les trois suites qui lancent
Chromium (`browser.test.mjs`, `facturation.test.mjs`, `facturation.api.test.mjs`) échouent au lancement du
processus, pas sur une assertion. Passaient avant ce lot avec le même moteur ; ici, `npm run build`
(241 ko de JS, 81 ko gzip) et `node --test scripts/lib.test.mjs scripts/rendus.test.mjs
scripts/svg-echappement.test.mjs` (43/43, mesuré le 2026-09-16) sont verts, et la suite pytest de l'API
exerce les mêmes URL de rendu bout en bout. À relancer sur un hôte complet avant de publier.
