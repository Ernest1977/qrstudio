# Analyse du fichier `qr-coding (1).html`

Fichier source : `uploads/qr-coding (1).html` — 470 lignes, ~175 Ko, un seul HTML autonome
(React + Babel + Tailwind via CDN dans l'original réel, puis réécriture en Vanilla ; le fichier
fourni est en Vanilla + `qrcode-generator@1.4.4` en CDN).

Le rendu est **très bien pensé** (22 types, personnalisation complète, aperçu live). Mais il
contient **un bug bloquant** et **une vingtaine de bugs réels**. Tout est détaillé ci-dessous
avec la preuve, puis la correction apportée dans `qr-coding-react/`.

---

## 1. Le bloquant : les QR code générés étaient illisibles par la plupart des lecteurs

**Preuve.** L'anneau des trois repères (finders) était dessiné en *contour* :

```html
<!-- ligne 332 de l'original -->
<rect x="${x}" y="${y}" width="${S}" height="${S}" rx="${rx}" fill="none"
      stroke="${color}" stroke-width="${cell}"/>
```

Un trait de `stroke-width = 1 cellule` centré sur le contour du carré 7×7 déborde de **½ cellule
de chaque côté** : l'anneau 1-module du motif `1:1:3:1:1` (silencieux clair de 1 module autour du
centre 3×3, puis anneau sombre de 1 module, puis 1 module clair extérieur) est **détruit** — les
seuils de contraste du décodeur tombent au milieu des traits et le module de timing adjacent est
parasité. Conséquence mesurée : le code affiché à l'écran ne se décodait **pas**, quel que soit le
niveau de zoom ou de densité.

**Correction** (`src/lib/qrcode.js`) : plus aucun `stroke`, trois rectangles pleins empilés
(7×7 couleur repère → 5×7 → 5×5 fond → 3×3 centre), ce qui restitue exactement la géométrie
normalisée. Les tests `scripts/lib.test.mjs` verrouillent les deux sens : la version *stroke* est
indécodable, la version *aires pleines* se décode réellement (décodage ZXing sur rendu rasterisé).

Vérifié de bout en bout : capture d'écran du vrai navigateur → décodage → URL retrouvée
(`scripts/browser.test.mjs`).

## 2. Logo : le seuil de tolérance était dépassé sans avertissement

Un QR reste lisible avec un obstacle central **si** la correction d'erreurs absorbe les modules
perdus. Mesures (ecc Q : ≤ 20 % de la trame masquée ; ecc H : ≤ 25 % ; au-delà : 0 % de réussite,
**augmenter la résolution n'y change rien** — c'est la redondance qui borne, pas les pixels).
L'original laissait monter le curseur jusqu'à **35 %** (`<input type="range" id="logoSize"
min="12" max="35">`, l. 150) sans jamais relever `ecc`.

**Correction** : la présence d'un logo relève automatiquement `ecc` en `H`, réduit le logo au
besoin (plafond 25 %) et l'explique dans les avertissements.

## 3. Contenu encodé : 6 bugs de format (tous reproduits, comparés à l'original)

