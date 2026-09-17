# QR Studio — API

Backend du service de QR dynamiques : comptes, redirection mesurée, statistiques par pays,
back-office. Le front (Vite/React) vit dans [`../qr-coding-react`](../qr-coding-react) ; la
conception chiffrée est dans
[`../qr-coding-react/ARCHITECTURE.md`](../qr-coding-react/ARCHITECTURE.md) — ce fichier est
l'index d'exécution, pas la spécification.

En trois lignes :

- **Le chemin chaud ne touche jamais la base.** `GET /r/{slug}` lit le cache Redis, pose un 302, et
  pousse l'événement de scan dans un stream. La base n'intervient qu'au moment de l'agrégation.
- **Les statistiques se lisent dans les agrégats** (`analytics_qrdailystats`), jamais dans les
  lignes brutes — qui sont partitionnées par mois et purgées selon la rétention RGPD.
- **L'admin Django est un outil d'exploitation à part entière**, protégé par URL non triviale et
  second facteur ; le back-office métier (`/manage/`) ne fait que reposer les mêmes garde-fous.

## Démarrer sans Docker

SQLite + cache mémoire : aucun service externe n'est nécessaire pour développer.

```bash
python3 -m pip install --target vendor -r requirements-dev.txt   # ou: make install
cp .env.example .env                                              # dev: rien à changer de plus
PYTHONPATH=vendor DJANGO_SETTINGS_MODULE=config.settings.dev python3 manage.py migrate
PYTHONPATH=vendor DJANGO_SETTINGS_MODULE=config.settings.dev python3 manage.py createsuperuser
PYTHONPATH=vendor DJANGO_SETTINGS_MODULE=config.settings.dev python3 manage.py runserver 0.0.0.0:8000
```

Avec le `Makefile` : `make migrate`, `make run`, `make test`, `make lint`, `make bench`.

URLs utiles en dev : `/` (le front si vous le servez à côté), `/api/v1/`, `/api/v1/docs/`
(Swagger), `/healthz/`, `/{DJANGO_ADMIN_PATH}` (`manage-9f2/` par défaut en dev).

## Grille tarifaire en vigueur

Source unique : [`apps/accounts/plans.py`](apps/accounts/plans.py). Les prix y vivent — pas dans une
variable d'environnement, pas dans le front — parce qu'un prix est une promesse faite au client.

| Palier | Prix | Ouvre | QR statiques | QR dynamiques / 30 j | Historique stats |
| --- | --- | --- | --- | --- | --- |
| Gratuit | 0 € | `qr_statique`, `export_image` (PNG/SVG) | 20 | — | — |
| Standard | 2,99 €/mois | + `score_scannabilite`, `cadres_cta`, `export_pdf`, `themes_sectoriels` | 500 | — | — |
| Premium | 8,99 €/mois | + `qr_dynamique`, `analytics` | 5 000 | 100 | 90 jours |
| Entreprise | 15,99 €/mois | + `carte_visite_connectee`, `qr_multi_liens`, `api_developpeurs` | illimité | 2 000 | 365 jours |

