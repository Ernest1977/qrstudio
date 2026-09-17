/**
 * Test de bout en bout de `/facturation` : le VRAI build, dans Chromium, contre un faux service.
 *
 *   npm run build && node --test scripts/facturation.test.mjs
 *
 * Le service est intercepté dans le navigateur (`setRequestInterception`) plutôt que monkey-patché :
 * c'est ce qui prouve les trois choses qui comptent sur une page de caisse — le **corps** envoyé au
 * backend (jamais un montant), la **navigation** vers l'URL de paiement renvoyée par le serveur, et le
 * fait que le palier n'apparaît qu'**après** la confirmation lue sur `/billing/etat`.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 4323;
const BASE = `http://127.0.0.1:${PORT}`;

const PALIERS = [
  { code: 'free', nom: 'Gratuit', prix_centimes: 0, prix_eur: '0,00', apporte: ['qr_statique', 'export_image'] },
  {
    code: 'standard',
    nom: 'Standard',
    prix_centimes: 299,
    prix_eur: '2,99',
    apporte: ['score_scannabilite', 'export_pdf'],
    limites: { statiques: 500 },
  },
  { code: 'premium', nom: 'Premium', prix_centimes: 899, prix_eur: '8,99', apporte: ['qr_dynamique', 'analytics'], limites: { statiques: 5000 } },
  { code: 'business', nom: 'Entreprise', prix_centimes: 1599, prix_eur: '15,99', apporte: ['api_developpeurs', 'qr_multi_liens'] },
];

let serveur;
let navigateur;

/** Journal des appels reçus par le faux service, pour vérifier corps et ordre. */
let journal;
let etatFaux;

function nouveauFauxService() {
  journal = [];
  etatFaux = {
    connecte: false,
    palier: 'free',
    mobile: null,
    mobileSondages: 0,
    urlPaiement: 'https://checkout.stripe.com/pay/cs_test_1',
    exonere: false, // compte d'administration : la grille ne s'applique pas
    reseau: null, // { devise: 'UGX', affiche: '11960.00 UGX' } si l'agregateur facture une monnaie locale
  };
}

function licence(code) {
  const p = PALIERS.find((x) => x.code === code) || PALIERS[0];
  return { code: p.code, nom: p.nom, prix_centimes: p.prix_centimes, prix_eur: p.prix_eur, caracteristiques: p.apporte, limites: p.limites || {}, paliers: PALIERS };
}

function repondre(reponse, statut, corps) {
  reponse.writeHead(statut, { 'Content-Type': 'application/json', 'X-Test': 'facturation' });
  reponse.end(typeof corps === 'string' ? corps : JSON.stringify(corps));
}

