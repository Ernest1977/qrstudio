/**
 * `/facturation` contre le VRAI service Django — pas de bouchon.
 *
 *   cd api && make run            (ou: runserver sur 8000)
 *   npm run dev                   (Vite sur 5173, proxifie /api)
 *   node --test scripts/facturation.api.test.mjs
 *
 * Ignoré proprement si l'un des deux serveurs manque (le saut est déclaré dans le corps : une
 * option `{ skip: () => … }` est une valeur véridique pour `node:test`, elle sauterait tout en silence).
 *
 * Ce test n'existe pas pour re-vérifier l'affichage (`facturation.test.mjs` le fait avec un faux
 * service, de façon déterministe). Il vérifie une seule chose qu'aucun bouchon ne peut prouver :
 * **qu'un POST authentifié passe la machine CSRF de Django depuis l'origine du front**. Le proxy Vite
 * réécrit le `Host` (`changeOrigin: true`), donc l'`Origin` du navigateur ne correspond plus à
 * `get_current_host()` ; sans `CSRF_TRUSTED_ORIGINS` côté `config/settings/dev.py`, la réponse serait
 * un 403 « CSRF Failed » et rien, dans le front, ne le distinguerait d'un refus métier.
 */

import test from 'node:test';
import assert from 'node:assert/strict';

const FRONT = process.env.QRC_FRONT ?? 'http://127.0.0.1:5173';

const vivant = async (url) =>
  new Promise((resolve) => {
    const req = fetch(url, { signal: AbortSignal.timeout(1500) })
      .then((r) => resolve(r.status < 500))
      .catch(() => resolve(false));
    void req;
  });

let navigateur;
let page;
let ignore = false;
// Passe a `true` quand le throttle de la dev empeche de creer une session : les tests qui en ont besoin
// se declarent sautes avec la raison, au lieu d'echouer sur un selector absent.
let sessionIndisponible = false;
const erreurs = [];

test.before(async () => {
  // On sonde l'API *a travers* le proxy du front, pas le front seul : Vite repond 200 sur `/` meme
  // quand sa cible est morte (le proxy renvoie une 500), et un test qui part sur cette hypothese echoue
  // pour la mauvaise raison — « le front est casse » alors que c'est le backend qui n'est pas lance.
  if (!(await vivant(`${FRONT}/api/v1/auth/config`))) {
    ignore = true;
    return;
  }
  const puppeteer = await import('puppeteer');
  try {
    navigateur = await puppeteer.default.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  } catch {
    ignore = true; // Chromium ou ses bibliothèques système absents : on ne rend pas `npm test` rouge là-dessus
    return;
  }
  page = await navigateur.newPage();
  page.on('pageerror', (e) => erreurs.push(e.message));
  await page.setViewport({ width: 1200, height: 900 });
});

test.after(async () => {
  if (navigateur) await navigateur.close();
});

const texte = (sel) => page.$eval(sel, (e) => e.textContent.trim()).catch(() => null);

/**
 * Remplit un champ au lieu d'y ajouter du texte.
 *
 * `page.type()` write a la suite du contenu existant : rejouer l'adresse dans un champ deja rempli
 * produit une chaine invalide, et le 400 de validation qui suit ressemble a s'y méprendre a un bug
 * d'authentification. Select-all puis frapper est ce que fait un humain qui corrige une saisie.
 */
const saisir = async (sel, valeur) => {
  await page.click(sel);
  await page.keyboard.down('Control');
  await page.keyboard.press('KeyA');
  await page.keyboard.up('Control');
  await page.keyboard.press('Backspace');
  await page.type(sel, valeur);
};
/**
 * Ouvre une page du front et attend que l'etat du compte soit reellement la.
 *
 * `domcontentloaded` ne dit rien de la grille : elle arrive d'un `GET /auth/config` puis d'un
 * `GET /billing/etat`. Les tests qui suivent cherchent des boutons — les interroger trop tot renvoyait
 * `null` et se lisait comme « la session a saute », alors que la page n'avait simplement pas fini (mesure :
 * l'echec n'apparait que quand les deux fichiers de test tournent en parallele, `node --test` lancait deux
 * Chromium en meme temps). On attend donc la grille, ou l'avis d'erreur qui la remplace.
 */