Chaque palier hérite du précédent (`caracteristiques()` fait l'union, une caractéristique n'est écrite
qu'au palier où elle apparaît). Conséquences côté API, toutes testées dans `tests/test_plans.py` :

- un QR dynamique demandé en Gratuit ou Standard → **402 `plan_required`** avec `details.palier_requis`
  (et non 403 : la porte s'ouvre avec un paiement, le front affiche un prix, pas un refus) ;
- les statistiques → 402 `plan_required` sous Premium ; la profondeur demandée est bornée par le
  palier (`?days=365` en Premium rend `window_days: 90`) ;
- `?fmt=pdf` sur `/image/` → 402 sous Standard ; PNG et SVG restent gratuits ;
- le plafond de volume → 402 `quota_exceeded` : ce n'est **pas** le même message, un client qui a déjà
  payé ne doit pas lire « achetez Premium » ;
- `plan_until` dépassé ⇒ `plan_effectif` retombe en Gratuit dans la seconde, sans attendre une tâche.

Les seuils se règlent sans déployer (`QUOTA_PREMIUM_DYNAMIQUES_30J=500`, `-1` = illimité) ; les paliers,
non. `/api/v1/auth/config` (public) sert la grille entière et l'état des fournisseurs, pour que le front
n'ait jamais un prix en dur.

**Connexion Google : mise de côté sur votre demande.** Le flux reste écrit (vue, allauth, PKCE) mais
inoffensif : sans `GOOGLE_CLIENT_ID`+`SECRET` ni `SocialApp` créée depuis l'admin, `/auth/google/url`
répond 503 `google_not_configured` et `/config` annonce `fournisseurs.google.actif: false` — donc aucun
bouton n'est rendu. `GOOGLE_LOGIN_ENABLED=0` ferme l'allée sans effacer la configuration
(`provider_disabled`). Inscription et connexion par mot de passe : seules voies actives.

## Routes ajoutées depuis le sprint 2

| Route | Méthode | À quoi elle sert |
| --- | --- | --- |
| `/api/v1/qr/{id}/image/?fmt=png\|svg&size=64..2048` | GET | Le fichier à imprimer. `Cache-Control: public, max-age=86400, immutable`, clé de cache dérivée du **contenu encodé** : l'image d'un QR dynamique ne bouge pas quand la destination change (c'est le principe du flyer re-imprimable), et le seul endpoint qui fabrique du CPU par requête est plafonné à part (`THROTTLE_QR_IMAGE`, 60/min). |
| `/api/v1/qr/{id}/rendu/?fmt=art\|gif` (et `POST` en `multipart` pour un logo) | GET, POST | Le rendu **vérifié** : style artistique ou GIF à liseré animé. La réponse porte la preuve dans `X-Lisibilite`, `X-Score` et `X-Montage` ; 402 si le palier ne la donne pas ; 413 si le GIF dépasse 4 Mo ; 503 si l'hôte ne peut pas relire. Throttle propre (`THROTTLE_QR_ART`, 30/min) : c'est du dessin **et** de la vision par ordinateur par absence de cache. |
| `/api/v1/qr/apercu/?fmt=art\|gif&payload=…` | GET | Le même rendu pour un contenu **non enregistré** — l'aperçu du studio, avant de créer un QR. Même porte de palier, mêmes bornes, TTL de cache 30 min. Une `<img src>` légale. |
| `/api/v1/qr/styles/` | GET | La table des styles avec le contraste calculé et `logo_surface_max` : le front affiche, il ne duplique pas. |
| `/api/v1/promo/codes/` (+ `generer/`, `verifier/`, `{id}/prolonger/`) | GET, POST, PATCH, DELETE | Les codes promo d'une campagne, palier Entreprise : génération à la volée, expiration obligatoire, lot de 1 à 500, verdict lu par la même fonction que le scan. |
| `/api/v1/account/export` | GET | Export de portabilité RGPD, JSON versionné (`qrs-export/1`), QR archivés et supprimés logiquement inclus. Au-delà de 5 000 lignes : 413 et l'export asynchrone CSV. |
| `/api/v1/account/erase` (alias de `/api/v1/account/`) | DELETE | Droit à l'effacement. Re-authentification par mot de passe, ou mot d'ordre `SUPPRIMER` pour un compte sans mot de passe (lié via Google). Session détruite, puis **0 ligne restante** dans les 7 tables du compte — vérifié table par table. L'alias sans slash final existe parce qu'un 301 sur une route destructive perd le corps de la requête. |

## E-mails

L'expéditeur réel du service est **`QR Studio <itsupport@kamcofarm.com>`** (`DEFAULT_FROM_EMAIL`,
`SERVER_EMAIL` en héritent). Pas de `Reply-To` posé : il serait identique au `From`, et le doubler est
un motif de rejet côté anti-spam. Les deux messages transactionnels portent `Auto-Submitted:
auto-generated` et `Precedence: bulk` — sans eux, chaque vacancier du monde répondrait à nos codes de
vérification.

Côté DNS/messagerie, la condition qui reste vraie même avec le bon réglage : `kamcofarm.com` doit
autoriser l'hôte d'envoi (SPF), signer (DKIM) et aligner le DMARC, sinon le premier réflexe d'un
client sera de chercher le code dans ses indésirables.

## QR artistiques, QR animés, codes promo

Trois postes, un seul impératif commun : **rien n'est servi qui n'ait été relu ici**. Un QR « stylisé »
qui ne décode pas n'est pas un QR, c'est une affiche — et l'incident se découvre au comptoir, sur un
flyer déjà imprimé.

### Ce que le serveur garantit

| Format | Contrats |
| --- | --- |
| PNG artistique (`apps/qr/art.py`) | 5 styles (`naples`, `halles`, `nuit`, `vitrine`, `encre`), contraste ≥ 4,5:1 calculé depuis les palettes, module ≥ 6 px, zone de silence de 2 modules, ECC **H** dès qu'un logo est posé, logo borné à **10 %** de la surface. |
| GIF animé (`apps/qr/animation.py`) | 8 à 48 frames, 40 à 400 ms par frame, liseré de 0 à 64 px, **≥ 60 % de frames immobiles**, ≤ 4 Mo, et vérification portée sur les **octets encodés** (la première et la dernière frame), pas sur l'image avant quantification. |
| Codes promo (`apps/qr/promo.py`) | Alphabet sans `0/O/1/I`, préfixe lisible dérivé de l'intitulé, `expire_le` obligatoire, plafond d'usages optionnel, unicité **par compte**. |

Échecs possibles, et ce qu'ils veulent dire : `style_inconnu` (400), `contraste_insuffisant` (400),
`payload_trop_long` (400), `parametre_invalide` (400 — les bornes ne sont pas corrigées à la volée),
`art_ilisible` et `animation_ilisible` (409), `logo_trop_lourd` (413), `animation_trop_lourde` (413),
`plan_required` (402, avec `palier_requis` calculé depuis la grille), `art_verifieur_absent` (**503**).

### Le vérifieur est zxing, OpenCV n'est que le repli

Mesure, pas opinion : sur nos propres images, `QRCodeDetector` d'OpenCV 4.11 refuse un QR parfaitement
valable dès qu'un liseré clair entoure l'image (6 à 48 px, toutes les variantes testées — recadrage,
seuil dur, ×2, ×3, +4 blanc, +4 noir) **et** refuse le PNG de référence de la librairie `qrcode` à
410 px. `zxing-cpp` lit les deux. Un vérificateur plus strict que le producteur ne rend pas le produit
plus sûr : il le rend indisponible. D'où `_detecteurs()` qui place zxing en premier, OpenCV en secours,
chacun essayé sur l'image telle quelle puis avec 4 px de clair ajoutés — l'épreuve utilisée est nommée
dans le score (`epreuve: "zxing-direct"` ou `"opencv-marge"`), pour que « vérifié » ne veuille jamais
dire « vérifié en trichant ». `pyzbar` est une impasse sur l'hôte de développement : `libzbar` absent,
aucune installation système possible.

Dépendances : `qrcode[pil]`, `numpy`, `zxing-cpp>=2.2`, `opencv-python-headless>=4.10`. Sans les deux
dernières, l'API ne tombe pas en panne silencieuse : elle répond 503 sur les formats qui exigent une
preuve.

### Le code promo se vérifie au scan, pas dans le navigateur

Le flyer n'imprime qu'un lien court. Trois cas, un seul code :

* le visiteur **présente** un code (`?promo=…`, et c'est bien ce que produit l'API : le lien court +
  `promo=`) : c'est *ce* code qui est jugé, et aucun autre — un code inconnu de ce compte reste un scan
  normal, jamais un 404 qui apprendrait aux scaliseurs quels codes existent ;
* le QR porte une offre unique : elle s'applique à tout scan ;
* l'offre est morte (date, quota, inactivité) : **une page 200 « offre terminée »**, pas une
  redirection. Rediriger vers la page normale ferait croire que la remise s'applique, et le litige
  éclaterait au comptoir. Le scan est compté avec `status=410` dans l'événement d'analytique.

La validité est lue depuis l'**entrée de cache** de la redirection (`promo` + `codes_promo`), jugée sur
un epoch (`expire_ts`) et non sur une chaîne ISO. Le chemin chaud reste à **0 requête SQL** par scan
vérifié : `test_le_chemin_chaud_ne_touche_toujours_pas_la_base_avec_une_offre` l'impose. Le compteur
d'usages vit dans le cache (`qrs:promo:usages:{id}`, TTL 25 h) ; `usages` en base est la baseline
resserrée par `manage.py sync_promo_usages`.

### Le compteur doit être resserré, sinon il ment dans les deux sens

Si Redis n'est que du `locmem` (une instance) ou subit une éviction (l'autre), un compteur qui n'existe
que là-dedans peut repartir de zéro — l'offre n'est jamais close et le client paie deux fois la même
remise — ou rester bloqué — l'offre meurt trop tôt et le support prend l'appel. La commande applique
`usages = max(db, db + redis)` puis vide la clé, en `F()` pour que deux passes qui se chevauchent ne
perdent pas de comptages.

```bash
17 * * * * cd /opt/qrstudio/api && /opt/qrstudio/venv/bin/python manage.py sync_promo_usages
```

### Paliers, et la baisse du Gratuit qui ne rétroagit pas

`qr_artistique_ia` et `qr_anime` : Premium et Entreprise. `codes_promo` et `boutique_templates` :
Entreprise seul, avec `codes_promo_max` à 50 codes actifs (0 ailleurs — c'est la même fonction
`plans.limite()` qui le dit, donc la porte et le quota ne peuvent pas diverger). Gratuit : **1** QR
statique, contre 20 avant ce lot.

Les licences déjà émises gardent leurs 20 : la migration `accounts/0003_gele_quota_statique` écrit
`quota_statique_gele = 20` et `quota_statique_palier = "free"` pour tout compte créé avant la bascule et
ne payant rien à ce moment-là (`plan_until` nul ou passé — la règle exacte de `User.plan_effectif`, en
deux `UPDATE` et pas une boucle sur un million de lignes). Le gel **taggé palier** est ce qui rend le
reste simple : il ne vaut que tant que le compte est au palier où il a été posé, un compte qui paie solde
sa dette (remise à zéro dans `billing.services.accord`), un compte qui annule repart de la grille en
vigueur — donc à 1. La non-rétroactivité est un prêt, pas un plancher à vie.

### L'admin crée tout, gratuitement

Depuis `/manage/qr/creer/` (back-office) ou `django-admin shell`, un superuser ou un titulaire de
`qr.creer_sans_facturation` crée des QR **statiques et dynamiques avec toutes les options Entreprise**,
sans facture : le formulaire porte le style, l'animation (frames, liseré) et la génération d'un code promo
daté. `origine = "admin"` est écrit après le service (le client ne peut pas le choisir), la permission
d'exemption est la seule règle (jamais `is_staff`), et la grille tarifaire reste affichée pour référence.

## Qualité — ce qui est vérifié

Ce qui suit a été exécuté sur ce dépôt, pas seulement écrit :

| Commande | Résultat |
| --- | --- |
| `make test` (`pytest`) | **337 passés**, 0 échec, 1 avertissement (surcharge de `DATABASES` dans un test) — dont `tests/test_flutterwave.py` (10, adaptateur de paiement mobile), `tests/test_backoffice.py` (20, espace admin : accès, exemption, création gratuite, pages rendues), `tests/test_qr_art.py` (scannabilité prouvée par relecture), `tests/test_csp.py` (27, politique de contenu), `tests/test_promo.py`, `tests/test_security.py` (8), `tests/test_throttling.py` (4), `tests/test_validators.py` (33), `tests/test_rgpd.py` (8) |
| `ruff check apps config tests` | All checks passed |
| `ruff format --check` | 124 fichiers déjà formatés |
| `mypy apps` | Success: no issues found in 46 source files (les modules critiques, dont `qr/art.py`, `qr/animation.py`, `qr/promo.py`, `qr/rendus.py`, `qr/promo_views.py`, `sync_promo_usages.py`) |
| `bandit -r apps -x tests` | **0 issue à toutes les sévérités** (low/medium/high,Undefined confondues) — la cible `make security` se limite à `-ll`, le complément est à lancer en CI |
| `manage.py check --deploy` (réglages **prod**, env posés) | **aucun problème** (1 avertissement mis en liste noire, justifié dans `prod.py`) |
| `makemigrations --check` | No changes detected |
| `manage.py migrate` (dev) | les 3 applications du projet migrent, y compris le DDL de partition |

Côté front, `npm test` (build + les fichiers de `scripts/*.test.mjs`) est décrit dans
`../qr-coding-react/ARCHITECTURE.md` : **43/43** sur les suites sans navigateur (`lib`, `rendus`,
`svg-echappement`, mesuré le 2026-09-16) ; les trois suites qui pilotent Chromium (`browser`, `facturation`,
`facturation.api`) échouent ici au lancement du processus — `libnss3.so` absent, pas de root — et passaient
sur un hôte complet. Le build passe (`241 ko` de JS, `81 ko` gzip).
Le banc de charge (`make bench`) est décrit à la section suivante : il mesure **0,6 à 0,8 ms** de
travail applicatif par scan en hit de cache, mais ne peut pas valider le critère `p99 < 15 ms` sur
cette machine — voir « Ce qui n'est pas vérifiable ici ».

### Ce qui ne doit jamais être committé

`api/.gitignore` exclut `db.sqlite3`, `.env*`, `vendor/` et les caches. Ce n'est pas un détail : `db.sqlite3`
porte les comptes joués de l'aperçu (mots de passe `Smoke2026!…` posés par `scripts/seed_apercu.py`) et, sur
un poste de développement, de vrais mots de passe saisis dans le formulaire d'inscription. Un `.env` avec
`SECRET_KEY` ou une clé Stripe tombé là-dedans se retrouve dans l'histoire git à la première
`git add -A`. Avant de partager la machine ou de pousser un commit, purger la base locale et faire tourner
ces identifiants de démonstration.

### Ce que la revue de sécurité a vérifié (mesuré le 2026-09-16)

| Contrôle | État mesuré |
| --- | --- |
| `bandit -r apps -x tests`, **toutes sévérités** | 0 issue (low, medium, high confondues) |
| `manage.py check --deploy --fail-level WARNING` (réglages prod) | aucun problème, 1 avertissement mis en liste noire et justifié dans `prod.py` |
| Greps à blanc sur les pièges connus (`apps/`, `config/`) | aucun `\|safe`, aucune `{% autoescape off %}`, aucun `eval`/`exec`, aucun `shell=True`, aucun `md5`/`sha1` comme digest de sécurité ; le seul `pickle` du dépôt est **un commentaire** de `apps/qr/cache.py` qui explique pourquoi le backend de cache est court-circuité (Redis sérialise en pickle et préfixe les clés) |
| Mots de passe et jetons | `secrets.token_*` pour les jetons d'inscription et les hash de cache ; hachage **Argon2 seul** (`PASSWORD_HASHERS`) |
| Webhooks de paiement | Stripe : en-tête au format réel `t=<epoch>,v1=<hex>[,v1=<hex>]`, tolérance d'horodatage (`horodatage_hors_tolerance`), comparaison en temps constant, vérifiée **avant** tout parsing du corps. Flutterwave : les deux formes documentées (`flutterwave-signature` = HMAC-SHA256 base64 du corps brut, et l'ancien `Verif-Hash`) sont acceptées, et le rappel seul ne suffit jamais — `reverification_obligatoire` impose de relire `verify_by_reference` (statut, montant, devise, `tx_ref`). L'idempotence du rejeu est garantie par la contrainte d'unicité sur `EvenementPaiement.identifiant` |
| Garde SSRF sur la **destination** des QR (`apps/qr/validators.py`) | schéma http(s) imposé, identifiants et ancre `#` refusés, `localhost`/`.local`/`.internal` refusés, et l'adresse est jugée **quand c'est une IP littérale comme après résolution DNS** — sinon `http://169.254.169.254/` passerait dès que le contrôle DNS est désactivé |
| Anti-énumération | 402 (et non 403) sur une porte de palier, 404 (et non 403) sur l'objet d'autrui, et un `?promo=` inconnu au scan répond un scan **normal** — pas un 404 qui révèlerait quelles offres existent |
| Anti-brute-force | rappels par e-mail : code à 6 chiffres, 15 minutes, **5 tentatives puis verrouillage du canal 30 min**, comparaison en temps constant (`apps/accounts/models.py`) ; mot de passe : `LOGIN_RATE` 10/min + `REGISTER_RATE` 5/h |
| Freins mesurés (`QR_FRAMEWORK["throttles"]`, lignes 245-257 de `base.py`) | login 10/min, register 5/h, password_reset 5/h, verify 10/min, écritures QR 120/min, stats 60/min, images 60/min, **rendus d'art et de GIF 30/min**  ; MFA du personnel **fail-closed** par `MfaGateMiddleware` (toute requête d'un membre du personnel sans TOTP est renvoyée vers l'activation du second facteur ; si les URLs `allauth.mfa` manquent au déploiement, la réponse est un 403 — jamais un passe-droit) |
| Téléversement de logo | PNG/JPEG/WebP seulement, 256 ko au plus, **surface refusée au-delà de 16 mégapixels avant décodage** (et le refus de Pillow lui-même est traduit en `logo_trop_grand`), contenu reconverti puis stocké sous un nom de 16 hexagones |
| En-têtes de production | HSTS 365 jours + sous-domaines, `X-Frame-Options: DENY`, `nosniff`, COOP `same-origin`, cookies de session `HttpOnly`/`Secure`/`Lax`, `Referrer-Policy: strict-origin-when-cross-origin` — posés par Django **et** par Caddy (`docker/conf/Caddyfile`) |

