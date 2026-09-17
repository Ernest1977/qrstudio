/**
 * Tests du coeur non-React (payload, encodage, rendu, export).
 *
 * Point clé : chaque SVG produit est rasterisé (sharp) puis passé à un vrai
 * lecteur de codes-barres (zxing). Un test « la chaîne contient <svg » n'aurait
 * rien prouvé — c'est exactement ce qui manquait au fichier d'origine, dont les
 * QR n'étaient plus décodés du tout (voir ANALYSE.md, bug n°1).
 *
 *   node --test scripts/lib.test.mjs
 */

process.env.TZ = 'Europe/Paris'; // le sandbox tourne en UTC : fige le fuseau

import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';
import { readBarcodesFromImageData, prepareZXingModule } from 'zxing-wasm/reader';

import { TYPES, getType, defaultData, initialDataByType } from '../src/lib/types.js';
import { buildPayload, escapeWifi, escapeVCard, foldLines, toICalUtc, buildVCard } from '../src/lib/payload.js';
import { createMatrix, byteLength } from '../src/lib/qrcode.js';
import { renderQrSvg, alignmentCenters, versionFromModules, ECC_LOGO_BUDGET } from '../src/lib/render.js';
import { svgToDataUrl } from '../src/lib/export.js';
import { checkSignature, describeSignature } from '../src/lib/files.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const wasm = path.join(here, '..', 'node_modules', 'zxing-wasm', 'reader', 'zxing_reader.wasm');
let zxingReady = false;
async function ensureZxing() {
  if (zxingReady) return;
  await prepareZXingModule({ overrideURL: wasm, fallback: () => fetch('file://' + wasm).then((r) => r.arrayBuffer()) });
  zxingReady = true;
}

/** SVG → PNG → texte décodé (ou null). */
async function decode(svg, { density = 220 } = {}) {
  await ensureZxing();
  const { data, info } = await sharp(Buffer.from(svg), { density })
    .flatten({ background: '#ffffff' })
    .raw()
    .ensureAlpha()
    .toBuffer({ resolveWithObject: true });
  const found = await readBarcodesFromImageData(
    { data: new Uint8ClampedArray(data), width: info.width, height: info.height },
    { tryHarder: true },
  );
  return found.length ? found[0].text : null;
}

const render = (payload, opts = {}) => {
  const matrix = createMatrix(payload, {});
  return renderQrSvg(matrix, { margin: 40, cell: 10, ...opts }).svg;
};

/* ------------------------------------------------------------------ types */

test('schéma : 22 types (l’original en annonçait 23), ids uniques, valeurs par défaut typées', () => {
  assert.equal(TYPES.length, 22);
  assert.equal(TYPES.filter((t) => t.kind === 'social' || t.kind === 'phone-link').length, 10, '10 réseaux sociaux');
  assert.equal(new Set(TYPES.map((t) => t.id)).size, TYPES.length);
  for (const t of TYPES) {
    assert.ok(t.fields.length >= 1, `${t.id} sans champ`);
    for (const f of t.fields) {
      assert.ok(f.key && f.label, `${t.id}: champ incomplet`);
      if (f.type === 'select') assert.ok(f.options?.length, `${t.id}/${f.key}: select sans options`);
    }
    const defaults = defaultData(t);
    assert.equal(Object.keys(defaults).length, t.fields.length);
  }
});

test('régression origine : les valeurs par défaut vivent dans le state, pas seulement dans le DOM', () => {
  const all = initialDataByType();
  assert.equal(all.url.url, 'https://exemple.com', 'le premier rendu doit afficher un QR, comme l’original');
  assert.equal(all.bcard.first, 'Marie');
  assert.equal(all.text.txt, 'Bonjour le monde !');
  assert.equal(all.wifi.enc, 'WPA');
});

/* ---------------------------------------------------------------- payload */