const aller = async (chemin) => {
  await page.goto(FRONT + chemin, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.qrc-bill');
  await page.waitForFunction(
    () => document.querySelector('.qrc-plan') || document.querySelector('.qrc-notice.is-err, .qrc-notice.is-warn'),
    { timeout: 15000 },
  );
};

test('la grille affichée est celle du serveur, prix réels compris', async (t) => {
  if (ignore) {
    t.skip('backend (Vite + API) ou Chromium indisponible');
    return;
  }
  await aller('/facturation');
  const cartes = await page.$$eval('.qrc-plan', (els) => els.map((e) => e.textContent.replace(/\s+/g, ' ')));
  assert.equal(cartes.length, 3);
  assert.match(cartes.join(' '), /2,99/);
  assert.match(cartes.join(' '), /8,99/);
  assert.match(cartes.join(' '), /15,99/);
  assert.match(cartes.join(' '), /QR dynamiques/);
  assert.doesNotMatch(cartes.join(' '), /NaN|undefined/, 'aucun libellé ne doit tomber en clair');
});

test('inscrire, ouvrir une session, puis écrire : la machine CSRF du backend est satisfaite', async (t) => {
  if (ignore) {
    t.skip('backend (Vite + API) ou Chromium indisponible');
    return;
  }
  const email = `apercu+${Date.now()}@exemple.com`;
  await aller('/facturation');
  await page.$$eval('.qrc-authbox button', (els) => els.find((b) => /Créer un compte/.test(b.textContent)).click());
  await saisir('#qrc-auth-email', email);
  await saisir('#qrc-auth-password', 'Apercu2026!Valide');
  await page.$$eval('.qrc-check input', (els) => els.forEach((e) => e.click()));
  await page.click('.qrc-authbox button[type="submit"]');
  // Le throttle d'inscription de la dev est par IP et par heure, et son compteur vit dans le cache
  // *processus* du serveur (LocMem) : relancer deux fois `npm test` dans la meme heure suffit a le
  // declencher. Ce n'est pas une regression du front, donc on le dit et on saute, au lieu de laisser un
  // timeout « selector not found » comme seul indice.
  await page.waitForSelector('#qrc-auth-code, .qrc-notice.is-err, .qrc-notice.is-warn', { timeout: 10000 });
  const avisInscription = await texte('.qrc-notice.is-err, .qrc-notice.is-warn');
  if (avisInscription && /ralent/i.test(avisInscription) && !(await page.$('#qrc-auth-code'))) {
    sessionIndisponible = true;
    t.skip(
      `inscription bridée par le throttle de la dev (${avisInscription.trim()}) — relancer le runserver pour le remettre a zero`,
    );
    return;
  }
  assert.ok(
    !(avisInscription && /ralent/i.test(avisInscription)),
    `un 429 sans champ de code doit etre reconnu comme tel, avis recu : ${avisInscription}`,
  );

  // Mot de passe connu : on le rejoue dans le formulaire de connexion. Pas de vérification e-mail ici,
  // le backend autorise la session sans elle (l'e-mail ne sert qu'à activer les envois).
  await page.$$eval('.qrc-authbox button', (els) => els.find((b) => /J’ai déjà un compte|Se connecter/.test(b.textContent))?.click());
  await page.waitForSelector('#qrc-auth-password', { timeout: 5000 });
  await saisir('#qrc-auth-email', email);
  await saisir('#qrc-auth-password', 'Apercu2026!Valide');
  await page.click('.qrc-authbox button[type="submit"]');
  await page.waitForSelector('.qrc-bill-resume', { timeout: 8000 });
  assert.match((await texte('.qrc-bill-resume h2')) || '', /Gratuit/);

  await page.$$eval('.qrc-plan', (els) => els[1].querySelector('button').click()); // Premium
  // On attend un avis d'erreur precis: `.qrc-notice` existe deja (celui de la connexion) et un
  // `waitForSelector` dessus passerait immediatement, sans jamais verifier le clic.
  await page.waitForSelector('.qrc-notice.is-warn, .qrc-notice.is-err', { timeout: 8000 });
  const avis = await texte('.qrc-notice.is-warn, .qrc-notice.is-err');
  assert.doesNotMatch(avis, /CSRF/i, 'un rejet CSRF n’est pas un message métier : il signalerait un réglage d’origine manquant');
  if (/ralent/i.test(avis || '')) {
    // Le throttle de la dev est aussi pose sur `POST /billing/checkout` (et sur `/auth/config`, `/billing/etat`) :
    // a la deuxieme execution dans la meme heure, c'est 429 qui repond avant meme le reglage Stripe. Ce qui
    // etait verifie ici — la machine CSRF — l'a deja ete par les trois POSTs precedents, qui ont passe.
    t.skip(`ecriture bridée par le throttle de la dev (${avis.trim()}) — relancer le runserver pour le remettre a zero`);
    return;
  }
  assert.match(avis, /price Stripe|STRIPE_PRICE_PREMIUM|non configuré/i, `message attendu, reçu : ${avis}`);

  assert.deepEqual(erreurs.filter((m) => !/favicon/.test(m)), [], 'aucune exception JavaScript sur le chemin');
  const etat = await page.evaluate(async () => (await fetch('/api/v1/billing/etat', { credentials: 'same-origin' })).json());
  assert.equal(etat.acorde, false, 'un clic qui échoue n’a rien accordé');
  assert.equal(etat.abonnement, null);
});

test('l’espace admin n’est pas une porte du front, et /auth/me dit l’exemption', async (t) => {
  if (ignore) {
    t.skip('backend (Vite + API) ou Chromium indisponible');
    return;
  }
  // Deux choses qu'aucun bouchon ne peut prouver : la page de creation reservee au personnel est bien
  // protegee derriere le proxy du front (302 vers la connexion, jamais le formulaire), et un compte client
  // normal ne se dit pas exonere — sinon « gratuit » deviendrait l'etat par defaut du front.
  await aller('/facturation');
  const moi = await page.evaluate(async () => {
    const r = await fetch('/api/v1/auth/me', { credentials: 'same-origin' });
    return { statut: r.status, corps: await r.json() };
  });
  if (moi.statut === 200) {
    // Session etablie (le test precedent s'est connecte) : le drapeau doit etre la, et a `false` pour un
    // client normal. Sans lui, le front devrait deviner qu'il fait face a un compte d'administration.
    assert.ok('exonere_de_facturation' in moi.corps, '`/auth/me` doit publier le drapeau, le front ne le devine pas');
    assert.equal(moi.corps.exonere_de_facturation, false, `un compte client ne doit pas etre exonere : ${JSON.stringify(moi.corps)}`);
  } else {
    // Pas de session — typiquement quand le throttle d'inscription de la dev (1/h et par IP) a deja ete
    // consomme par une run precedente : on verifie alors le contrat anonymes, sans inventer de session.
    assert.equal(moi.statut, 401, `reponse inattendue sur /auth/me : ${JSON.stringify(moi)}`);
    assert.equal(moi.corps.error.code, 'unauthenticated');
  }

  // Deux etats, deux reponses attendues, et c'est la distinction qui compte : un anonyme est envoye a la
  // connexion (302 suivi), un compte client deja connecte recoit un 403. Renvoyer ce dernier vers la page
  // de connexion serait une boucle — allauth ejecte un deja-connecte vers sa destination, et `npm test`
  // l'a mesure en `ERR_TOO_MANY_REDIRECTS` avant la correction `acces_admin`.
  const porte = await page.evaluate(async () => {
    const r = await fetch('/manage/qr/creer/', { credentials: 'same-origin' });
    return { statut: r.status, corps: await r.text() };
  });
  if (moi.statut === 200) {
    assert.equal(porte.statut, 403, `session client sur la page admin : 403 attendu, recu ${porte.statut}`);
  } else {
    const avant = page.url();
    await page.goto(FRONT + '/manage/qr/creer/', { waitUntil: 'domcontentloaded' });
    assert.match(page.url(), /\/accounts\/login\//, 'sans session, la creation renvoie a la connexion');
    await page.goto(avant, { waitUntil: 'domcontentloaded' });
  }
  assert.doesNotMatch(porte.corps, /name="payload"/, 'aucun champ de creation ne doit fuiter devant un non-personnel');
});

test('le paiement mobile renvoie le refus du service, pas un faux succès', async (t) => {
  if (ignore) {
    t.skip('backend (Vite + API) ou Chromium indisponible');
    return;
  }
  // Même session que le test précédent n’est pas garantie (ordre des tests) : on repart du formulaire.
  if (sessionIndisponible) {
    t.skip('aucune session disponible : le suivi mobile ne peut pas etre exerce sans compte');
    return;
  }
  await aller('/facturation');
  const connecte = await page.$('.qrc-plans button:not([disabled])');
  assert.ok(connecte, 'la session doit tenir d’un test à l’autre (mêmes cookies, même origine)');
  await page.$$eval('.qrc-plan', (els) => [...els[0].querySelectorAll('button')][1].click());
  await page.waitForSelector('#qrc-mobile-tel');
  await saisir('#qrc-mobile-tel', '+3933312345678');
  await page.click('.qrc-mobile-form button[type="submit"]');
  await page.waitForSelector('.qrc-notice.is-warn, .qrc-notice.is-err', { timeout: 8000 });
  const avis = await texte('.qrc-notice.is-warn, .qrc-notice.is-err');
  assert.match(avis, /mobile/i, `message attendu, reçu : ${avis}`);
  assert.equal(await page.$('.qrc-mobile'), null, 'aucun suivi inventé : rien n’a été demandé à un agrégateur');
});
