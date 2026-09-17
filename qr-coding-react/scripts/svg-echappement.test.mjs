/**
 * Le seul endroit du front où du HTML construit à la main atterrit dans le DOM : l'aperçu SVG, injecté
 * par `Preview.jsx` avec `dangerouslySetInnerHTML`. C'est acceptable aussi longtemps que le générateur
 * n'interpole aucune valeur non validée — les légendes passent par `escapeXml`, les couleurs et l'URL du
 * logo par `couleurValide` / `urlImageValide`.
 *
 * Ces tests sont la goupille : sans eux, le prochain `<text>` ou `href=` ajouté dans `render.js` peut
 * ouvrir une XSS par état partagé sans que personne ne le voie passer.
 */

import assert from 'node:assert/strict';
import test from 'node:test';

import {
  couleurValide, escapeXml, renderQrSvg, urlImageValide,
} from '../src/lib/render.js';

const grille3 = [
  [true, false, true],
  [false, true, false],
  [true, false, true],
];
const matrice = { size: 3, get: (r, c) => Boolean(grille3[r][c]) };
const HOSTILE = '</text><script>alert(1)</script><text>';

test('escapeXml neutralise une balise injectée dans une légende', () => {
  const echappe = escapeXml(HOSTILE);
  assert.ok(!echappe.includes('<'), `il reste une balise ouverte : ${echappe}`);
  assert.ok(!echappe.includes('>'), `il reste une balise fermante : ${echappe}`);
  assert.ok(!echappe.includes('"'), `il reste un guillemet nu : ${echappe}`);
});

test("aucune saisie hostile ne sort du SVG en tant que balise ou attribut", () => {
  const rendu = renderQrSvg(matrice, {
    caption: HOSTILE,
    moduleColor: '" onload="alert(1)',
    background: '"><img src=x>',
    logo: '" onerror="alert(1)',
  });
  assert.ok(rendu, 'le générateur doit rendre un objet');
  assert.ok(rendu.svg.startsWith('<svg'), 'le générateur doit rendre un SVG');
  for (const motif of [/<script/i, /onload/i, /onerror/i, /<img/, /onmouseover/i]) {
    assert.ok(!motif.test(rendu.svg), `${motif} présent dans le SVG : ${rendu.svg.slice(0, 240)}`);
  }
  // La légende hostile est bien là, mais sous sa forme échappée : rien n'a été avalé au passage.
  assert.match(rendu.svg, /&lt;\/text&gt;&lt;script&gt;/);
  // Une valeur refusée retombe sur le défaut du composant, elle n'est pas « échappée en espérant ».
  assert.match(rendu.svg, /fill="#111827"/);
  assert.match(rendu.svg, /fill="#ffffff"/);
  assert.equal(rendu.warnings.length, 1, 'le logo refusé doit être signalé à l’utilisateur');
});

test('couleurValide accepte les notations #rgb, #rgba, #rrggbb, #rrggbbaa', () => {
  for (const valeur of ['#fff', '#ffff', '#112233', '#11223344', '#ABC']) {
    assert.equal(couleurValide(valeur, 'defaut'), valeur);
  }
  assert.equal(couleurValide('#13', 'defaut'), 'defaut');
  assert.equal(couleurValide('red', 'defaut'), 'defaut');
  assert.equal(couleurValide('javascript:1', 'defaut'), 'defaut');
  assert.equal(couleurValide(undefined, 'defaut'), 'defaut');
});

test('urlImageValide n’accepte qu’une image data: ou http(s):', () => {
  assert.ok(urlImageValide('data:image/png;base64,iVBORw0KGgo='));
  // Le format que le test historique du logo utilise : SVG en `data:` percent-encodé, à accepter.
  assert.ok(urlImageValide('data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%2F%3E'));
  assert.ok(urlImageValide('https://cdn.example.com/logo.png'));
  assert.equal(urlImageValide('javascript:alert(1)'), null);
  assert.equal(urlImageValide('data:text/html;base64,PHA+'), null);
  assert.equal(urlImageValide('http://x/" onerror="1'), null);
  assert.equal(urlImageValide(null), null);
});
