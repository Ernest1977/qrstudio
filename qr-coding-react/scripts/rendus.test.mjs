/**
 * Les rendus vérifiés (art, GIF) et les codes promo, côté client.
 *
 * Ce fichier ne teste pas le rendu d'images — c'est le travail de `tests/test_qr_art.py` côté serveur.
 * Il teste ce que le navigateur peut rater tout seul : les URL construites (un paramètre oublié = un
 * client servi avec le GIF d'un autre), les bornes envoyées, et la lecture des en-têtes de preuve.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

import {
  BORNES_RENDU,
  PART_STATIQUE_MIN,
  STYLE_DEFAUT,
  animationDemandee,
  borner,
  decrirePreuve,
  estCapaciteRefusee,
  framesStatiques,
  lirePreuve,
  urlApercu,
  urlRendu,
} from '../src/lib/rendus.js';

const ICI = dirname(fileURLToPath(import.meta.url));
const RACINE = resolve(ICI, '..'); // `qr-coding-react/` ; l'API est le frere, `../api`
const contenu = (chemin) => readFileSync(resolve(RACINE, chemin), 'utf8');

test('les bornes du client sont celles du serveur', () => {
  // Lues dans le source du serveur : une table dupliquée à la main est une table qui dérive.
  const art = contenu('../api/apps/qr/animation.py'); // memes bornes, un seul fichier a lire
  assert.equal(BORNES_RENDU.frames.min, lireConstante(art, 'FRAMES_MIN'));
  assert.equal(BORNES_RENDU.frames.max, lireConstante(art, 'FRAMES_MAX'));
  assert.equal(BORNES_RENDU.duree.min, lireConstante(art, 'DUREE_MIN'));
  assert.equal(BORNES_RENDU.duree.max, lireConstante(art, 'DUREE_MAX'));
  assert.equal(BORNES_RENDU.liser.max, lireConstante(art, 'LISIERE_MAX'));
  assert.equal(PART_STATIQUE_MIN, lireConstante(art, 'PART_STATIQUE_MIN'));
});

/**
 * Lit une constante du serveur, sous ses deux formes d'ecriture : `NOM = 8` et la paire
 * `NOM_MIN, NOM_MAX = 8, 48`. Le module python ecrit les bornes en une ligne par paire ; ignorer cette
 * forme ferait echouer le test pour une raison de syntaxe, pas de divergence — le pire genre de rouge.
 */
function lireConstante(source, nom) {
  for (const ligne of source.split('\n')) {
    const affectation = ligne.match(/^([A-Z_0-9 ,]+)=\s*(.+)$/);
    if (!affectation) continue;
    const noms = affectation[1].split(',').map((partie) => partie.trim()).filter(Boolean);
    const valeurs = affectation[2].split('#')[0].split(',').map((partie) => partie.trim());
    const index = noms.indexOf(nom);
    if (index >= 0 && /^\d+(?:\.\d+)?$/.test(valeurs[index] || '')) return Number(valeurs[index]);
  }
  throw new Error(`${nom} introuvable cote serveur : la table du client n'a plus de source`);
}

test('borner: entrées vides → défaut, jamais NaN envoyé au serveur', () => {
  for (const vide of [undefined, null, '', 'abc', NaN]) {
    assert.equal(borner(vide, BORNES_RENDU.frames), BORNES_RENDU.frames.defaut, String(vide));
  }
  assert.equal(borner(4000, BORNES_RENDU.frames), BORNES_RENDU.frames.max);
  assert.equal(borner(-3, BORNES_RENDU.liser), BORNES_RENDU.liser.min);
  assert.equal(borner('32', BORNES_RENDU.frames), 32);
});

test("l'URL d'aperçu omet les valeurs par défaut et borne le reste", () => {
  const url = new URL(urlApercu({ fmt: 'art', contenu: 'https://x.y/a?c=1', style: STYLE_DEFAUT }), 'https://h');
  assert.equal(url.pathname, '/api/v1/qr/apercu/');
  assert.equal(url.searchParams.get('fmt'), 'art');
  assert.equal(url.searchParams.get('payload'), 'https://x.y/a?c=1', 'le payload doit survivre à l’encodage');
  assert.equal(url.searchParams.has('style'), false, 'le style par défaut ne se mentionne pas');
  assert.equal(url.searchParams.has('size'), false);

  const anime = new URL(
    urlApercu({ fmt: 'gif', contenu: 'x', style: 'nuit', taille: 640, frames: 9999, liser: -4, accroche: 'a'.repeat(80) }),
    'https://h',
  );
  assert.equal(anime.searchParams.get('frames'), String(BORNES_RENDU.frames.max));
  assert.equal(anime.searchParams.get('liser'), '0');
  assert.equal(anime.searchParams.get('accroche').length, 40, 'l’accroche est tronquée comme côté serveur');
  assert.equal(anime.searchParams.get('size'), '640');
});