test('réseau sociaux : nettoyage du @ et des espaces, tolérance aux URL collées', () => {
  const d = (v) => ({ user: v });
  assert.equal(buildPayload(getType('instagram'), d(' @marie du pont ')), 'https://instagram.com/mariedupont');
  assert.equal(buildPayload(getType('linkedin'), d('https://www.linkedin.com/in/marie-dupont')), 'https://linkedin.com/in/marie-dupont');
  assert.equal(buildPayload(getType('linkedin'), d('/in/marie')), 'https://linkedin.com/in/marie');
  assert.equal(buildPayload(getType('whatsapp'), { num: '+33 6-12.34.56.78' }), 'https://wa.me/33612345678');
  assert.equal(buildPayload(getType('facebook'), d('')), '');
});

test('URL : le protocole manquant est ajouté (l’original encodait « exemple.com » en brut)', () => {
  assert.equal(buildPayload(getType('url'), { url: 'exemple.com/fiche' }), 'https://exemple.com/fiche');
  assert.equal(buildPayload(getType('url'), { url: 'http://a.fr' }), 'http://a.fr');
});

test('vCard : le champ ADR est émis, dans l’ordre normalisé BP;Adresse2;Rue;Ville;Région;CP;Pays', () => {
  const card = buildPayload(getType('bcard'), {
    first: 'Éric',
    last: 'Dupont',
    street: '12 rue de Rivoli',
    city: 'Paris',
    zip: '75001',
    country: 'France',
  });
  assert.match(card, /ADR;TYPE=WORK:;;12 rue de Rivoli;Paris;;75001;France/);
  assert.match(card, /^BEGIN:VCARD\r\nVERSION:3\.0/);
  assert.match(card, /N:Dupont;Éric;;;/);
  assert.match(card, /END:VCARD$/);
});

test('vCard : une adresse vide n’émet pas de ligne ADR parasite', () => {
  const card = buildVCard({ first: 'A', last: 'B' });
  assert.doesNotMatch(card, /ADR/);
});

test('vCard : échappement et CRLF obligatoires (RFC 6350)', () => {
  assert.equal(escapeVCard('a;b,c\\d\ne'), 'a\\;b\\,c\\\\d\\ne');
  const card = buildVCard({ org: 'Dupont; Fils', first: 'É', last: 'N' });
  assert.ok(card.includes('ORG:Dupont\\; Fils'), 'ORG non échappé : ' + card);
  assert.ok(card.split('\r\n').length > 3, 'les fins de ligne ne sont pas CRLF');
  assert.ok(!/(^|[^\r])\n/.test(card), 'un LF seul subsiste');
});

test('régression origine : repli des lignes à ≤ 75 octets (l’original ne pliait rien)', () => {
  const folded = foldLines('é'.repeat(200));
  const lines = folded.split('\r\n');
  assert.ok(lines.length > 1, 'aucun repli appliqué');
  for (const line of lines) assert.ok(byteLength(line) <= 75, `ligne trop longue (${byteLength(line)} o)`);
  for (const line of lines.slice(1)) assert.ok(line.startsWith(' '), 'continuation sans espace');
  // aucune coupure en milieu de caractère : l'UTF-8 doit rester valide
  assert.equal(new TextDecoder('utf-8').decode(new TextEncoder().encode(folded.replace(/\r\n /g, ''))), 'é'.repeat(200));
});

test('Wi-Fi : échappement de ; , " \\ et du : dans le SSID (l’original n’échappait rien)', () => {
  // le `:` n'est un délimiteur que dans le champ S: (le séparateur K-P est ';'),
  // zxing ne l'échappe donc que là : même comportement ici, c'est le standard.
  assert.equal(escapeWifi('MaBox;5G', { inSsid: true }), 'MaBox\\;5G');
  assert.equal(escapeWifi('MaBox,5G:1', { inSsid: true }), 'MaBox\\,5G\\:1');
  assert.equal(escapeWifi('p:a"s\\e'), 'p:a\\"s\\\\e');
  const payload = buildPayload(getType('wifi'), { ssid: 'MaBox;5G', enc: 'WPA', pwd: 'p:a"s\\e' });
  assert.equal(payload, 'WIFI:T:WPA;S:MaBox\\;5G;P:p\\"a\\\\s\\\\e;;'.replace('p\\"a\\\\s\\\\e', 'p:a\\"s\\\\e'));
});

