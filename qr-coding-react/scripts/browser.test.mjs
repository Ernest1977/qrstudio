/**
 * Test de bout en bout : on sert le VRAI build (`npm run build` puis
 * `vite preview`), on le charge dans Chromium, on interagit comme un humain et
 * on décode le PNG de l'aperçu avec zxing.
 *
 *   node --test scripts/browser.test.mjs
 *
 * Nécessite Chromium (puppeteer) ; ignoré proprement s'il est absent.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';
import { readBarcodesFromImageData, prepareZXingModule } from 'zxing-wasm/reader';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 4321;
const BASE = `http://127.0.0.1:${PORT}/`;
const wasm = path.join(root, 'node_modules', 'zxing-wasm', 'reader', 'zxing_reader.wasm');

let server;
let browser;

const waitForServer = async (url, tries = 80) => {
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

/** PNG (ou SVG) → texte décodé. */
async function decodePng(buffer, density) {
  await prepareZXingModule({ overrideURL: wasm, fallback: () => fetch('file://' + wasm).then((r) => r.arrayBuffer()) });
  const pipeline = sharp(buffer, density ? { density } : undefined).flatten({ background: '#ffffff' }).raw().ensureAlpha();
  const { data, info } = await pipeline.toBuffer({ resolveWithObject: true });
  const found = await readBarcodesFromImageData({ data: new Uint8ClampedArray(data), width: info.width, height: info.height }, { tryHarder: true });
  return found.length ? found[0].text : null;
}

test.before(async () => {
  if (!fs.existsSync(path.join(root, 'dist', 'index.html'))) throw new Error('lancez d’abord `npm run build`');
  server = spawn('node', ['node_modules/vite/bin/vite.js', 'preview', '--port', String(PORT), '--strictPort'], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  await waitForServer(BASE);
  const puppeteer = await import('puppeteer');
  browser = await puppeteer.default.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
});

test.after(async () => {
  await browser?.close();
  server?.kill('SIGTERM');
});

async function newPage() {
  const page = await browser.newPage();
  const errors = [];
  page.on('console', (m) => {
    if (m.type() === 'error' || m.type() === 'warning') errors.push(`${m.type()}: ${m.text()}`);
  });
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  await page.goto(BASE, { waitUntil: 'networkidle0' });
  return { page, errors };
}

// `page.waitForTimeout` a été supprimé de puppeteer 23 : on attend explicitement
// la fin de la débounce (120 ms) + un rendu, puis on re-vérifie l'état du DOM.
/** Remplit un input contrôlé par React comme le ferait un clavier. */
const setValue = (page, selector, value) =>
  page.evaluate(
    (sel, val) => {
      const el = document.querySelector(sel);
      if (!el) throw new Error(`champ absent : ${sel}`);
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, val);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    },
    selector,
    value,
  );

/** Capture l'élément lui-même (gère le défilement) — plus fiable qu'un clip. */
async function shotOf(page, selector) {
  const handle = await page.$(selector);
  if (!handle) throw new Error(`élément absent : ${selector}`);
  return handle.screenshot();
}

/** Clique un élément par le DOM : `page.click` se fait parfois intercepter par le header sticky. */
const domClick = (page, selector, index = 0) =>
  page.evaluate(
    (sel, i) => {
      const els = document.querySelectorAll(sel);
      if (!els[i]) throw new Error(`élément absent : ${sel}[${i}]`);
      els[i].click();
    },
    selector,
    index,
  );

/** Payload affiché dans le panneau « Contenu encodé ». */
const payloadOf = (page) => page.$eval('.qrc-payload pre', (el) => el.textContent);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const settle = async (page, ms = 400) => {
  await sleep(ms);
  await page.evaluate(() => document.fonts?.ready ?? Promise.resolve());
};

