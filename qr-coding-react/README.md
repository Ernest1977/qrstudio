# QR Coding — React

Studio de QR code 100 % local (aucune donnée ne quitte le navigateur), réécrit en React à partir
de `uploads/qr-coding (1).html`. Analyse complète des bugs d'origine : **[`ANALYSE.md`](ANALYSE.md)**.

## Démarrer

```bash
npm install
npm run dev        # http://localhost:5173
npm run build      # → dist/ (statique, à poser sur n'importe quel CDN)
npm run preview    # sert le build
npm run test:lib      # 27 tests (dont décodage réel des rendus)
npm run test:browser  # 12 tests e2e dans Chromium (nécessite `npm run build` avant)
```

`test:browser` télécharge une instance de Chromium via Puppeteer au premier lancement ; en
environnement Debian 13, le binaire a besoin de `libnss3`, `libatk-bridge2.0-0t64`,
`libasound2t64`, `libgbm1`, `libpango-1.0-0`, `libcairo2` (et cousins `t64`).

## Structure

```
src/
  main.jsx, App.jsx          montage + page (Hero, Types, Studio, How, Features, Footer)
  state.js                   reducer : { type, dataByType, design } + ECC_LOGO_BUDGET
  hooks/useStudio.js         orchestration : saisie → payload → matrice → SVG → avertissements
  lib/
    types.js                 les 22 types, leurs champs, leurs valeurs par défaut, nettoyage
    payload.js               constructeurs vCard / Wi-Fi / iCal / mailto / sms / liens…
    qrcode.js                matrice (lib `qrcode`) + rendu SVG (finders, styles, logo, légende)
    files.js                 contrôle des signatures binaires à l'import
    export.js                PNG (×1…×8), SVG, copie presse-papier, data URL
  components/                TypePicker, DynamicFields, Preview, Studio, marketing…
scripts/
  lib.test.mjs               tests de bibliothèque + décodage ZXing
  browser.test.mjs           e2e sur le build réel (captures d'écran décodées, exports vérifiés)
```

## Choix de portage