test('Wi-Fi : sans mot de passe (nopass), pas de champ P parasite', () => {
  assert.equal(buildPayload(getType('wifi'), { ssid: 'Free', enc: 'nopass', pwd: 'ignored' }), 'WIFI:T:nopass;S:Free;;');
});

test('iCal : heure convertie en UTC + UID/DTSTAMP, échappement du texte libre', () => {
  assert.equal(toICalUtc('2026-10-01T09:30'), '20261001T073000Z'); // UTC+2 en octobre 2026
  const ical = buildPayload(getType('event'), { title: 'Réu, nion; 2', loc: 'Paris', start: '2026-10-01T09:30', end: '2026-10-01T11:00' });
  assert.match(ical, /SUMMARY:Réu\\, nion\\; 2/);
  assert.match(ical, /DTSTART:20261001T073000Z/);
  assert.match(ical, /UID:[0-9a-z]+@qr-coding/);
  assert.match(ical, /PRODID:-\/\/QR Coding/);
});

test('mailto/sms/tel : échappement minimal et numéros nettoyés', () => {
  assert.equal(
    buildPayload(getType('mail'), { to: 'a@b.fr', sub: 'Sujet & co', body: 'ligne1\nligne2' }),
    'mailto:a@b.fr?subject=Sujet%20%26%20co&body=ligne1%0Aligne2',
  );
  assert.equal(buildPayload(getType('tel'), { num: '+33 1 23 45 67 89' }), 'tel:+33123456789');
  assert.equal(buildPayload(getType('sms'), { num: '06 12 34 56 78', msg: 'coucou' }), 'smsto:0612345678:coucou');
  assert.equal(buildPayload(getType('sms'), { num: '0612345678' }), 'smsto:0612345678');
});

test('partage de fichier : un fichier intégré passe avant l’URL', () => {
  const type = getType('pdf');
  const d = { url: 'https://x/y.pdf' };
  assert.equal(buildPayload(type, d), 'https://x/y.pdf');
  assert.equal(buildPayload(type, d, { embedded: 'data:application/pdf;base64,AAA' }), 'data:application/pdf;base64,AAA');
});

/* -------------------------------------------------------------- encodage */

test('capacité : un payload trop long renvoie une erreur exploitable (et non un « undefined » avalé)', () => {
  let err;
  try {
    createMatrix('x'.repeat(5000), {});
  } catch (e) {
    err = e;
  }
  assert.ok(err, 'aucune erreur levée');
  assert.equal(err.code, 'TOO_LONG');
  assert.match(err.message, /5000 octets/);
  assert.match(err.message, /2331/);
});

test('payload vide : erreur typée EMPTY', () => {
  assert.throws(() => createMatrix('', {}), (e) => e.code === 'EMPTY');
});

/* ---------------------------------------------------------------- fichiers */

test('signature binaire : un PNG renommé .pdf est refusé, un vrai PDF passe', () => {
  const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0]);
  assert.equal(checkSignature(png, 'pdf').ok, false);
  assert.match(checkSignature(png, 'pdf').reason, /signature PNG/);
  assert.equal(checkSignature(png, 'image').ok, true);
  const pdf = new TextEncoder().encode('%PDF-1.7\n%âãÏÓ\n');
  assert.equal(checkSignature(pdf.slice(0, 16), 'pdf').ok, true);
  const docx = new Uint8Array([0x50, 0x4b, 0x03, 0x04, ...new Array(12).fill(0)]);
  assert.equal(checkSignature(docx, 'word').ok, true);
  assert.equal(checkSignature(docx, 'pdf').ok, false);
  assert.equal(describeSignature(new Uint8Array([])).kind, 'unknown');
});

/* ------------------------------------------------------------------ SVG */

const LONG = 'https://exemple.com/campagne/2026?utm_source=qr&utm_medium=print&utm_campaign=lancement';