/** Le faux backend, en une fonction : (méthode, chemin, corps) → [statut, objet]. */
function router(methode, chemin, corps, entetes) {
  journal.push({ methode, chemin, corps, jeton: entetes['x-csrftoken'] || '' });
  // Comme DRF : le jeton n'est exige que si une session authentifiee accompagne la requete.
  if (methode === 'POST' && etatFaux.connecte && chemin.startsWith('/api/v1/billing/') && !entetes['x-csrftoken']) {
    return [403, { error: { code: 'permission_denied', message: 'Jeton CSRF manquant.' } }];
  }
  const u = new URL(chemin, BASE);
  const p = u.pathname;

  if (p === '/api/v1/auth/config') return [200, { paliers: PALIERS, registration_open: true }];
  if (p === '/api/v1/auth/me') {
    if (!etatFaux.connecte) return [401, { error: { code: 'unauthenticated', message: 'Authentification requise.' } }];
    return [
      200,
      {
        id: 7,
        email: 'client@exemple.com',
        plan: etatFaux.palier,
        is_email_verified: true,
        exonere_de_facturation: etatFaux.exonere,
      },
    ];
  }
  if (p === '/api/v1/auth/login' && methode === 'POST') {
    if (corps.password === 'mauvais') return [403, { error: { code: 'wrong_password', message: 'Identifiants incorrects.' } }];
    etatFaux.connecte = true;
    return [200, { user: { id: 7, email: 'client@exemple.com' }, csrf_token: 'jeton-csrf-rotate', next: '/dashboard/' }];
  }
  if (p === '/api/v1/auth/register' && methode === 'POST') {
    if (corps.accept_terms !== true) return [400, { error: { code: 'invalid', message: 'Données invalides.', details: { accept_terms: ['Ce champ est obligatoire.'] } } }];
    return [202, { status: 'email_envoye', message: 'Un code vient d’être envoyé.' }];
  }
  if (p === '/api/v1/auth/verify' && methode === 'POST') return [200, { status: 'verifie' }];
  if (p === '/api/v1/auth/verify/resend' && methode === 'POST') return [202, { message: 'Code renvoyé.' }];
  if (p === '/api/v1/billing/etat') {
    if (!etatFaux.connecte) return [401, { error: { code: 'unauthenticated', message: 'Authentification requise.' } }];
    const payant = etatFaux.palier !== 'free';
    return [
      200,
      {
        licence: licence(etatFaux.palier),
        acorde: payant,
        abonnement: payant
          ? { fournisseur: 'stripe', statut: 'active', palier: etatFaux.palier, periode_fin: '2026-10-15T00:00:00Z', en_sursis_jusqu_a: null, annule_le: null, en_registre: true }
          : null,
      },
    ];
  }
  if (p === '/api/v1/billing/checkout' && methode === 'POST') {
    if (corps.palier === etatFaux.palier) return [403, { error: { code: 'palier_deja_actif', message: 'Vous êtes déjà à ce palier.' } }];
    if (corps.palier === 'standard') return [503, { error: { code: 'stripe_price_manquant', message: 'Aucun price Stripe n’est configuré pour le palier `standard`.' } }];
    return [201, { url: etatFaux.urlPaiement, expire_le: '2026-09-15T10:00:00Z' }];
  }
  if (p === '/api/v1/billing/portail' && methode === 'POST') return [200, { url: 'https://connect.stripe.com/setup_page/portail_test' }];
  if (p === '/api/v1/billing/mobile/demande' && methode === 'POST') {
    etatFaux.mobile = { reference: 'mob_test', palier: corps.palier, telephone: corps.telephone, statut: 'pending' };
    return [
      201,
      {
        reference: 'mob_test',
        statut: 'pending',
        palier: corps.palier,
        instruction: 'Ouvrez votre application de paiement mobile et validez la demande mob_test.',
        code_a_utiliser: '481 902',
        expire_le: new Date(Date.now() + 20 * 60 * 1000).toISOString(),
        montant_centimes: PALIERS.find((x) => x.code === corps.palier).prix_centimes,
        devise: 'eur',
        // Ce que l'agregateur reclamera sur le telephone : la devise du reseau, pas celle de la grille.
        ...(etatFaux.reseau ? { montant_affiche: etatFaux.reseau.affiche, devise: etatFaux.reseau.devise } : {}),
      },
    ];
  }
  if (p.startsWith('/api/v1/billing/mobile/') && methode === 'GET') {
    etatFaux.mobileSondages += 1;
    // Confirmé au deuxième sondage : c'est ce retard qui doit rester visible côté client.
    const statut = etatFaux.mobileSondages >= 2 ? 'confirmed' : 'pending';
    if (statut === 'confirmed') etatFaux.palier = etatFaux.mobile.palier;
    return [
      200,
      {
        reference: 'mob_test',
        statut,
        palier: etatFaux.mobile.palier,
        expire_le: new Date(Date.now() + 20 * 60 * 1000).toISOString(),
        confirme_le: statut === 'confirmed' ? new Date().toISOString() : null,
        instruction: 'Ouvrez votre application de paiement mobile et validez la demande mob_test.',
      },
    ];
  }
  return [404, { error: { code: 'inconnu', message: `Route non simulée : ${methode} ${p}` } }];
}