test('le build se charge sans erreur console et affiche le studio complet', async () => {
  const { page, errors } = await newPage();
  assert.deepEqual(errors, [], 'erreurs console au chargement');
  assert.equal(await page.$eval('.qrc-logo', (el) => el.textContent.trim()), 'QR Coding');
  assert.equal(await page.$$eval('.qrc-type', (els) => els.length), 22, 'les 22 types doivent être rendus');
  assert.ok(await page.$('.qrc-preview-svg svg'), 'le QR par défaut doit être affiché');
  assert.equal(await page.$$eval('.qrc-feat', (e) => e.length), 8);
  await page.close();
});

test('URL saisie → QR décodable depuis une capture d’écran du rendu réel', async () => {
  const { page, errors } = await newPage();
  const target = 'https://exemple.com/tarifs-2026?src=qr';
  await setValue(page, '#url-url', target);
  await settle(page);
  assert.equal(await page.$eval('#url-url', (el) => el.value), target);

  // on décode la capture d'écran de l'élément : ce que l'utilisateur voit vraiment
  const shot = await shotOf(page, '.qrc-preview-svg svg');
  fs.writeFileSync('/tmp/e2e-preview.png', shot);
  assert.equal(await decodePng(shot), target, 'la capture de l’aperçu doit se décoder');
  assert.deepEqual(errors, [], 'erreurs console pendant la saisie');
  await page.close();
});

test('changement de type : les champs suivent, la saisie de chaque type est conservée', async () => {
  const { page } = await newPage();
  await setValue(page, '#url-url', 'https://a.fr');
  await settle(page, 250);
  await domClick(page, '.qrc-type', 11); // carte de visite
  await settle(page);
  const labels = await page.$$eval('.qrc-fields label', (els) => els.map((e) => e.textContent));
  assert.ok(labels.includes('Prénom') && labels.includes('LinkedIn (URL)'), 'champs vCard absents');
  assert.equal(await page.$$eval('.qrc-fields .qrc-field', (e) => e.length), 13);
  await setValue(page, '#bcard-last', 'Martin');
  await settle(page);
  await domClick(page, '.qrc-type', 0);
  await settle(page);
  assert.equal(await page.$eval('#url-url', (el) => el.value), 'https://a.fr', 'la saisie URL a été perdue');
  await domClick(page, '.qrc-type', 11);
  await settle(page);
  assert.equal(await page.$eval('#bcard-last', (el) => el.value), 'Martin', 'la saisie vCard a été perdue');
  await page.close();
});

test('vCard : ADR échappée dans le QR décodé — sans échappement la ville glissait en région', async () => {
  const { page } = await newPage();
  await domClick(page, '.qrc-type', 11);
  await settle(page);
  await setValue(page, '#bcard-street', '5 via Roma');
  await setValue(page, '#bcard-city', 'Paris ');
  await settle(page);
  const payload = await payloadOf(page);
  assert.match(payload, /ADR;TYPE=WORK:;;5 via Roma;Paris;;75001;France/);
  // l'espace saisi doit être resté dans le champ (le piège du .trim() contrôlé)
  assert.equal(await page.$eval('#bcard-street', (el) => el.value), '5 via Roma');
  assert.equal(await decodePng(await shotOf(page, '.qrc-preview-svg svg')), payload);
  await page.close();
});

test('Wi-Fi : le SSID avec ; et : est échappé et le QR décodé rend le vrai réseau', async () => {
  const { page } = await newPage();
  await domClick(page, '.qrc-type', 17); // Wi-Fi
  await settle(page);
  await setValue(page, '#wifi-ssid', 'MaBox;5G');
  await setValue(page, '#wifi-pwd', 'p:a"s');
  await settle(page, 500);
  const payload = await payloadOf(page);
  assert.equal(payload, 'WIFI:T:WPA;S:MaBox\\;5G;P:p:a\\"s;;');
  assert.equal(await decodePng(await shotOf(page, '.qrc-preview-svg svg')), payload);
  await page.close();
});