### Politique de contenu (CSP) : un nonce par requête, et un mode qui ne bloque rien par défaut

`apps/common/csp.py` (politique, nonce, point de chute des rapports) + `apps/common/middleware.py::CspMiddleware`
+ `apps/common/templatetags/csp.py` (`{% load csp %}`, `<style {% csp_nonce %}>`). Elle couvre **tout le HTML
rendu par Django** : `/manage/`, `/manage-9f2/`, `/accounts/` (allauth), et les pages publiques `/r/{slug}`.

La politique, dans l'ordre où elle est lue par le navigateur :

```
default-src 'self'; script-src 'self' 'nonce-<par requête>'; style-src 'self' 'nonce-<par requête>';
style-src-attr 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self';
form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'
[upgrade-insecure-requests] [report-to "qrcsp"; report-uri "/csp-violation/"]
```

Quatre choix se tiennent par une mesure, pas par une habitude :

- **`script-src` strict, sans `'unsafe-inline'`.** Vérifié sur les 9 gabarits du projet, les 50 gabarits
  installés de `django.contrib.admin` et ceux de `django-allauth` : aucun script en ligne exécutable nulle part.
  Allauth pose bien des `<script>` en ligne, mais en `type="application/json"` — des îlots de données inertes
  lus par `onload.js`, que `script-src` ne regarde pas. `test_aucun_script_executable_en_ligne_sur_les_pages_
  fournisseurs` rougit si une montée de version introduit le contraire, au lieu que l'admin ferme ses portes en
  production ; la réponse est alors `CSP_SCRIPT_SRC_EXTRA` (un hash), jamais un `unsafe-inline` par surprise.
- **`style-src` noncé, `style-src-attr` libre.** Nos trois blocs `<style>` (`manage/_style.html`,
  `qr/promo.html`, `qr/unavailable.html`) portent le nonce ; les attributs `style="margin:0"` et les graphiques
  SVG construits serveur restent permis parce qu'une feuille de style ne fait pas tourner de code. Le seul bloc
  `<style>` de Django (`admin/change_list.html`, une règle décorative sur `#changelist table thead th`) n'est
  même pas émis avec les réglages par défaut : mesuré `blocs <style> = aucun` sur `/manage-9f2/qr/qrcode/`.
- **Les redirections sont exclues, et c'est un chiffre.** `HttpResponseRedirect` sort avec un
  `Content-Type: text/html` hérité de Django et un corps vide : publier une politique sur `/r/{slug}` coûterait
  un `secrets.token_urlsafe` et ~400 octets d'en-tête par scan, pour un document que le navigateur ne rend pas.
  Mesuré sur ce dépôt : `Nonce()` engendré et lu coûte **1,40 µs**, le tri `est_html()` sur une 302 **0,35 µs** ;
  le nonce est paresseux, donc un scan qui ne rend pas de HTML ne tire aucun aléa.