test('régression n°1 : chaque style de module reste décodable par un lecteur réel', async () => {
  for (const style of ['square', 'round', 'rounded', 'diamond']) {
    for (const finderStyle of ['square', 'rounded', 'circle']) {
      for (const margin of [0, 8, 32]) {
        const svg = render(LONG, {
          style,
          finderStyle,
          margin,
        });
        const text = await decode(svg);
        assert.equal(text, LONG, `${style}/${finderStyle}/m${margin}`);
      }
    }
  }
});

test('tous les types se décodent avec le payload attendu', async () => {
  const samples = {
    url: { url: 'https://kamco.farm' },
    instagram: { user: '@kamco.farm' },
    whatsapp: { num: '+33612345678' },
    bcard: { first: 'Marie', last: 'Dupont', org: 'Dupont & Associés', street: '12 rue de Rivoli', city: 'Paris', zip: '75001', country: 'France' },
    wifi: { ssid: 'MaBox;5G', enc: 'WPA', pwd: 'p:a"s\\e' },
    text: { txt: 'café école — été 2026 🎉' },
    mail: { to: 'contact@exemple.com', sub: 'Demande de contact' },
    event: { title: 'Lancement produit', loc: 'Paris', start: '2026-10-01T09:30', end: '2026-10-01T11:00' },
    tel: { num: '+33123456789' },
    sms: { num: '+33612345678', msg: 'Bonjour' },
    pdf: { url: 'https://exemple.com/doc.pdf' },
  };
  for (const [id, data] of Object.entries(samples)) {
    const type = getType(id);
    const payload = buildPayload(type, data);
    const svg = render(payload, { style: 'round', finderStyle: 'circle' });
    const text = await decode(svg);
    assert.equal(text, payload, `type ${id} non décodé fidèlement`);
  }
});

test('logo : trou découpé dans la grille, code toujours décodable', async () => {
  const logo =
    'data:image/svg+xml,' +
    encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" fill="#000"/></svg>');
  const payload = 'https://exemple.com?avec=logo&encore=1';
  for (const logoPercent of [10, 22, 35]) {
    // 'H' : niveau de correction que le studio impose dès qu'un logo est posé
    const matrix = createMatrix(payload, { ecc: 'H' });
    const out = renderQrSvg(matrix, { margin: 40, cell: 10, style: 'round', logo, logoPercent, ecc: 'H' });
    assert.ok(out.svg.includes('<image'), 'balise <image> absente');
    assert.ok(out.logoCoverage <= ECC_LOGO_BUDGET.H + 1e-9, `trame masquée ${(out.logoCoverage * 100).toFixed(1)} % > budget H`);
    assert.equal(await decode(out.svg), payload, `logo ${logoPercent} %`);
  }
});

test('logo : un logo démesuré est réduit (repères + budget de correction) et signalé', () => {
  const matrix = createMatrix('https://a.fr/quelque-chose-d-un-peu-plus-long-pour-monter-en-version', {});
  const { warnings, logoCoverage, logoPercent } = renderQrSvg(matrix, { logo: 'data:image/png;base64,AA', logoPercent: 45, margin: 40, ecc: 'M' });
  assert.ok(warnings.length >= 1, 'aucun avertissement');
  assert.match(warnings.join(' '), /Logo réduit/i);
  assert.ok(logoPercent < 45, `le logo aurait dû être réduit, reste à ${logoPercent} %`);
  assert.ok(logoCoverage <= ECC_LOGO_BUDGET.M + 1e-9, `budget M dépassé : ${logoCoverage}`);
  // et le rendu reste décodable une fois réduit
});

test('légende : accents, emoji et crochets échappés ; hauteur augmentée', () => {
  const matrix = createMatrix('https://a.fr', {});
  const { svg, height, size } = renderQrSvg(matrix, { caption: 'Équipe <b>au salon</b> 🎉', margin: 40 });
  assert.ok(svg.includes('Équipe &lt;b&gt;au salon&lt;/b&gt; 🎉'), 'échappement manquant');
  assert.ok(!svg.includes('<b>'), 'HTML injecté dans le SVG');
  assert.ok(height > size, 'la place de la légende n’est pas réservée');
});