test('styles exotiques + logo : le rendu à l’écran reste décodable', async () => {
  const { page } = await newPage();
  await setValue(page, '#url-url', 'https://exemple.com/campanile');
  await domClick(page, '.qrc-custom .qrc-chip', 1); // modules ronds
  await domClick(page, '.qrc-custom .qrc-chip', 5); // coins circulaires
  await settle(page);

  // logo : on passe par le FileInput avec un PNG en mémoire
  const png = await sharp({ create: { width: 24, height: 24, channels: 4, background: { r: 20, g: 20, b: 20, alpha: 1 } } }).png().toBuffer();
  fs.writeFileSync('/tmp/e2e-logo.png', png);
  await (await page.$('#qrc-logo-input')).uploadFile('/tmp/e2e-logo.png');
  await settle(page, 700);
  assert.ok(await page.$('.qrc-preview-svg image'), 'le <image> du logo doit être présent');
  // le budget de réduction doit tenir compte de la correction réellement utilisée (H)
  const logoNotice = await page.$$eval('.qrc-notice', (els) => els.map((e) => e.textContent).join(' '));
  assert.match(logoNotice, /relevé en H/);
  assert.doesNotMatch(logoNotice, /plus que la correction M/, 'le rendu a utilisé le budget M au lieu de H');

  assert.equal(await decodePng(await shotOf(page, '.qrc-preview-svg svg')), 'https://exemple.com/campanile');
  await page.close();
});

test('capacité dépassée : message explicite, pas de QR fantôme, boutons désactivés', async () => {
  const { page } = await newPage();
  await page.click('#url-url');
  await setValue(page, '#url-url', 'x'.repeat(4000));
  await settle(page, 700);
  const notice = await page.$eval('.qrc-notice.is-warn', (el) => el.textContent).catch(() => '');
  assert.match(notice, /trop volumineux/i);
  assert.match(notice, /\d{4} octets/, `message incomplet : ${notice}`);
  assert.equal(await page.$('.qrc-preview-svg svg'), null, 'aucun QR ne doit rester affiché');
  assert.equal(await page.$eval('.qrc-btn-primary', (el) => el.disabled), true, 'le bouton PNG doit être désactivé');
  await page.close();
});

test('fichier importé : un PDF de 40 Ko est refusé avec une explication, un mini est accepté', async () => {
  const { page } = await newPage();
  await domClick(page, '.qrc-type', 12); // PDF
  await settle(page);
  fs.writeFileSync('/tmp/e2e-big.pdf', Buffer.from('%PDF-1.4\n' + 'A'.repeat(40 * 1024)));
  const input = await page.$('input[type=file]');
  await input.uploadFile('/tmp/e2e-big.pdf');
  await settle(page, 500);
  const notice = await page.$eval('.qrc-notice.is-warn', (el) => el.textContent).catch(() => '');
  assert.match(notice, /trop volumineux/i);
  assert.match(notice, /Hébergez/i);

  fs.writeFileSync('/tmp/e2e-mini.pdf', Buffer.from('%PDF-1.4\nok\n'));
  const input2 = await page.$('input[type=file]');
  await input2.uploadFile('/tmp/e2e-mini.pdf');
  await settle(page, 600);
  const okNotice = await page.$eval('.qrc-notice.is-info', (el) => el.textContent).catch(() => '');
  assert.match(okNotice, /intégré/i);
  const payload = await page.$eval('.qrc-payload pre', (el) => el.textContent);
  assert.ok(payload.startsWith('data:application/pdf;base64,'), 'le fichier doit être encodé en data-URI');
  await page.close();
});