test('un contenu sans schéma et un contenu accentué passent intacts', () => {
  for (const texte of ['bonjour', 'https://x.y/é?b=1&c=2', '   ', 'Wi-Fi invité — plage 2026']) {
    const url = new URL(urlApercu({ contenu: texte }), 'https://h');
    assert.equal(url.searchParams.get('payload'), texte);
  }
});

test('urlRendu construit le téléchargement à partir du design enregistré', () => {
  const design = { art: 'halles', frames: 40, liser: 8, duree: 120, animation: 'bordure' };
  const url = new URL(urlRendu(42, { fmt: 'gif', design }), 'https://h');
  assert.equal(url.pathname, '/api/v1/qr/42/rendu/');
  assert.equal(url.searchParams.get('fmt'), 'gif');
  assert.equal(url.searchParams.get('style'), 'halles');
  assert.equal(url.searchParams.get('frames'), '40');
  assert.equal(url.searchParams.get('liser'), '8');

  // PNG de base : pas de `fmt=art`, et le style n'a pas à être envoyé (le serveur le déduit du rendu).
  const simple = new URL(urlRendu(7, { fmt: 'png', taille: 320 }), 'https://h');
  assert.equal(simple.searchParams.get('fmt'), 'png');
  assert.equal(simple.searchParams.get('size'), '320');
  assert.equal(simple.searchParams.has('style'), false);
});

test('framesStatiques: la promesse du scanner est arrondie vers le haut', () => {
  for (let n = BORNES_RENDU.frames.min; n <= BORNES_RENDU.frames.max; n += 1) {
    const statiques = framesStatiques(n);
    assert.ok(statiques / n >= PART_STATIQUE_MIN, `${statiques}/${n} sous le seuil promis`);
    assert.ok(statiques < n || n <= 1, 'toutes les frames immobiles = pas une animation');
  }
  assert.equal(framesStatiques(24), 15);
});

test('animationDemandee lit le design tel qu’il est stocké', () => {
  assert.equal(animationDemandee({}), false);
  assert.equal(animationDemandee({ animation: 'aucune' }), false);
  assert.equal(animationDemandee({ animation: 'bordure' }), true);
});

test('lirePreuve tolère un en-tête tronqué par un proxy', () => {
  const bonne = { headers: { get: (nom) => ({ 'X-Lisibilite': 'verifiee', 'X-Score': '{"module_px":12}' })[nom] || null } };
  const preuve = lirePreuve(bonne);
  assert.equal(preuve.verifie, true);
  assert.equal(preuve.detail.module_px, 12);

  const tronque = ['{', '"module_px":'].join('');
  const cassee = { headers: { get: () => tronque } };
  assert.equal(lirePreuve(cassee).detail, null);
  assert.equal(lirePreuve(undefined).verifie, false);
});

test('decrirePreuve dit la vérité qui fâche', () => {
  const texte = decrirePreuve({ verifie: true, logoRetire: true, detail: { taux_contraste: 9.5 } }, { format: 'art' });
  assert.match(texte, /relu et décodé/);
  assert.match(texte, /logo retiré/);
  const gif = decrirePreuve({ detail: { frames_statiques: 15, frames: 24, part_statique: 0.625 } }, { format: 'gif' });
  assert.match(gif, /15\/24 frames immobiles \(63 %\)/);
});

test('un 402 de capacité nest pas une erreur technique', () => {
  assert.equal(estCapaciteRefusee({ code: 'plan_required', statut: 402 }), true);
  assert.equal(estCapaciteRefusee({ statut: 402 }), true, "ErreurApi porte `statut`, pas `status`");
  assert.equal(estCapaciteRefusee({ status: 402 }), true, 'une Response crue porte `status`');
  assert.equal(estCapaciteRefusee({ code: 'quota_exceeded', statut: 402 }), true);
  assert.equal(estCapaciteRefusee({ code: 'payload_ilisible', statut: 400 }), false);
  assert.equal(estCapaciteRefusee(undefined), false);
});

test('les nouveautés de la grille ont un libellé français', () => {
  // Sans libellé, la carte du palier affiche la clé brute (`qr_artistique_ia`) au client : c'est le
  // genre de détail qui ne casse aucun test et se voit sur chaque écran.
  const source = contenu('src/content.js');
  for (const cle of ['qr_artistique_ia', 'qr_anime', 'codes_promo', 'boutique_templates']) {
    assert.match(source, new RegExp(`${cle}:\\s*'`), `${cle} sans libellé côté front`);
  }
  assert.match(source, /codes_promo_max/);
});

test('le studio et labonnement branchent les nouveaux panneaux', () => {
  const studio = contenu('src/components/Studio.jsx');
  assert.match(studio, /RendusServeur/);
  assert.match(studio, /payload=\{payload\} design=\{state\.design\}/);
  const facturation = contenu('src/components/Facturation.jsx');
  assert.match(facturation, /CodesPromo/);
  const api = contenu('src/lib/api.js');
  assert.match(api, /chargerRendu/);
  assert.match(api, /promo\/codes\/generer\//);
});