- **Un mode, trois états, et un démarrage qui refuse le quatrième.** `CSP_MODE=off|report-only|apply` — vide =
  `off` en debug (la page de traceback de Django a ses propres scripts en ligne, une politique appliquée
  l'aveuglerait au moment précis où on en a besoin) et `report-only` sinon. Une valeur mal orthographiée lève
  `ConfigurationCsp` à la construction du middleware : une politique absente qui ressemble à une politique
  respectée est le pire des deux mondes.

Les rapports de violation tombent sur `POST /csp-violation/` (vue maison, `csrf_exempt` parce que c'est le
navigateur qui les poste, 204 et rien d'autre en réponse pour ne pas renseigner un serveur curieux). Elle
lit les deux formes (`report-uri` : `{"csp-report": …}` ; `report-to` : enveloppe de l'API Reporting avec
`blocked-url`), tronque le corps à 8 ko, et **journalise une fois par minute et par adresse** — sinon un site
vérolé remplirait `journalctl` de rapports.

Ce que tout cela a donné, mesuré sur `runserver` avec `CSP_MODE=apply` :

| Vérification | Résultat |
| --- | --- |
| `GET /manage-9f2/login/` | en-tête `Content-Security-Policy` publié, `script-src 'self' 'nonce-o43Vu…'`, `Reporting-Endpoints: qrcsp="…/csp-violation/"` |
| `GET /r/CKHUyMzi?promo=PROMO-CAVE-H9RF` (offre close, page publique) | 200, politique publiée, et le `<style nonce="A0VrJ7TPziX9JWSn-9XEWQ">` du corps porte exactement le nonce de l'en-tête |
| `GET /r/CKHUyMzi` (chemin chaud, 302) | **0** occurrence de `Content-Security-Policy` — l'exemption tient |
| `POST /csp-violation/` avec un rapport | `204`, et une ligne `WARNING apps.common.csp CSP: script-src 'self' bloque inline (document …)` dans le journal |
| `pytest tests/test_csp.py` | **27 passés** (forme de la politique, nonce, mode, pages réelles, rapports, garde d'amont) |

**Activer pour de vrai** : `CSP_MODE=apply` dans l'environnement du service `app`, puis une semaine de
`journalctl -u qrstudio-app -f | grep "CSP:"` avant d'y toucher. Les statiques passent par WhiteNoise
(même origine) donc `'self'` les couvre sans configuration.

### Ce que la revue de sécurité laisse ouvert (à décider, pas de la décoration)

1. **La CSP est appliquée côté Django, pas encore côté front.** Les pages rendues par le serveur portent un
   nonce et une politique stricte (section ci-dessus) ; la ligne du Caddyfile reste en `Report-Only` parce
   qu'elle couvre aussi le SPA servi en statique, dont `index.html` contient un `<script>` en ligne (résolution
   du thème avant le premier rendu React). Le passage en `Content-Security-Policy` simple demande de sortir ce
   script dans un module — et de garder `img-src … data: blob:` plus `style-src-attr 'unsafe-inline'` dans la
   politique du front : le studio construit des aperçus en `data:` et des téléchargements en `blob:`. C'est un
   choix de produit (un flash de thème au chargement), pas une ligne à effacer.
2. **Pas de COEP ni de `Cross-Origin-Embedder-Policy`** : le cloisonnement par origine est à moitié fait
   (COOP `same-origin` est posé, il n'isole les groupes de documents que si COEP suit). Ajouter
   `require-corp` exige de servir chaque ressource avec `CORP`/`CORS` — à valider en recette, pas à l'aveugle.
3. **Le compteur d'usages d'un code promo vit dans Redis.** La colonne `usages` de la base n'est resserrée
   que par `manage.py sync_promo_usages` (cron `17 * * * *`) : un Redis vidée entre deux passes
   rouvre une offre close pour une heure au plus. Accepté tant que la remise est marketing ; à durcir par un
   `SELECT … FOR UPDATE` sur la ligne de promo si elle porte un montant.
4. **Le `robots`/anti-ratés du chemin chaud.** `/r/{slug}` n'est pas limité par le cache de freins (choix
   assumé : un QR scanné en magasin doit rediriger), la protection est la limite de taille de Caddy et le
   cache de slug. Un agresseur qui **connaît** un slug peut donc compter les scans — c'est le prix du
   comptage honnête, noté ici pour qu'il ne soit pas découvert plus tard.

## Banc de charge

```bash
PYTHONPATH=vendor DJANGO_SETTINGS_MODULE=config.settings.bench python3 manage.py runserver 0.0.0.0:8124 --noreload &
PYTHONPATH=vendor DJANGO_SETTINGS_MODULE=config.settings.bench python3 scripts/bench_redirect.py \
    --target http://127.0.0.1:8124 --requests 16000 --concurrency 64
```

`config/settings/bench.py` existe pour que la mesure soit utile : sous `DEBUG=True`, Django consigne
chaque requête et le plancher de latence du harnais écrase tout.

Ce qui a été mesuré le 2026-09-11 sur un poste **2 cœurs**, serveur de dev + client dans le même
bac à sable :

- `GET /r/{slug}` (hit de cache, 0 requête SQL) : p50 44 ms, p99 176 ms, **518 req/s** à 64 connexions
  parallèles, 0 erreur.
- `GET /healthz/` (vue de contrôle, une requête SQL) : **exactement le même plancher**, 44 ms à
  concurrence 4, 90 req/s.

Les deux chiffres étant identiques, la latence observée est celle du **harnais** (un seul processus
Python, 2 cœurs, le client de test vole le GIL au serveur), pas celle du chemin de redirection : le
script de mesure du travail applicatif seul rend **0,776 ms** par `_lookup` en hit de cache et
**0,598 ms** pour la vue `scan_redirect` complète, soit ~1 290 req/s par cœur de travail applicatif.
Trois workers sur un VPS dédié ont donc la marge pour tenir 700 req/s — c'est une **projection**, pas
une mesure : le critère `p99 < 15 ms` doit être rejoué sur la machine cible avec k6.

## Déploiement sur le VPS Hostinger

Scénario A de la spec : une instance Postgres, PgBouncer devant, Redis, 3 conteneurs d'application.

### 1. Le terrain

1. DNS : un enregistrement `A` (et `AAAA`) pour **`qrstudio.kamcofarm.com`** vers l'IP du VPS. C'est
   le QR qui sera imprimé : le domaine est figé, un changement de domaine après tirage = flyers morts.
2. OS : Ubuntu 24.04 LTS, Docker + plugin compose. Une clé SSH, `sshd` fermé sur le port 22 par
   IP, `ufw allow 80,443` et rien d'autre en entrée.
3. Mémoire : 8 Go minimum pour le scénario A ; `shared_buffers` de Postgres réglé à ~2 Go
   (déjà posé dans `docker/compose.yml`).

### 2. L'application

```bash
git clone <dépôt> /opt/qrstudio && cd /opt/qrstudio/api
cp .env.example .env && python3 - <<'PY'
import secrets, string, pathlib, re
cle = "".join(secrets.choice(string.ascii_letters + string.digits + "!?#@") for _ in range(64))
env = pathlib.Path(".env").read_text()
env = re.sub(r"^DJANGO_SECRET_KEY=.*$", f"DJANGO_SECRET_KEY={cle}", env, flags=re.M)
env = re.sub(r"^DJANGO_ADMIN_PATH=.*$", "DJANGO_ADMIN_PATH=" + secrets.token_urlsafe(6), env, flags=re.M)
pathlib.Path(".env").write_text(env)
print("clé et chemin d'admin régénérés")
PY
$EDITOR .env          # BASE de donnees, Redis, e-mail, Google OAuth, SHORT_BASE_URL
docker compose -f docker/compose.yml up -d --build
docker compose -f docker/compose.yml exec app python3 manage.py createsuperuser
```

L'URL d'admin tirée au sort est dans `.env` (`DJANGO_ADMIN_PATH`). **La figer** est préférable à la
laisser vide : sinon elle change à chaque redémarrage et les favoris du personnel meurent.

La 2FA du personnel passe par `allauth.mfa` : après la première connexion, `/accounts/2fa/` pour
enrouler un TOTP. Un compte de personnel sans second facteur est **refusé** à l'entrée de l'admin
(`apps/common/middleware.py`, `MfaGateMiddleware`) — pas de repli ouvert.

### 3. TLS, en-têtes, journalisation

Caddy (dans la pile) demande et renouvelle le certificat, force HTTPS, pose HSTS, limite le corps
des requêtes à 1 Mo, expose `/healthz/` comme sonde amont, et journalise en JSON dans
`/var/log/caddy/access.log`. Aucun port autre que 80/443 n'est publié.

### 4. Ce qui tourne, et pourquoi

| Service | Rôle | Si ça tombe |
| --- | --- | --- |
| `app` (gunicorn/uvicorn ×4) | API + redirection + admin | Caddy renvoie un 502 ; la sonde `/healthz/` redémarre le conteneur |
| `worker` (Celery) | agrégation, purge, exports, e-mails | les stats prennent du retard, le scan continue |
| `beat` | ordonnance les mêmes tâches | idem, jusqu'à la relance |
| `postgres` | la vérité | le site devient en lecture seule sur les pages non cachees |
| `pgbouncer` | 12 connexions utiles au lieu de 5 000 | les workers saturent Postgres en quelques minutes |
| `redis` | cache de slugs, compteurs de frein, stream de scans | la redirection **continue** (retour base), l'ingest tamponne |

Le point qui compte dans le tableau : aucune de ces pannes ne transforme un scan en page d'erreur.
`hit_rate()` répond « autorisé » quand Redis est mort, et `_lookup` retombe sur une requête SQL
bornée. Un QR scanné en magasin doit rediriger.

### 5. Tâches récurrentes (déjà dans `beat`)

- `consume_scans` — vide le stream vers la table brute, par lots, `XACK` **après** écriture.
- `rebuild_daily_stats` — agrège la veille (voie SQL paramétrée sous Postgres).
- `flush_scan_counters` — compteurs Redis → agrégats.
- `sync_promo_usages` — compteurs d'usages des codes promo : Redis → `usages` en base, puis remise à zéro de la clé (sinon l'offre est soit survendue, soit close trop tôt). Toutes les heures suffisent : le plafond est un budget de campagne, pas un stock unique.
- `ensure_scan_partitions` — crée le mois suivant avant qu'il ne commence.
- `purge_expired_ips` — efface les adresses tronquées après 24 h, puis droppe les partitions au-delà
  de la rétention (13 mois). Jamais de `DELETE` sur la table d'audit.

### 6. Sauvegardes et mise à jour

```bash
# Sauvegarde logique quotidienne + WAL archivé si le PITR est requis ; à copier hors du VPS.
docker compose -f docker/compose.yml exec -T postgres pg_dump -U qrs -Fc qrs > /backup/qrs-$(date +%F).dump
# Rétention sugereée : 14 sauvegardes quotidiennes + 8 mensuelles.

git fetch && git checkout <tag> && docker compose -f docker/compose.yml up -d --build
```

Un déploiement = `migrate` (dans l'entrypoint, verrouillé pour qu'un seul conteneur le fasse), puis
`check --deploy` doit rester à zéro avertissement : la CI casse avant vous sur HSTS, cookies,
`SECRET_KEY`, `ALLOWED_HOSTS`.

### 7. Check-list avant d'imprimer

- [ ] `curl -sSI https://qrstudio.kamcofarm.com/healthz/` → 200 (et 301 **absent** : la sonde est
      exempte de redirection TLS, voir `SECURE_REDIRECT_EXEMPT`).
- [ ] Un QR de test imprimé **sur papier**, scanné avec un téléphone en 4G → 302, et la ligne
      apparaît dans `/api/v1/qr/{id}/stats/` au tour d'agrégation suivant.
- [ ] E-mail de vérification reçu **en boîte** (pas en spam) : SPF + DKIM + DMARC sur l'expéditeur.
- [ ] `docker compose exec postgres psql -U qrs -d qrs -c "select count(*) from analytics_scanevent"` 
      avance quand on scans.
- [ ] k6 sur la machine cible : `p99 < 15 ms` à 1 000 req/s (le seuil de bascule vers une passerelle
      Go dédiée est à ~5 000 req/s soutenus, cf. spec §8).
- [ ] Registre de traitement RGPD + mention d'information mise à jour (mesure sans cookie, pays seul).

## Variables d'environnement

Tout est dans [`.env.example`](.env.example), commenté. Les quatre qui cassent le service si elles
sont fausses : `DATABASE_URL` (vers PgBouncer, port 6432), `REDIS_URL`, `DJANGO_ALLOWED_HOSTS`,
`SHORT_BASE_URL` (c'est lui qui compose l'URL imprimée).

## Ce qui n'est **pas** vérifiable dans ce bac à sable

Déclaré pour qu'aucun de ces points ne soit pris pour « testé » :

1. `docker compose up` à froid — ni Docker ni démon conteneurs ici. Les fichiers sont écrits pour ça,
   pas exécutés.
2. Le comportement **PostgreSQL** au runtime : partitionnement natif, `ensure_scan_partitions`,
   `INSERT … ON CONFLICT` réel, PgBouncer `transaction` (le DDL et le SQL sont vérifiés
   textuellement et par test unitaire, l'exécution sur 17 n'a pas eu lieu).
3. `k6` et le critère `p99 < 15 ms` — remplacé par `scripts/bench_redirect.py`, mesures ci-dessus.
4. L'OAuth **Google** réel (client ID/secret manquants côté client), l'envoi SMTP effectif
   (SPF/DKIM non posés), la politique payante (les quotas sont donc des valeurs par défaut, pas des
   décisions métier).
5. L'authentification de PgBouncer (`auth_type`/`auth_query`) selon que Postgres est en scram ou
   md5 — à trancher au premier déploiement, note dans `docker/conf/pgbouncer-extra.ini`.

## Arborescence

```
api/
├── config/{settings/{base,dev,prod,test,bench,ci}.py, env.py, urls.py, asgi.py, wsgi.py, celery.py}
├── apps/
│   ├── accounts/    # User, codes de vérification, reset, allauth, admin
│   ├── qr/          # QrCode/QrVersion/templates, cache de résolution, validators SSRF, quotas
│   ├── redirect/    # GET /r/{slug} — le chemin chaud
│   ├── analytics/   # ScanEvent (brut partitionné), QrDailyStats, geo, ingest, agrégats, 5 commandes
│   └── common/      # ip, cache_tools, redis_client, streams, throttling, pagination, export,
│                    # middleware (IP client, garde MFA, durcissement admin), tasks, shortid
├── tests/           # 337 tests : comptes, QR, validation, redirection, analytics, admin, sécurité, art/promo
├── scripts/bench_redirect.py
├── docker/{Dockerfile, compose.yml, entrypoint.sh, conf/{Caddyfile, pgbouncer-extra.ini}}
├── .github/workflows/ci.yml
├── requirements.txt / requirements-dev.txt / requirements.lock.txt
└── .env.example
```

## Facturation : Stripe et paiement mobile

**Prix = `apps/accounts/plans.py`, source unique.** Stripe ne les reçoit que par `STRIPE_PRICE_*`
(l'identifiant de produit créé dans la console) et **aucun montant n'est accepté depuis le client** :
le prix du lien de paiement est celui du palier demandé, et chaque webhook est recoupé — un écart
n'accorde **pas** le palier, journalise l'événement `traite=False` avec l'écart dans `erreur` et répond
quand même 200 (un 5xx ferait rejouer Stripe indéfiniment).

    POST /api/v1/billing/checkout          {"palier":"premium"} -> 201 {url, expire_le}
    GET  /api/v1/billing/etat              -> {abonnement|null, licence, acorde}
         abonnement = {fournisseur, statut, palier, periode_fin, en_sursis_jusqu_a, annule_le, en_registre}
         `acorde` = le droit d'usage reel (l'abonnement arbitre), pas le champ `User.plan`
    POST /api/v1/billing/mobile/demande    {"palier":"standard","telephone":"+39..."} -> 201
         {reference, statut, instruction, code_a_utiliser, expire_le, montant_centimes, devise,
          montant_affiche}
         `montant_centimes` = la valeur de nos livres (centimes d'euro) ; `montant_affiche` = ce que le
         client doit reellement valider sur son telephone, dans la devise du reseau. Le front affiche le
         second et ne recalcule rien.
    GET  /api/v1/billing/mobile/<reference> -> {reference, statut, palier, expire_le, confirme_le, instruction}
         (`mobile/callback` est declare AVANT ce motif : sinon l'agregateur recoit un 405)
    POST /api/v1/billing/mobile/callback   payload signe de l'agregateur -> {recu: true}
    POST /api/v1/billing/webhook/stripe    `Stripe-Signature` exige (timing-safe, +/- 300 s) -> {recu: true}

Codes d'erreur, tous sous `{error:{code,message}}` comme le reste de l'API : `palier_inconnu` et
`palier_infacturable` (400 — le Gratuit ne se facture pas), `palier_deja_actif` (403),
`stripe_non_configure` et `stripe_price_manquant` (503), `mobile_provider_non_configure` (503),
`signature_invalide` (400 sur le webhook Stripe, avec le motif : `secret_de_webhook_absent`,
`signature_absente`,
`timestamp_trop_ancien` ; 403 sur le rappel mobile, qui n'a pas de secret partagé à ce stade), `montant_incoherent` (400 sur `mobile/callback`, apres journalisation du refus),
`reference_inconnue` (404), `mobile_telephone_invalide` (400 — le numero part au reseau sans espaces ni
`+`, et un `+` perdu revient en rejet opaque trois minutes plus tard), `mobile_provider_invalide` (503 —
reglage d'agregateur incomplet ou incoherent : cle vide, `secret_hash` absent, devise du reseau sans taux),
`mobile_provider_injoignable` (502 — le reseau n'a pas repondu ; **aucun** `PaiementMobile` n'est cree dans ce
cas, sinon la ligne resterait en attente pour rien), `mobile_provider_erreur` (502 — le reseau a repondu,
mais en erreur : `processor_response` est recopie dans le message). Verifie en reel sur l'instance de dev : `checkout` sans cle Stripe renvoie
503 `stripe_price_manquant` en nommant la variable d'environnement qui manque, `mobile/demande` renvoie
503 `mobile_provider_non_configure`, et aucun des deux n'a cree d'`Abonnement` ni bouge `User.plan`.

Ce que l'API accepte et ce qu'elle ne fait pas :

| Événement Stripe | Effet |
| --- | --- |
| `checkout.session.completed` | palier accordé, `periode_fin = maintenant + 30 j` (corrigé au premier `subscription.updated`, seule source exacte) |
| `customer.subscription.created/updated` | statut collé au réel (`trialing`/`active`/`past_due`/`canceled`), période reprise telle quelle |
| `invoice.payment_failed` | `past_due` + sursis de `BILLING_GRACE_DAYS` (3 j) pendant lequel **rien n'est coupé** |
| `invoice.payment_succeeded` | `trialing`/`active` selon le champ Stripe |
| `customer.subscription.deleted` | repasse à `free`, **ne supprime aucun QR** — les slugs dynamiques continuent de rediriger |
| tout autre | 200 + `erreur="type non géré"` (ne pas 400 sur un événement qu'on ne connaît pas = se faire rejouer tout le trafic) |

Signature vérifiée à la main (`HMAC-SHA256`, format `t=<epoch>,v1=<hex>`, **plusieurs `v1` séparés par
des virgules** acceptés — Stripe en émet plusieurs pendant une rotation de secret) : pas de SDK Stripe,
donc pas de dépendance à la version d'API. `apps/billing/stripe_api.py`.

**Le mobile-money n'est pas branché** : `apps/billing/mobile.py` pose le contrat (`FournisseurMobile`),
`FournisseurInactif` répond 503 `provider_disabled` quand aucun fournisseur n'est configuré — un
paiement inventé de toutes pièces serait pire. La démo qui « accepte » est refusée hors dev ; le
rappel de l'agrégateur est vérifié sur le **montant** comme sur la signature, un rappel après expiration
est journalisé et refusé, et `demande` ne change jamais le plan.

Vendu : le plan est arbitré par `Abonnement` (statut + `periode_fin` + sursis), pas par `User.plan`,
qui n'est qu'un cache lu par la licence à chaque appel ; `plan_effectif` invalide son cache d'instance
au `refresh_from_db()`, sinon un sursis expiré reste invisible tant que l'objet vit.

### Flutterwave : ce que l’adaptateur garantit

L’agrégateur mobile retenu est **Flutterwave** (`MOBILE_MONEY_PROVIDER=flutterwave`), qui couvre MTN MoMo,
Airtel Money, M-PESA et Orange Money sur Ouganda / Kenya / Tanzanie / Zambie / Afrique francophone. Le
contrat d’adaptateur vit dans `apps/billing/mobile.py` ; `demo` (fil de simulation, development seulement)
et `flutterwave` l’implementent, et `fournisseur_actif()` refuse tout le reste plutôt que de tomber sur un
fallback silencieux.

    MOBILE_MONEY_PROVIDER=flutterwave
    MOBILE_MONEY_FLUTTERWAVE_SECRET_KEY=FLWSECK-…      # cle d'initialisation (Bearer)
    MOBILE_MONEY_FLUTTERWAVE_SECRET_HASH=…             # secret hash des webhooks — sans lui : 503
    MOBILE_MONEY_FLUTTERWAVE_TYPE=mobile_money_uganda  # mpesa | mobile_money_franco | …
    MOBILE_MONEY_FLUTTERWAVE_RESEAU=MTN
    MOBILE_MONEY_FLUTTERWAVE_DEVISE=EUR               # la devise reellement demandee au reseau
    MOBILE_MONEY_FLUTTERWAVE_TAUX=                    # obligatoires si DEVISE != EUR
    MOBILE_MONEY_FLUTTERWAVE_ARRONDI=0.01
    MOBILE_MONEY_FLUTTERWAVE_BASE_URL=https://api.flutterwave.com

Quatre regles, toutes testes (`tests/test_flutterwave.py`, 10 tests) :

1. **Appeler** : `POST /v3/charges?type=<type>`, corps `amount` (en **unites**, pas en centimes),
   `currency`, `email`, `phone_number`, `tx_ref`, `fullname`, `network`, `client_ip`. `tx_ref` est **notre**
   reference `QRM-…` : c’est elle qui retrouve la ligne en base quand le rappel arrive ; `flw_ref` et l’`id`
   du fournisseur ne sont conservés que dans `PaiementMobile.detail`, pour le rapprochement comptable.
   L’e-mail du payeur est celui du compte — jamais une adresse inventee.
2. **Devise** : la grille est en euros, les rails en monnaie locale. La conversion n’existe qu’à un seul
   endroit (`montant_attendu`) ; sans `TAUX` configure, la demande est refusee en 503 `mobile_provider_invalide`
   **plutot que de deviner**. Le montant converti est grave dans `detail["montant_facture"]`/`devise_facturee`
   au moment de la demande, et c’est lui — pas le taux du jour — qui sert a juger le rappel : un changement
   de taux entre la demande et la confirmation ne doit pas transformer un paiement legitime en trop-percu.
3. **Rappel** : la signature est acceptee sous ses **deux** formes documentees par Flutterwave — `Verif-Hash`
   egal au secret hash recopie, et `flutterwave-signature` = HMAC-SHA256 du corps brut en base64 — comparees
   en temps constant. Un rappel n’accorde **jamais** rien seul : `reverification_obligatoire` impose de
   re-interroger `GET /v3/transactions/verify_by_reference?tx_ref=…` et de retrouver `status` confirme **et**
   le montant grave. Sans re-interrogation possible, la vue renvoie `{"recu": true, "attente_reverification":
   true}` et laisse le paiement en attente ; avec, `constater()` fournit la verite monetaire.
4. **Statuts normalises** : `successful|success|confirmed|paid|completed` → `confirmed`, `pending|
   success-pending-validation` → `pending`, `failed|cancelled|invalid` → `failed`, `refunded` → `refunded`.
   Une reponse de verification absente ou illisible n’est **pas** une autorisation : le paiement attend.

En dev, le reseau est simule au niveau de `requests.request` (`tests/test_flutterwave.py`) : l’URL,
l’en-tete et le corps sont verifies, ce qui est le seul moyen de prouver qu’un montant ne part pas dans la
mauvaise unite. Les releves de la documentation Flutterwave etant contradictoires entre versions (`/v3/charges`
vs `/v3/payments`, `Verif-Hash` vs HMAC), l’adaptateur accepte les deux formes de signature et ne se fie a
aucune : la seule source de verite est la re-interrogation de la transaction.

## Espace admin : analytique des scans et creation gratuite

`/manage/` est l’espace du personnel (`apps/common/views_backoffice.py`, gabarits `templates/manage/*.html`,
héritent d’`admin/base_site.html`). Il est distinct de `django-admin` : il ne fait pas de CRUD, il lit des
agregets et cree des QR par le **même** service que l’API.

    GET  /manage/                      etat du service (volumes, scans de la veille, cache, files)
    GET  /manage/analytique/           ?jours=7|30|90&kind=static|dynamic&origine=client|admin|import
    GET  /manage/analytique/qr/<id>/   drill-down d’un QR : serie 90 j, pays, versions, apercu SVG regenere
    POST /manage/analytique/recalcul/  reconstruit les agregats de 1 a 30 jours (au-dela : la commande)
    GET  /manage/qr/creer/             formulaire de creation, statique et dynamique, gratuit
    POST /manage/qr/<id>/supprimer/    suppression logique par `apps.qr.services.soft_delete`
    GET  /manage/abonnements/          abonnements, paiements mobiles, evenements non traites — lecture seule

**L’exemption est une regle, pas une accumulation de `if`.** `apps/accounts/exemption.py::exonere_de_facturation`
repond `vrai` pour un **superutilisateur** ou pour un compte portant la permission **nommee**
`qr.creer_sans_facturation`. Elle est utilisee par `apps/qr/quota.py` (portes 402), par
`User.a_droit_a` (fonctionnalites payantes : export PDF, analytics — sans quoi l’admin pourrait creer un QR
mais pas le mesurer) et publiee par `/auth/me` sous `exonere_de_facturation`, donc par `/billing/etat` et le
front. `is_staff` seul ne donne **rien** : sans cette distinction, tout compte de support ou de staging
devient un trou dans les revenus.

    # etendre l’espace a un binome, sans toucher au code :
    python3 manage.py shell -c "from django.contrib.auth.models import Permission, Group; \
    g, _ = Group.objects.get_or_create(name='Analytique QR Studio'); \
    g.permissions.add(Permission.objects.get(codename='creer_sans_facturation'), \
                      Permission.objects.get(codename='voir_analytique_plateforme'))"

**Pourquoi une table de plus.** `analytics_platformdailystats` (une ligne par jour) porte les totaux
plateforme : scans, visiteurs, melange statique/dynamique, part `admin`, bots bloques. Sans elle, le tableau
de bord devrait agreger `analytics_qrdailystats` — une ligne par (QR, jour, pays), soit des dizaines de
millions de lignes a 1 M d’utilisateurs — a chaque rafraichissement. Elle est alimentee par
`aggregates.aggreger_plateforme()`, appele a la fin de chaque `rebuild_for_day`, donc par la tache de nuit et
par le bouton de recalcul. Les filtres `kind`/`origine` redescendent consciemment dans `QrDailyStats` avec une
sous-requete sur `QrCode` (index `qr_kind_active_idx`) : c’est exact, plus lent, et la page le dit.

**`QrCode.origine`** (`client` | `admin` | `import`) est ecrit **apres** `services.create_qr()`, jamais par
le serializer : un client ne doit pas pouvoir declarer lui-meme qu’il est l’administration. C’est ce champ qui
permet d’ecrire, dans l’analytique, « le volume que la grille ne facture pas », et de verifier qu’aucune
creation payante n’a ete detournee par l’espace gratuit (`tests/test_backoffice.py` compare les deux chemins
sur la meme donnee : 302 cote admin, 402 `plan_required` cote client).

Deux details trouves en chemin et corriges parce que testes : les graphiques sont de l’**SVG ecrit cote
serveur** (`apps/common/charts.py`, un seul `mark_safe` centralise, `escape()` a la source, verifie par test) —
aucune dependance JS ni CDN ; et `/manage/` ne renvoie plus la page de connexion a un compte deja connecte
qui n’est pas du personnel, il repond **403** : `staff_member_required` + page de connexion d’allauth produit
une boucle de redirection infinie (`ERR_TOO_MANY_REDIRECTS`, mesure depuis le navigateur).

### URLs de retour du paiement

`success_url` et `cancel_url` du checkout, et le `return_url` du portail client, sont construits sur
**l'origine publique du site** (`settings.QR["SHORT_BASE_URL"]`, la même que les liens des e-mails) et
pointent donc sur `/facturation` et `/facturation/retour` du front. Ce n'était pas le cas avant cette correction : `getattr(settings, "SHORT_BASE_URL", …)` cherchait une variable de module qui n'existe pas,
retombait sur `http://localhost:8000` : en production, un client qui venait de payer était rejeté sur
la machine de l'opérateur — et le portail fabriquait une URL relative que Stripe refuse. Gardé par
`tests/test_billing.py::test_urls_de_retour_utilisent_l_origine_publique`.

En dev, `config/settings/dev.py` ajoute les origines du front à `CSRF_TRUSTED_ORIGINS`
(`localhost:5173`, `127.0.0.1:5173`, `https://*.e2b.app`) et lève `CSRF_COOKIE_HTTPONLY` : sans ça,
tout `POST` authentifié à travers le proxy Vite revient en 403 « CSRF Failed », un code d'erreur qui
ne ressemble à rien de connu côté utilisateur.

## Vérifier SPF / DKIM / DMARC avant d'envoyer

    manage.py check_email_dns [--domaine X] [--json]     # code 1 si le minimum manque

Lit `DEFAULT_FROM_EMAIL`, interroge SPF, les sélecteurs `*._domainkey` et `_dmarc`, et **recolle les
TXT multichaînes** (une clé DKIM RSA 2048 arrive en deux morceaux : jugés séparément, ils font croire à
un domaine non signé). Le verdict est borné : un CNAME `_domainkey` publie une clé, il ne prouve pas que le
**signage** est actif dans la console de l'hébergeur — la seule preuve est l'en-tête
`DKIM-Signature:` d'un message reçu. `apps/common/email_dns.py` sépare la résolution du jugement,
et l'état « DNS non résolvable » est distingué de « enregistrement absent » (jamais la même alerte).

### Brancher le controle sur le deploiement et la CI

```bash
# 1. le verdict humain (code 1 si le minimum manque)
make check-email
# 2. le JSON brut, pour un journal ou une CI sans jq
make check-email-json
# 3. le garde-fou : le JSON juge, compare a ce que le deploiement ATTEND
make check-email EMAIL_ATTENTE="--attendu kamcofarm.com --politique quarantine"
python3 scripts/check_email_gate.py --strict --json-out /var/log/qrstudio/email.json
```

`scripts/check_email_gate.py` n'ajoute aucun jugement au controle : il compare le verdict publie a une
**attente fournie par le deploiement**, et c'est la qu'il sert. Ses codes de sortie sont distincts exprès,
parce qu'un CI qui ne rend que 0/1 ne dit pas si le courrier est mort ou si c'est le domaine qui a change :

| code | sens | ce qu'on fait |
| --- | --- | --- |
| 0 | prêt, conforme a l'attente | on continue |
| 1 | pas de SPF utilisable **ou** aucune clé DKIM derrière les sélecteurs attendus | on ne déploie pas l'envoi |
| 2 | le contrôle n'a pas pu rendre de verdict (DNS injoignable, `DEFAULT_FROM_EMAIL` inexploitable) | on rejoue depuis le VPS |
| 3 | le courrier partirait, mais `--attendu` / `--politique` / `--strict` ne sont pas satisfaits | on corrige la zone DNS |

Deux details qui comptent, mesures ici : `--skip-checks` est passe a `manage.py` parce qu'un verdict DNS ne
doit pas dependre de l'importabilite de toutes les integrations (une dependance optionnelle cassee faisait
echouer le garde-fou sur un traceback d'import, alors que SPF/DKIM/DMARC se lisent sans charger les URLs) ;
et le script **herite de l'environnement recu**, sinon il jugerait un `DEFAULT_FROM_EMAIL` different de celui
qui sert reellement, et passerait au vert pour rien.

A brancher avant `collectstatic`/`migrate` dans le pipeline de mise a jour, et une fois par semaine en job
planifie — le DNS peut bouger sans que personne ne redéploie :

```cron
30 6 * * 1  cd /opt/qrstudio/api && make check-email EMAIL_ATTENTE="--attendu kamcofarm.com" >> /var/log/qrstudio/email-check.log 2>&1
```

### Corriger les trois points chez Hostinger

Tout se passe dans **hPanel → Domains → `kamcofarm.com` → Manage → DNS Zone** (le même écran où le SPF et
les CNAME DKIM ont été posés). Trois interventions, dans cet ordre :

1. **Réécrire le TXT `_dmarc`** — dans la liste, ligne `_dmarc` / `TXT`, ouvrir au crayon, remplacer la
   valeur, TTL **3600**, enregistrer. A coller tel quel (espaces après les virgules supprimés ; `ruf` et
   `fo=1` ajoutés pour que l'observation rapporte aussi les demi-échecs) :

   ```text
   v=DMARC1; p=none; pct=100; adkim=s; aspf=s; rua=mailto:postmaster@kamcofarm.com,mailto:dmarc@kamcofarm.com; ruf=mailto:postmaster@kamcofarm.com; fo=1
   ```

   **Un seul enregistrement `_dmarc` dans la zone.** Si l'ancien reste à côté du nouveau, la politique est
   ignorée entièrement : ce n'est pas « la plus sévère gagne », c'est « aucune ne s'applique ».
2. **Créer les deux boîtes de rapport** — hPanel → **Emails → Mailboxes → Select domain → `kamcofarm.com`** :
   créez `postmaster@` (ou un alias vers une boîte que vous lisez) puis `dmarc@`. Un rapport adressé à une
   boîte inexistante ne revient pas en erreur : il n'arrive simplement jamais, et la phase `p=none` se
   termine sans rien vous avoir appris.
3. **Noter le sélecteur qui signe** — Emails → **Domain settings** (le panneau d'état SPF/DKIM) : les trois
   CNAME existent, **un seul porte une clé** ; `hostingermail-b` et `-c` répondent `v=DKIM1;p=`, c'est-à-dire
   rien. Laissez-les (ils servent à la rotation), mais gardez le sélecteur actif en tête : si Hostinger fait
   basculer la signature sur `b`, le courrier partira non signé avec un DNS qui a l'air impeccable.

Puis, dans l'ordre :

```bash
dig +short TXT _dmarc.kamcofarm.com                             # la valeur exacte doit revenir
make check-email EMAIL_ATTENTE="--attendu kamcofarm.com"        # 0 = prêt ; ajouter --strict plus tard
```

La propagation est rapide chez Hostinger (quelques minutes), mais les receveurs gardent les rapports en
cache : un `rua` corrigé se voit dans les rapports du jour suivant, pas dans l'heure. Et si les nameservers
du domaine ne pointent pas Hostinger, l'écran DNS Zone d'hPanel **ne publie rien** — vérifiez d'abord
`dig +short NS kamcofarm.com` et corrigez chez le détenteur réel de la zone.

### État mesuré de `kamcofarm.com` (2026-09-15, DNS réel, depuis cette machine)

    racine TXT      v=spf1 include:_spf.mail.hostinger.com ~all
                    └─ include:_spf.mail.hostinger.com = v=spf1 include:relay.mail.hostinger.com
                       include:relay.mailchannels.net ~all      # les deux sont des domaines TIERS
    hostingermail-a  CNAME → hostingermail-a.dkim.mail.hostinger.com → cle RSA 2048 (2 morceaux TXT) ✓
    hostingermail-b  CNAME → …                                   → `v=DKIM1;p=`  ← **clé vide**
    hostingermail-c  CNAME → …                                   → `v=DKIM1;p=`  ← **clé vide**
    _dmarc TXT      v=DMARC1; p=none; rua=mailto:postmaster@kamcofarm.com, mailto:dmarc@kamcofarm.com;
                    pct=100; adkim=s; aspf=s
    MX              mx1.hostinger.com (5), mx2.hostinger.com (10)

`check_email_dns` sort donc **code 0 avec quatre remarques** — c'est le bon état pour envoyer, pas pour
dormir dessus. Ce que chacune veut dire, et quoi faire :

1. **`p=none` : surveillance, pas protection.** Un attaquant qui envoie depuis `factures.kamcofarm.com`
   aujourd'hui ne rencontre aucun refus fondé sur le DMARC ; il rencontre au pire le SPF/DKIM *du
   destinataire*. Le `none` sert à une chose : collecter les rapports pour savoir ce que la politique
   casserait. Durée utile : 7 à 14 jours couvrant un vrai cycle d'envoi (les codes de vérification, les
   réinitialisations de mot de passe, le digest éventuel).
2. **Espace après la virgule dans `rua`.** L'ABNF de la RFC 7489 le tolère, mais des receveurs (et la
   plupart des outils de lecture) découpent sur `,` et rejettent l'URI suivante : le premier adresse
   passe, la seconde est perdue, et rien ne le dit. À publier sans espace :

       v=DMARC1; p=none; pct=100; adkim=s; aspf=s;
       rua=mailto:postmaster@kamcofarm.com,mailto:dmarc@kamcofarm.com; ruf=mailto:postmaster@kamcofarm.com; fo=1

   `fo=1` vaut le coup pendant la phase d'observation : par défaut un rapport n'est émis que si **tous**
   les mécanismes d'alignement échouent ; avec `fo=1` on voit aussi les demi-échecs (le SPF qui passe
   sans s'aligner, ci-dessous), qui sont justement ce qu'on cherche.
3. **`aspf=s` ne survivra probablement pas au relais.** L'alignement SPF se juge sur le *Return-Path*, et
   `include:relay.mailchannels.net` (le mutualisé d'Hostinger) écrit un Return-Path qui n'est pas
   `kamcofarm.com`. Résultat attendu dans les rapports : `spf=fail (alignment mismatch)` sur une large part
   du trafic, **alors que le contrôle SPF passe**. Ce n'est pas cassé — DMARC passe si DKIM s'aligne — mais
   cela veut dire que toute la valeur de la politique repose sur DKIM. Deux conséquences : ne pas passer à
   `quarantine` avant d'avoir vu `dkim=pass` aligné sur le volume qui compte ; et garder `adkim=s`
   uniquement si Hostinger signe bien avec `d=kamcofarm.com` (à lire dans un message reçu, pas dans le DNS).
4. **Deux sélecteurs publiés sans clé.** `v=DKIM1;p=` n'est pas une signature : c'est Hostinger qui a créé
   les trois CNAME et activé un seul sélecteur. Aucun impact tant que le signing utilise `hostingermail-a`,
   mais si la rotation passait par `b`, le courrier partirait non signé sans que le DNS ait l'air faux.
   La vérification DNS seule ne dirait toujours pas la vérité : l'unique preuve est l'en-tête
   `DKIM-Signature: d=kamcofarm.com; s=hostingermail-a;` d'un message réellement reçu.

Et une condition hors DNS : **`postmaster@` et `dmarc@` doivent exister** comme boîtes (ou alias) chez
Hostinger, sinon la phase `none` ne rapporte rien du tout. Les agrégats arrivent en XML compressé ; pour
les lire sans outil, `python3 manage.py check_email_dns --json` reste le contrôle le plus rapide de ce qui
est publié, et une seule boîte de collecte, `postmaster@`, suffit pour démarrer l'observation.

### Passer à l'étape suivante, et quand

    # 1) observation (état actuel, corrige juste l'espace de `rua`)
    v=DMARC1; p=none; pct=100; adkim=s; aspf=s; rua=mailto:postmaster@kamcofarm.com,mailto:dmarc@kamcofarm.com; fo=1

    # 2) quarantaine, une fois vus : 100 % du trafic transactionnel en `dkim=pass` aligné, et zéro volume
    #    legitime venant d'un autre hote (newsletter, CRM, facture tiers) — sinon ce sont ces envois-la
    #    qui partiront en spam.
    v=DMARC1; p=quarantine; pct=100; adkim=s; aspf=s; rua=mailto:postmaster@kamcofarm.com,mailto:dmarc@kamcofarm.com

    # 3) rejet, seulement si le point 2 tient une semaine complete sans plainte
    v=DMARC1; p=reject; adkim=s; aspf=s; rua=mailto:postmaster@kamcofarm.com,mailto:dmarc@kamcofarm.com

Ne pas oublier `sp=` à l'étape 2 : absent, les sous-domaines héritent de `p`, ce qui est normalement ce
qu'on veut ; présent et plus sévère, il protège explicitement `news.`, `factures.` et consorts qui n'envoient
rien et sont donc le premier choix d'un imposteur.

La CI (et un déploiement) peut couper sur ce contrôle : `check_email_dns` sort **1** quand SPF ou une clé
DKIM manque — donc avant `collectstatic`/`migrate` sur le VPS, pas après l'incident.

Deux precisions mesurées aujourd'hui, parce que l'outil juge le domaine et se juge mal lui-même :
le code de sortie est rendu par `SystemExit`, pas par la valeur de retour de `handle()` — `BaseCommand.execute`
écrit ce retour sur stdout dès qu'il est truthy, et `return 1` faisait donc échouer la commande en
`AttributeError`, un traceback qui donnait *par accident* le code 1 attendu. Le test joue `call_command` pour
vérifier le chemin réel. Et les sélecteurs interrogés viennent de `EMAIL_DKIM_SELECTEURS` (défaut : les trois
d'Hostinger) — changer de relais sans toucher à ce réglage ferait annoncer « domaine non signé » à un domaine
parfaitement configuré.