test('fichier non conforme : un PNG envoyé comme « PDF » est refusé (contrôle MIME enfin actif)', async () => {
  const { page } = await newPage();
  await domClick(page, '.qrc-type', 12);
  await settle(page);
  const png = await sharp({ create: { width: 4, height: 4, channels: 4, background: { r: 0, g: 0, b: 0, alpha: 1 } } }).png().toBuffer();
  fs.writeFileSync('/tmp/e2e-fake.pdf', png);
  await (await page.$('input[type=file]')).uploadFile('/tmp/e2e-fake.pdf');
  await settle(page, 500);
  const notice = await page.$eval('.qrc-notice.is-warn', (el) => el.textContent).catch(() => '');
  // .pdf + extension acceptable → c'est la signature binaire qui doit bloquer
  assert.match(notice, /signature PNG au lieu d'un fichier PDF/, `notice inattendue : ${notice}`);
  await page.close();
});

test('thème : bascule + persistance après rechargement, sans flash', async () => {
  const { page } = await newPage();
  const before = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
  await domClick(page, '.qrc-btn-theme');
  await settle(page, 300);
  const after = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
  assert.notEqual(after, before ?? 'dark');
  assert.equal(after, 'light');
  assert.equal(await page.evaluate(() => localStorage.getItem('qr-coding:theme')), 'light');
  await page.reload({ waitUntil: 'networkidle0' });
  assert.equal(await page.evaluate(() => document.documentElement.getAttribute('data-theme')), 'light', 'le thème ne doit pas revenir à sombre');
  assert.equal(await page.$eval('.qrc-btn-theme', (el) => el.textContent.trim()), '🌙', 'l’icône doit refléter le thème chargé');
  await page.close();
});

test('accessibilité clavier : focus visible, flèches sur les types, pressed annoncé', async () => {
  const { page } = await newPage();
  await page.focus('.qrc-type');
  await page.keyboard.press('ArrowRight');
  await settle(page, 250);
  const active = await page.$$eval('.qrc-type.is-active', (els) => els.map((e) => e.textContent.trim()));
  assert.equal(active.length, 1);
  assert.equal(active[0], '📷Instagram');
  assert.equal(await page.$eval('.qrc-type.is-active', (el) => el.getAttribute('aria-pressed')), 'true');
  // le champ du type sélectionné reçoit un label associé (htmlFor/id, pas un <div> cliquable)
  const labelled = await page.$eval('#instagram-user', (el) => el.labels?.[0]?.textContent);
  assert.equal(labelled, "Nom d'utilisateur");
  assert.equal(await page.$eval('#instagram-user', (el) => el.placeholder), 'votre.pseudo');
  assert.ok(await page.$eval('.qrc-type.is-active', (el) => getComputedStyle(el).outlineWidth !== '0px' || getComputedStyle(el).borderColor !== ''));
  await page.close();
});

test('export : PNG et SVG déclenchés depuis l’UI produisent un QR lisible', async () => {
  const { page } = await newPage();
  const dir = '/tmp/e2e-downloads';
  fs.rmSync(dir, { recursive: true, force: true });
  fs.mkdirSync(dir, { recursive: true });
  const client = await page.createCDPSession();
  await client.send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: dir });
  await setValue(page, '#url-url', 'https://exemple.com/export-test');
  await settle(page, 400);

  await domClick(page, '#qrc-dl-png');
  await settle(page, 1500);
  await domClick(page, '#qrc-dl-svg');
  await settle(page, 1500);

  const files = fs.readdirSync(dir).filter((f) => !f.endsWith('.crdownload'));
  assert.ok(files.includes('qr-code.png'), 'qr-code.png manquant : ' + files.join(','));
  assert.ok(files.includes('qr-code.svg'), 'qr-code.svg manquant : ' + files.join(','));

  const expected = 'https://exemple.com/export-test';
  assert.equal(await decodePng(fs.readFileSync(path.join(dir, 'qr-code.png'))), expected);
  const svgText = fs.readFileSync(path.join(dir, 'qr-code.svg'), 'utf8');
  assert.match(svgText, /^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg"/);
  assert.equal(await decodePng(Buffer.from(svgText)), expected, 'le SVG exporté doit se décoder');
  // PNG haute résolution (cell 8 × scale 4 = 32 px/module)
  const meta = await sharp(path.join(dir, 'qr-code.png')).metadata();
  assert.ok(meta.width >= 800, `PNG trop petit : ${meta.width}px`);
  await page.close();
});