const waitForServer = async (url, tries = 100) => {
  for (let i = 0; i < tries; i += 1) {
    await new Promise((r) => setTimeout(r, 200));
    const ok = await new Promise((resolve) => {
      const req = http.get(url, (res) => {
        res.resume();
        resolve(res.statusCode === 200);
      });
      req.on('error', () => resolve(false));
    });
    if (ok) return;
  }
  throw new Error('serveur de preview injoignable');
};

/** Un service qui répond *et* sert le build : `vite preview` ne gère que le statique. */
function serveurDeTest() {
  return http.createServer((req, res) => {
    if (req.url.startsWith('/api/')) {
      let brut = '';
      req.on('data', (c) => {
        brut += c;
      });
      req.on('end', () => {
        const corps = brut ? JSON.parse(brut) : {};
        const [statut, objet] = router(req.method, req.url, corps, req.headers);
        repondre(res, statut, objet);
      });
      return;
    }
    const relatif = req.url === '/' ? '/index.html' : req.url.split('?')[0];
    const fichier = path.join(root, 'dist', path.normalize(relatif).replace(/^(\.\.[/\\])+/, ''));
    if (fs.existsSync(fichier) && fs.statSync(fichier).isFile()) {
      const type = fichier.endsWith('.js') ? 'text/javascript' : fichier.endsWith('.css') ? 'text/css' : 'text/html';
      res.writeHead(200, { 'Content-Type': type });
      fs.createReadStream(fichier).pipe(res);
      return;
    }
    // SPA fallback : `/facturation` et `/facturation/retour` doivent répondre avec index.html.
    res.writeHead(200, { 'Content-Type': 'text/html' });
    fs.createReadStream(path.join(root, 'dist', 'index.html')).pipe(res);
  });
}