- **`qrcode` (npm)** remplace le CDN `qrcode-generator` : plus de dépendance réseau, version
  épinglée, et une API qui expose la *capacité réelle* (utile pour les messages d'erreur).
- **Un seul rendu** pour l'aperçu et l'export : le PNG monte en résolution par facteur d'échelle,
  donc ce que vous voyez est exactement ce que vous téléchargez.
- **L'état vit dans React**, pas dans le DOM : changer de type puis revenir ne perd plus la saisie,
  et les valeurs par défaut n'écrasent plus ce qui a été tapé.
- Le QR est **généré côté client** : le site peut être servi en statique sur un CDN, il ne nécessite
  aucun serveur applicatif pour fonctionner.

## `/facturation` — abonnement Stripe et suivi d'un paiement mobile

La page est une vraie route (`src/lib/router.js`, deux routes, zéro dépendance ajoutée) parce que le
**backend y renvoie le client** : `cancel_url` du checkout est `{originesite}/facturation?annule=1` et
`success_url` `{originesite}/facturation/retour?session_id=…`. Sans page sur ces URL, un paiement
réussi atterrit sur une 404.

Ce qu'elle appelle, et rien d'autre :

| Action | Appel | Ce qui en revient |
| --- | --- | --- |
| ouvrir la page | `GET /api/v1/billing/etat` (repli `GET /api/v1/auth/config` sans session) | grille + palier courant |
| se connecter / s'inscrire | `POST /api/v1/auth/login`, `/auth/register`, puis `/auth/verify` | session + **jeton CSRF neuf** |
| « S'abonner » | `POST /api/v1/billing/checkout` `{"palier": "premium"}` | `{url}` → on part chez Stripe |
| « Payer par mobile » | `POST /api/v1/billing/mobile/demande` `{"palier","telephone"}` | référence, instruction, code, expiration |
| suivi | `GET /api/v1/billing/mobile/<réf>` toutes les 5 s | statut ; la reprise survit à un rechargement (`sessionStorage`) |
| gérer l'abonnement | `POST /api/v1/billing/portail` | URL du portail client Stripe |

Trois règles de conception, parce que c'est de l'argent :

* **aucun prix n'est écrit dans le front** — la grille vient de `apps/accounts/plans.py` via l'API ; un
  libellé de caractéristique inconnu s'affiche en clair (`PALIERS_LABELS` ne fait que traduire) plutôt
  que de disparaître silencieusement ;
* **le corps d'une demande d'abonnement ne contient jamais de montant**, seulement `{"palier": …}` ;
  c'est vérifié dans le test, et un `montant` ajouté par erreur à cet endroit se ferait reprendre par le
  recoupement du webhook, pas par la bonne foi du client ;
* **la page n'accorde rien** : après un retour de Stripe elle relit `/billing/etat` dix fois sur vingt
  secondes, puis dit honnêtement « pas encore confirmé ». Un 503 de fournisseur non configuré est
  affiché comme tel, avec la mention *(réglage d'exploitation manquant)* — jamais comme un succès.

### Tests

    npm test                     # build + 52 tests (studio, lib, caisse sur bouchon, caisse sur vrai backend)
    node --test scripts/facturation.test.mjs       # la page contre un faux service deterministe
    node --test scripts/facturation.api.test.mjs   # la page contre l'API reelle — s'auto-saute si le backend manque

Le second fichier est celui qui prouve la plomberie CSRF : Vite proxifie `/api` avec
`changeOrigin: true`, donc Django voit un `Host: 127.0.0.1:8000` alors que l'`Origin` du navigateur est
celle du front. Sans `CSRF_TRUSTED_ORIGINS` dans `api/config/settings/dev.py` (y compris
`https://*.e2b.app` pour l'aperçu), **toute** écriture authentifiée revient en 403 « CSRF Failed ».
Le test échoue là-dessus si on retire l'origine : vérifié en l'enlevant réellement.

Le troisième lot de la caisse couvre ce que le bouchon ne peut pas prouver :

- **un compte d'administration ne voit aucune caisse** — `/auth/me` publie `exonere_de_facturation`, la page
  remplace alors les boutons « S'abonner »/« Payer par mobile » par un encadré qui renvoie vers
  `/manage/qr/creer/` (l'espace admin Django, où la création est gratuite par construction). C'est le serveur
  qui décide ; le front ne fait que le dire, et aucun bouton retiré n'a jamais empêché une facturation ;
- **le mobile affiche le montant du réseau** — quand l'agrégateur facture en monnaie locale, le serveur
  renvoie `montant_affiche` (« 11960.00 UGX ») et c'est lui qui s'affiche, la valeur de la grille restant
  visible à côté en centimes d'euro. Le front ne convertit jamais rien ;
- côté **vrai backend**, le même fichier vérifie que `/manage/qr/creer/` n'est pas une porte du front : un
  anonyme est renvoyé vers la connexion, un compte client connecté reçoit un **403** (renvoyer ce dernier vers
  `/accounts/login/` produirait une boucle de redirection, allauth éjectant un déjà-connecté — mesuré en
  `ERR_TOO_MANY_REDIRECTS`, corrigé côté API par `acces_admin`).

Pour que `/manage/` réponde en développement, `vite.config.js` proxifie `^/manage/` vers l'API, à côté de
`/api`. Le motif est ancré exprès : `/manage` seul ferait aussi correspondre `/manage-9f2`, l'URL de
`django-admin`.

Enfin : `npm test` pilote un vrai navigateur **depuis une seule IP**, et les taux de throttling de
production (`register` 5/h, `login` 10/min) feraient échouer le deuxième run de la même heure pour une raison
qui n'a rien à voir avec la caisse. `api/config/settings/dev.py` ouvre donc ces quatre taux en dev **uniquement**
(`prod.py` et `base.py` gardent les valeurs réelles ; les 429 restent couverts par `api/tests/test_rate_limit.py`,
qui pose ses propres réglages). Par prudence — une machine partagée reste une machine partagée — les tests qui
ont besoin d'une session se déclarent **saautés avec la raison** si un 429 survient quand même, au lieu de
rendre un « selector not found » muet.