| # | Bug d'origine | Ce que ça faisait | Correction |
|---|---|---|---|
| 1 | `ADR` sans échappement (l. 308) | `5 via Roma; scala B` : le `;` casse le champ → **Paris glissait en « région »**, la ville disparaissait | `escapeVCard` sur tous les champs (`\` `;` `,` `:` saut de ligne) |
| 2 | `FN`/`TITLE`/`ORG` non échappés | `TITLE:Dir, Commerciale` invalide, cartes lues à moitié | idem |
| 3 | Lignes vCard non repliées | > 75 octets = illégal, beaucoup d'apps tronquent | `foldLines()` (repli UTF-8 sûr) + fins de ligne **CRLF** (RFC 6350) |
| 4 | Wi-Fi : `S:`/`P:` non échappés, `;` et `:` compris (l. ~300) | `WIFI:T:nopass;S:Café; Net:5G;P:p"a,s\s;;` → SSID faux, **mot de passe accepté alors que le réseau est ouvert**, guillemet cassé | `escapeWifi` (`;` `,` `:` `\` `"` dans `S:`/`P:` ; `:` seulement dans `S:`), et `T:nopass` ⇒ pas de `P:` |
| 5 | iCal : `DTSTART:20260601T0900` (l. 96 `icsDT`) | **format illégal** (RFC 5545 exige `HHMMSS` + `Z`) → l'événement est ignoré ou l'import échoue selon l'appli | `toICalUtc()` + secondes + `Z`, et ajout de `PRODID`/`UID`/`DTSTAMP`/`CALSCALE` |
| 6 | `tel:`/`smsto:` : `.replace(/\s/g,'')` seulement | `smsto:00336-12-34-56-78` : les tirets restent → numérotation impossible | normalisation E.164 (`\D` retiré, `00`→`+`) dans `tel`/`sms`/`whatsapp` |

Les 6 sont vérifiés côte à côte dans le projet : `node .tmp/vcmp.mjs` exécute le `buildPayload` de
l'original dans un bac à sable et l'affiche en regard de la version corrigée.

## 4. Robustesse

- **Capacité** : `MAX_EMBED = 1900` « octets max intégrables en base64 » est faux dans les deux
  sens — le base64 coûte 4/3, et la vraie limite d'un QR (version 40) est **2953 octets en L,
  2331 en M, 1663 en Q, 1273 en H** — mesure faite avec la lib : `n` octets passent,
  `n+1` sont refusés. Remplacé par `byteLength()` (UTF-8 compté à l'octet près) et
  un message qui cite la limite de *la version courante* + le niveau de correction choisi, avec
  proposition de descendre l'ecc.
- **Fichiers importés** : `file.mime` était déclaré (l. 210-213) **jamais lu** ; `accept=".pdf"` ne
  filtre que la boîte de dialogue. Un PNG renommé `facture.pdf` passait et le QR envoyait
  l'utilisateur vers un fichier illisible. Ajout de `src/lib/files.js` : lecture des 16 premiers
  octets (`%PDF-`, `PK`, OLE2, PNG/JPEG/GIF) et refus motivé.
- **`btoa(unescape(encodeURIComponent(svg)))`** (l. 384/442/456) : `unescape` est proscrit et
  l'aller-retour sur un SVG déjà encodé peut corrompre les accents ; remplacé par une data URL
  `encodeURIComponent` (sans base64, donc sans `btoa`).
- **Injection HTML dans l'aperçu** : `fieldsEl.innerHTML = html` avec `${f.def}` et les valeurs
  recrachées dans les attributs → un `"` dans un champ casse le `value=` ; le SVG reconstruit en
  chaîne n'était pas échappé. Passé par React (plus d'`innerHTML`) + `escapeXml` pour le texte du
  SVG (légende, labels de couleur).
- **Légende sous le QR** : ajoutée sans agrandir le `viewBox` → elle **débordait** du canvas et
  rognait le QR au export PNG. Le calcul de hauteur réserve maintenant la place (`height > size`).
- **Aucune garde CDN** : si le `<script src="…/qrcode-generator">` échouait, l'outil affichait un
  cadre vide sans message. Dépendance npm `qrcode` bundlée, plus de CDN.
- **`state.data = {}` au changement de type** (l. 258) : toute la saisie était **perdue** en allant
  de « Carte de visite » à « Wi-Fi » et au retour. Conservée par type (`initialDataByType`).
- **`state.data[el.dataset.k] = el.value` au rendu** : le DOM était la source de vérité → les
  valeurs par défaut écrasaient l'état à chaque frappe. Devenu un état React contrôlé.
- **Champs contrôlés + `.trim()` à la frappe** : impossible de taper un espace (« 5 via Roma »
  devenait `5viaRoma`) dans la version intermédiaire ; le tondage ne se fait plus qu'à la
  **construction du payload**.
- **Message `undefined`** quand le QR dépasse la capacité : l'original affichait
  `⚠ Fichier non conforme : undefined`.