const aller = async (page, chemin) => {
  await page.goto(BASE + chemin, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.qrc-bill, .qrc-app');
};

const texte = (page, sel) => page.$eval(sel, (e) => e.textContent.trim()).catch(() => null);
const existe = (page, sel) => page.$(sel).then((e) => Boolean(e));

test.before(async () => {
  if (!fs.existsSync(path.join(root, 'dist', 'index.html'))) throw new Error('lancez d’abord `npm run build`');
  nouveauFauxService();
  serveur = serveurDeTest();
  await new Promise((r) => serveur.listen(PORT, '127.0.0.1', r));
  await waitForServer(`${BASE}/`);
  const puppeteer = await import('puppeteer');
  navigateur = await puppeteer.default.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
});

test.after(() => {
  if (navigateur) navigateur.close();
  if (serveur) serveur.close();
});

let page;
test.beforeEach(async () => {
  nouveauFauxService();
  page = await navigateur.newPage();
  const erreurs = [];
  page.on('pageerror', (e) => erreurs.push(e.message));
  page.erreurs = erreurs;
  await page.setViewport({ width: 1200, height: 900 });
  // On ne sort pas du bac a sable : les URLs de paiement "externes" sont repondues localement.
  await page.setRequestInterception(true);
  page.on('request', (requete) => {
    const u = requete.url();
    if (u.startsWith('https://checkout.stripe.com/') || u.startsWith('https://connect.stripe.com/')) {
      requete.respond({ status: 200, contentType: 'text/html', body: '<html><body>STRIPE_FAKE</body></html>' });
      return;
    }
    requete.continue();
  });
});
test.afterEach(async () => {
  const bloquees = page.erreurs.filter((m) => !/favicon/.test(m));
  await page.close();
  assert.deepEqual(bloquees, [], `erreurs JavaScript dans la page : ${bloquees.join(' | ')}`);
});

test('la grille de prix vient du service, et un anonyme ne peut pas souscrire', async () => {
  await aller(page, '/facturation');
  const cartes = await page.$$eval('.qrc-plan', (els) => els.map((e) => e.textContent));
  assert.equal(cartes.length, 3, 'Standard, Premium, Entreprise — le Gratuit n’est pas à vendre');
  assert.match(cartes.join(' '), /2,99/);
  assert.match(cartes.join(' '), /15,99/);
  assert.match(cartes[1], /QR dynamiques, modifiables après impression/, 'un code de caractéristique doit être traduit');
  assert.ok(await existe(page, '.qrc-authbox'), 'sans session, l’encadré de connexion est la seule porte');
  const desactives = await page.$$eval('.qrc-plan button', (els) => els.map((e) => e.disabled));
  assert.equal(desactives.length, 6, 'deux boutons par palier');
  assert.ok(desactives.every(Boolean), 'rien ne doit être cliquable sans session : pas de 401 à la clic');
  const indices = await page.$$eval('.qrc-plan .qrc-hint', (els) => els.map((e) => e.textContent));
  assert.ok(indices.filter((t) => /Connexion requise/.test(t)).length >= 3, 'et la raison est dite sur chaque carte');
});

test('la connexion débloque les boutons, et le jeton CSRF vient de la réponse de login', async () => {
  await aller(page, '/facturation');
  await page.type('#qrc-auth-email', 'client@exemple.com');
  await page.type('#qrc-auth-password', 'un-mot-de-passe-solide');
  await page.click('.qrc-authbox button[type="submit"]');
  await page.waitForSelector('.qrc-bill-resume');
  const resume = await texte(page, '.qrc-bill-resume h2');
  assert.equal(resume.replace(/\s+/g, ' '), 'Gratuit 0,00 €/mois');
  const login = journal.find((j) => j.chemin === '/api/v1/auth/login');
  assert.deepEqual(login.corps, { email: 'client@exemple.com', password: 'un-mot-de-passe-solide' });
  const apres = journal.filter((j) => j.methode === 'POST' && j.chemin.startsWith('/api/v1/billing'));
  assert.deepEqual(apres, [], 'aucune écriture avant que l’on clique');
});

test('un clic sur « S’abonner » envoie le palier, jamais un montant, et part chez Stripe', async () => {
  await aller(page, '/facturation');
  await page.type('#qrc-auth-email', 'client@exemple.com');
  await page.type('#qrc-auth-password', 'un-mot-de-passe-solide');
  await page.click('.qrc-authbox button[type="submit"]');
  await page.waitForSelector('.qrc-plans button:not([disabled])');

  // Premium est la 2e carte ; Standard est simulé en 503 pour vérifier l'autre branche plus bas.
  await page.$$eval('.qrc-plan', (els) => els[1].querySelector('button').click());
  await page.waitForFunction(() => window.location.href.includes('checkout.stripe.com'), { timeout: 8000 });
  const checkout = journal.find((j) => j.chemin === '/api/v1/billing/checkout');
  assert.ok(checkout, 'le POST de checkout doit partir');
  assert.equal(checkout.jeton, 'jeton-csrf-rotate', 'le jeton doit etre celui rendu par login, pas le vieux cookie');
  assert.deepEqual(checkout.corps, { palier: 'premium' }, 'le montant n’est PAS négociable depuis le client');
  assert.match(page.url(), /^https:\/\/checkout\.stripe\.com\/pay\/cs_test_1$/, 'on doit atterrir sur l’URL renvoyée par le serveur');
  assert.equal(await texte(page, 'body'), 'STRIPE_FAKE');
});

test('un service non configuré est dit comme tel, sans faire croire à un succès', async () => {
  await aller(page, '/facturation');
  await page.type('#qrc-auth-email', 'client@exemple.com');
  await page.type('#qrc-auth-password', 'un-mot-de-passe-solide');
  await page.click('.qrc-authbox button[type="submit"]');
  await page.waitForSelector('.qrc-plans button:not([disabled])');
  await page.$$eval('.qrc-plan', (els) => els[0].querySelector('button').click()); // Standard -> 503
  await page.waitForSelector('.qrc-notice.is-warn');
  const message = await texte(page, '.qrc-notice.is-warn');
  assert.match(message, /Aucun price Stripe n’est configuré/);
  assert.match(message, /réglage d’exploitation manquant/);
  assert.equal(etatFaux.palier, 'free', 'un 503 ne doit surtout pas avoir accordé quoi que ce soit');
});

test('le paiement mobile : refus local avant réseau, code affiché, confirmation relue depuis /etat', async () => {
  await aller(page, '/facturation');
  await page.type('#qrc-auth-email', 'client@exemple.com');
  await page.type('#qrc-auth-password', 'un-mot-de-passe-solide');
  await page.click('.qrc-authbox button[type="submit"]');
  await page.waitForSelector('.qrc-plans button:not([disabled])');

  await page.$$eval('.qrc-plan', (els) => [...els[0].querySelectorAll('button')][1].click()); // « Payer par mobile »
  await page.waitForSelector('#qrc-mobile-tel');
  await page.type('#qrc-mobile-tel', 'abc');
  await page.click('.qrc-mobile-form button[type="submit"]');
  await page.waitForSelector('.qrc-mobile-form .qrc-notice.is-warn');
  assert.match(await texte(page, '.qrc-mobile-form .qrc-notice.is-warn'), /format international/);
  assert.equal(journal.filter((j) => j.chemin === '/api/v1/billing/mobile/demande').length, 0, 'un numéro invalide ne part pas au réseau');

  await page.click('#qrc-mobile-tel');
  await page.keyboard.down('Control');
  await page.keyboard.press('KeyA');
  await page.keyboard.up('Control');
  await page.keyboard.press('Backspace');
  await page.type('#qrc-mobile-tel', '+393331234567');
  await page.click('.qrc-mobile-form button[type="submit"]');
  await page.waitForSelector('.qrc-mobile');
  const demande = journal.find((j) => j.chemin === '/api/v1/billing/mobile/demande');
  assert.deepEqual(demande.corps, { palier: 'standard', telephone: '+393331234567' });
  assert.equal(demande.jeton, 'jeton-csrf-rotate');
  assert.equal(await texte(page, '.qrc-code'), '481 902');
  assert.match(await texte(page, '.qrc-mobile .qrc-muted'), /validez la demande mob_test/);
  assert.match(await texte(page, '.qrc-mobile-montant'), /2,99\s€|2,99 €/);
  assert.equal(etatFaux.palier, 'free', 'tant que l’agrégateur n’a pas confirmé, le palier ne bouge pas');
  await page.waitForSelector('.qrc-notice.is-ok', { timeout: 15000 });
  await page.waitForFunction(() => document.querySelector('.qrc-bill-resume h2')?.textContent.includes('Standard'), { timeout: 15000 });
  assert.ok(etatFaux.mobileSondages >= 2, 'le suivi doit re-sonder, pas demander à l’utilisateur de recharger');
  assert.match((await texte(page, '.qrc-notice.is-ok')) || '', /confirmé/i);
  assert.equal(await page.evaluate(() => window.sessionStorage.getItem('qrc.facturation.mobile')), null, 'un paiement fini ne doit pas être rejoué au retour');
});

test('un compte d’administration ne voit aucune caisse, mais voit où créer', async () => {
  // L'exemption est decidee par le serveur (`/auth/me`), le front ne fait que la dire : ce test verifie
  // qu'il ne reste aucun bouton « S'abonner » cliquable sur une session exoneree, et que la page renvoie
  // bien vers l'espace admin — sinon un super admin qui cherche ou creer paierait pour tester son produit.
  etatFaux.exonere = true;
  etatFaux.connecte = true;
  await aller(page, '/facturation');
  await page.waitForSelector('.qrc-bill-resume');
  const avis = await texte(page, '.qrc-bill-exemption');
  assert.match(avis, /Session d’administration — facturation non applicable/, `avis recu : ${avis}`);
  const boutons = await page.$$eval('.qrc-plan button', (els) => els.map((e) => e.textContent.trim()));
  assert.deepEqual(boutons, [], 'aucune porte de paiement sur un compte exonéré');
  const cartes = await page.$$eval('.qrc-plan .qrc-hint', (els) => els.map((e) => e.textContent));
  assert.ok(cartes.filter((t) => /Compte d’administration/.test(t)).length === 3, "c’est dit sur chaque palier");
  const href = await page.$eval('.qrc-bill-exemption a', (e) => e.getAttribute('href'));
  assert.equal(href, '/manage/qr/creer/');
  const ecrits = journal.filter((j) => j.methode === 'POST' && j.chemin.startsWith('/api/v1/billing'));
  assert.deepEqual(ecrits, [], 'exonéré ne veut pas dire « on clique quand même »');
});

test('le mobile affiche le montant du réseau, et la grille reste lisible à côté', async () => {
  // Un client ougandais ne doit jamais voir « 2,99 € » comme somme a valider sur son telephone : c'est
  // 11 960 UGX qu'on lui demande. Le front affiche ce que le serveur annonce, sans jamais recalculer.
  etatFaux.reseau = { devise: 'UGX', affiche: '11960.00 UGX' };
  await aller(page, '/facturation');
  // On passe par le formulaire : `etatFaux.connecte = true` ne donnerait au front aucun jeton CSRF, et le
  // POST serait rejeté par le faux service pour la mauvaise raison.
  await page.type('#qrc-auth-email', 'client@exemple.com');
  await page.type('#qrc-auth-password', 'un-mot-de-passe-solide');
  await page.click('.qrc-authbox button[type="submit"]');
  await page.waitForSelector('.qrc-plans button:not([disabled])');
  await page.$$eval('.qrc-plan', (els) => [...els[0].querySelectorAll('button')][1].click()); // Standard
  await page.waitForSelector('#qrc-mobile-tel');
  await page.type('#qrc-mobile-tel', '+256700000000');
  await page.click('.qrc-mobile-form button[type="submit"]');
  await page.waitForSelector('.qrc-mobile-montant', { timeout: 10000 });
  assert.equal(await texte(page, '.qrc-mobile-montant'), '11960.00 UGX');
  assert.match(await texte(page, '.qrc-mobile .qrc-hint'), /Grille : 2,99/);
  const demande = journal.find((j) => j.chemin === '/api/v1/billing/mobile/demande');
  assert.deepEqual(demande.corps, { palier: 'standard', telephone: '+256700000000' }, 'le front narrive aucun montant');
});

test('le retour d’un paiement annulé est dit, puis nettoyé de l’URL', async () => {
  await aller(page, '/facturation?annule=1');
  assert.match((await texte(page, '.qrc-notice')) || '', /Paiement interrompu/);
  assert.equal(new URL(page.url()).search, '', 'sinon « précédent » rejouerait l’annulation');
});

test('inscription : le code de vérification a une fenêtre, et le 400 de champ manquant est lisible', async () => {
  await aller(page, '/facturation');
  await page.$$eval('.qrc-authbox button', (els) => els.find((b) => /Créer un compte/.test(b.textContent)).click());
  await page.type('#qrc-auth-email', 'nouveau@exemple.com');
  await page.type('#qrc-auth-password', 'un-mot-de-passe-solide');
  await page.click('.qrc-authbox button[type="submit"]'); // `accept_terms` est vide : le service doit refuser
  await page.waitForSelector('.qrc-authbox .qrc-notice.is-err');
  assert.match(await texte(page, '.qrc-authbox .qrc-notice.is-err'), /obligatoire/);

  await page.$$eval('.qrc-check input', (els) => els.forEach((e) => e.click()));
  await page.click('.qrc-authbox button[type="submit"]');
  await page.waitForSelector('#qrc-auth-code');
  assert.ok(await existe(page, 'button[class*="qrc-btn"]'), 'le formulaire de confirmation est présent');
  await page.type('#qrc-auth-code', '12a34');
  assert.equal(await page.$eval('#qrc-auth-code', (e) => e.value), '1234', 'le champ filtre tout ce qui n’est pas chiffré');
  await page.type('#qrc-auth-code', '56');
  assert.equal(await page.$eval('#qrc-auth-code', (e) => e.value), '123456');
});