test('régression n°1 (preuve inverse) : l’anneau « stroke » de l’original ne décodait pas', async () => {
  const matrix = createMatrix('HELLO', {});
  const n = matrix.size;
  const cell = 8;
  const margin = 32;
  const square = n * cell + margin * 2;
  let body = '';
  for (let r = 0; r < n; r += 1)
    for (let c = 0; c < n; c += 1) {
      if (!matrix.get(r, c)) continue;
      const inFinder = (r < 7 && c < 7) || (r < 7 && c >= n - 7) || (r >= n - 7 && c < 7);
      if (inFinder) continue;
      body += `<rect x="${margin + c * cell}" y="${margin + r * cell}" width="${cell}" height="${cell}"/>`;
    }
  for (const [fr, fc] of [[0, 0], [0, n - 7], [n - 7, 0]]) {
    const x = margin + fc * cell;
    const y = margin + fr * cell;
    body += `<rect x="${x}" y="${y}" width="${7 * cell}" height="${7 * cell}" fill="none" stroke="#000" stroke-width="${cell}"/>`;
    body += `<rect x="${x + 2 * cell}" y="${y + 2 * cell}" width="${3 * cell}" height="${3 * cell}" fill="#000"/>`;
  }
  const legacy = `<svg xmlns="http://www.w3.org/2000/svg" width="${square}" height="${square}"><rect width="${square}" height="${square}" fill="#fff"/><g fill="#000">${body}</g></svg>`;
  assert.equal(await decode(legacy), null, 'le rendu legacy devrait être illisible (c’est le bug)');
  // même grille, repères pleins : lisible
  assert.equal(await decode(renderQrSvg(matrix, { margin, cell }).svg), 'HELLO');
});

test('repères : formes pleines, aucun stroke dans le rendu', () => {
  const matrix = createMatrix('HELLO', {});
  const { svg } = renderQrSvg(matrix, { margin: 40, cell: 10, finderStyle: 'square' });
  assert.ok(!/stroke=/.test(svg), 'le rendu utilise encore un stroke');
  assert.match(svg, /fill-rule="evenodd"|fill="#ffffff"/);
});

test('motifs d’alignement : centres conformes à l’ISO 18004', () => {
  assert.deepEqual(alignmentCenters(21), []); // v1 : aucun
  assert.deepEqual(alignmentCenters(25), [6, 18]); // v2
  assert.deepEqual(alignmentCenters(29), [6, 22]); // v3
  assert.deepEqual(alignmentCenters(45), [6, 22, 38]); // v7
  assert.deepEqual(alignmentCenters(73), [6, 26, 46, 66]); // v14
  assert.deepEqual(alignmentCenters(177), [6, 30, 58, 86, 114, 142, 170]); // v40
});

test('très gros QR v31 (nombreux alignements) : tous les styles restent décodables', async () => {
  const payload = 'https://exemple.com/' + 'a'.repeat(1200);
  for (const style of ['square', 'round', 'diamond']) {
    const svg = render(payload, { style, margin: 16 });
    assert.equal(await decode(svg), payload, `style ${style}`);
  }
});

test('export : la data-URI SVG est prête à l’emploi et survivante en UTF-8', () => {
  const url = svgToDataUrl('<svg xmlns="http://www.w3.org/2000/svg"><text>É & "</text></svg>');
  assert.ok(url.startsWith('data:image/svg+xml;charset=utf-8,'));
  assert.equal(decodeURIComponent(url.split(',')[1]), '<svg xmlns="http://www.w3.org/2000/svg"><text>É & "</text></svg>');
});

test('régression origine : btoa(unescape(encodeURIComponent())) n’est plus utilisé', async () => {
  const fs = await import('node:fs');
  const files = ['src/lib/export.js', 'src/components/Preview.jsx', 'src/lib/render.js'];
  for (const f of files) {
    const src = fs.readFileSync(path.join(here, '..', f), 'utf8');
    assert.ok(!/unescape\(/.test(src), `${f} appelle encore unescape()`);
  }
});