- **Avertissement avalé** : dans la version React intermédiaire, si le QR n'était pas générable,
  la notice « fichier refusé » était écrasée et l'utilisateur ne voyait rien. Les `notice` du
  formulaire passent maintenant en premier et restent visibles.

## 5. UX / accessibilité

- **Réseaux sociaux** : les libellés affichaient le placeholder (`votre.pseudo`) à la place de
  « Nom d'utilisateur », car la fabrique `social()` recevait les arguments dans le mauvais ordre.
- **Préfixes** : `@`/`/in/` collés par l'utilisateur → liens `https://x.com/@user` et profils
  introuvables. Ajout de `cleanHandle()` / `cleanLinkedIn()` et de `absolutize()` (un
  `exemple.com` saisi sans schéma devient `https://exemple.com`, sinon le QR ouvre une page
  blanche).
- **Repères (finders) ignorés** : `finderStyle`/`finderColor` n'étaient pas appliqués à l'anneau
  extérieur → incohérence entre l'aperçu et le PNG exporté. Aligné sur la même géométrie.
- **Zone de quiétude** : `margin` par défaut **24 px** (l. 222, ≈ 2,4 modules) **sous le minimum
  ISO 18004 (4 modules)** ; le champ était borné à `Math.max(8, …)` (l. 394), donc 8 px étaient
  autorisés — un QR sans zone claire se décode mal, voire pas du tout. Défaut porté à 40 px (4 modules) + indication
  sous le champ.
- **Aperçu ≠ export** : deux tailles de module différentes (10 px à l'écran, 8 px à l'export)
  faisaient diverger la réduction automatique du logo — on téléchargeait un QR légèrement
  différent de celui qu'on avait validé à l'écran. Un seul grain de rendu, la résolution du PNG se
  règle par `scale` (×1…×8).
- **Accessibilité** : les types étaient des `<div onclick>` inaccessibles au clavier. Rendus
  `<button role="tab">` avec navigation aux flèches, `aria-pressed` sur les chips, `aria-live` sur
  les avertissements, labels reliés aux champs, focus visible. Le panneau « Contenu encodé » est
  un `<details>` (donc atteignable au clavier) et les boutons d'export ont des `id` stables.
- **Thème sombre** : la bascule écrivait la classe sur `<body>` après le premier paint (flash
  clair au chargement) et l'état n'était pas lu avant le rendu. Corrigé + persistance `localStorage`.
- **`scroll-margin-top`** : les liens d'ancrage (`#studio`, `#features`) arrivaient collés sous le
  header sticky, titre de section masqué.
- **Bouton « Essayer avec l'exemple : logo Kamco Farm »** supprimé, comme demandé, avec le logo
  base64 de 170 Ko qu'il embarquait (~200 Ko économisés sur le fichier).

## 6. Ce qui a été gardé à l'identique (périmètre « fidèle »)

Les 22 types et leurs champs, les libellés, les valeurs par défaut, les couleurs du thème, la
mise en page (2 colonnes, chips de style, jauge de capacité), les 4 sections marketing
(Hero / Types / Comment ça marche / Features), l'export PNG ×4 et SVG, la copie du contenu, la
génération 100 % locale (aucune requête réseau sortante : c'est le vrai point fort de l'outil).

## 7. Ce qui est testé

- `npm run test:lib` — **27 tests** : géométrie des finders, décodage réel (sharp + ZXing) des
  rendus, payloads des 22 types, échappements vCard/Wi-Fi, iCal valide, `foldLines`, repli
  `ecc`/logo, signatures de fichiers, data URL, persistance du thème, sérialisation/désérialisation.
- `npm run test:browser` — **12 tests** dans Chromium headless sur le `dist/` réel : pas d'erreur
  console, QR décodable depuis une **capture d'écran**, changement de type sans perte de saisie,
  vCard avec adresse, Wi-Fi échappé, styles ronds + logo toujours décodables, dépassement de
  capacité, refus d'un PNG signé comme PDF, thème persistant après rechargement, clavier, et
  **exports PNG et SVG déclenchés depuis l'UI puis re-décodés**.
