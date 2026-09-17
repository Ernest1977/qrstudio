/**
 * Rendu SVG du QR code : grille de modules → chaîne SVG vectorielle.
 *
 * Cœur de la correction n°1 : dans la version d'origine, les trois repères
 * (finder patterns) étaient dessinés avec `fill="none" stroke="…"` d'une
 * largeur d'une cellule, centré sur le contour des modules. Le tracé débordait
 * donc de ½ cellule sur la ligne de séparation et dans la zone de quiétude.
 * Résultat mesuré : les QR produits ne sont plus décodés (zxing échoue sur
 * 100 % des rendus testés), alors que la même grille dessinée en formes pleines
 * passe. Les repères sont ici peints en aires pleines, alignés sur la grille.
 *
 * Autres écarts assumés :
 *  - les motifs d'alignement (v ≥ 2) rejoignent la ligne de synchronisation
 *    dans le lot des modules forcés en carré (l'original ne protégeait que
 *    les timings) ;
 *  - le trou du logo est découpé dans la grille plutôt que masqué après coup ;
 *  - la légende n'est plus injectée dans une chaîne à la main, elle passe par
 *    `escapeXml`.
 */

export const MODULE_STYLES = ['square', 'round', 'rounded', 'diamond'];
export const FINDER_STYLES = ['square', 'rounded', 'circle'];
/** Taille d'un module dans le SVG de base (l'export PNG multiplie ensuite). */
export const CELL = 8;

const round2 = (n) => Math.round(n * 100) / 100;
const isFinderZone = (r, c, size) =>
  (r < 7 && c < 7) || (r < 7 && c >= size - 7) || (r >= size - 7 && c < 7);
const isTiming = (r, c) => r === 6 || c === 6;
export const versionFromModules = (size) => Math.max(1, Math.round((size - 17) / 4));

/** Modules de données, stylés. */
function moduleShape(x, y, cell, style) {
  switch (style) {
    case 'round':
      return `<circle cx="${round2(x + cell / 2)}" cy="${round2(y + cell / 2)}" r="${round2(cell / 2)}"/>`;
    case 'rounded':
      return `<rect x="${round2(x)}" y="${round2(y)}" width="${cell}" height="${cell}" rx="${round2(cell * 0.32)}"/>`;
    case 'diamond': {
      const m = cell / 2;
      return `<path d="M${round2(x + m)} ${round2(y)}L${round2(x + cell)} ${round2(y + m)}L${round2(x + m)} ${round2(y + cell)}L${round2(x)} ${round2(y + m)}Z"/>`;
    }
    default:
      return `<rect x="${round2(x)}" y="${round2(y)}" width="${cell}" height="${cell}"/>`;
  }
}

/**
 * Repère (finder) : anneau 7×7 + centre 3×3, en aires pleines empilées
 * (pas de stroke → pas de débordement antialiasé sur le séparateur).
 */
export function finderShape(x, y, cell, shape, ringColor, dotColor, background) {
  // Echappees ici aussi : la fonction est exportee et peut etre appelee avec des valeurs qui ne sont
  // pas passees par le controle de `renderQrSvg`.
  ringColor = escapeXml(ringColor);
  dotColor = escapeXml(dotColor);
  background = escapeXml(background);
  const outer = 7 * cell;
  const ix = x + 2 * cell;
  const iy = y + 2 * cell;
  const inner = 3 * cell;

  if (shape === 'circle') {
    const cx = round2(x + outer / 2);
    const cy = round2(y + outer / 2);
    return (
      `<circle cx="${cx}" cy="${cy}" r="${round2(outer / 2)}" fill="${ringColor}"/>` +
      `<circle cx="${cx}" cy="${cy}" r="${round2(outer / 2 - cell)}" fill="${background}"/>` +
      `<circle cx="${cx}" cy="${cy}" r="${round2(inner / 2)}" fill="${dotColor}"/>`
    );
  }

  const radius = shape === 'rounded' ? 2 * cell : 0;
  const innerRadius = shape === 'rounded' ? cell : 0;
  const holeRadius = shape === 'rounded' ? cell : 0;
  return (
    `<rect x="${round2(x)}" y="${round2(y)}" width="${outer}" height="${outer}" rx="${radius}" fill="${ringColor}"/>` +
    `<rect x="${round2(x + cell)}" y="${round2(y + cell)}" width="${outer - 2 * cell}" height="${outer - 2 * cell}" rx="${holeRadius}" fill="${background}"/>` +
    `<rect x="${round2(ix)}" y="${round2(iy)}" width="${inner}" height="${inner}" rx="${innerRadius}" fill="${dotColor}"/>`
  );
}

/**
 * Centre des motifs d'alignement par version (ISO/IEC 18004, tableau 9).
 * Table littérale : plus sûr qu'une formule réinventée, et c'est précisément ce
 * que l'original ignorait — en style « rond » ou « diamant », ces motifs
 * devenaient des disques isolés que le lecteur peut confondre avec des données.
 */
const ALIGNMENT_CENTERS = [
  [], [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34], [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50],
  [6, 30, 54], [6, 32, 58], [6, 34, 62], [6, 26, 46, 66], [6, 26, 48, 70], [6, 26, 50, 74], [6, 30, 54, 78],
  [6, 30, 56, 82], [6, 30, 58, 86], [6, 34, 62, 90], [6, 28, 50, 72, 94], [6, 26, 50, 74, 98],
  [6, 30, 54, 78, 102], [6, 28, 54, 80, 106], [6, 32, 58, 84, 110], [6, 30, 58, 86, 114],
  [6, 28, 54, 80, 106, 132], [6, 26, 52, 78, 104, 130], [6, 30, 56, 82, 108, 134], [6, 34, 60, 86, 112, 138],
  [6, 30, 58, 86, 114, 142], [6, 34, 62, 90, 118, 146], [6, 30, 56, 82, 108, 134, 160],
  [6, 34, 60, 86, 112, 138, 164], [6, 30, 54, 78, 102, 126, 150], [6, 24, 50, 76, 102, 128, 154],
  [6, 28, 54, 80, 106, 132, 158], [6, 32, 58, 84, 110, 136, 162], [6, 26, 54, 82, 110, 138, 166],
  [6, 30, 58, 86, 114, 142, 170],
];

export function alignmentCenters(size) {
  const version = versionFromModules(size);
  if (version < 2) return [];
  return ALIGNMENT_CENTERS[version] ?? fallbackCenters(size, version);
}

/** Repli algorithmique (théoriquement inutilisé : la table couvre v1 → v40). */
function fallbackCenters(size, version) {
  const last = size - 7;
  const count = Math.min(7, Math.max(2, Math.round(version / 5) + 2));
  const step = Math.max(8, Math.round((last - 6) / count)) * 2;
  const centers = [last];
  while (centers[0] - step >= 14) centers.unshift(centers[0] - step);
  if (version >= 7) centers.unshift(6);
  return centers.filter((v, i, arr) => i === 0 || v !== arr[i - 1]);
}

function buildAlignmentMask(size) {
  const centers = alignmentCenters(size);
  if (!centers.length) return () => false;
  const last = size - 7;
  const cells = new Set();
  for (const cy of centers) {
    for (const cx of centers) {
      // les trois coins portent un repère : (6,6), (6,last), (last,6) sont exclus
      if ((cx === 6 && cy === 6) || (cx === 6 && cy === last) || (cx === last && cy === 6)) continue;
      for (let r = cy - 2; r <= cy + 2; r += 1) for (let c = cx - 2; c <= cx + 2; c += 1) cells.add(`${r}:${c}`);
    }
  }
  return (r, c) => cells.has(`${r}:${c}`);
}

/**
 * Surface maximale (fraction des modules) qu'un logo peut masquer pour chaque
 * niveau de correction. Ce ne sont PAS les 7/15/25/30 % théoriques : un logo
 * détruit des blocs de codewords entiers et contigus, ce qui est bien plus
 * coûteux que des erreurs dispersées. Les valeurs ci-dessous sont calées sur
 * des décodages réels (zxing) : au-delà, le code ne se lit plus.
 * L'original autorisait 35 % de logo avec une simple correction M.
 */
export const ECC_LOGO_BUDGET = { L: 0.02, M: 0.05, Q: 0.08, H: 0.11 };

/**
 * @param {{size:number, get:(r:number,c:number)=>boolean, version?:number}} matrix
 * @param {object} options
 * @returns {{svg:string, size:number, height:number, version:number, warnings:string[], logoCoverage:number}|null}
 */
export function renderQrSvg(matrix, options = {}) {
  if (!matrix?.size) return null;
  const {
    style = 'square',
    finderStyle = 'square',
    moduleColor: moduleColorBrut = '#111827',
    background: backgroundBrut = '#ffffff',
    finderColor: finderColorBrut = null,
    margin = Math.round(4 * CELL),
    cell = CELL,
    logo: logoBrut = null,
    logoPercent = 22,
    caption = '',
    ecc = 'M',
  } = options;

  const size = matrix.size;
  const warnings = [];

  // Aucune valeur venue d'ailleurs qu'une constante du fichier n'entre telle quelle dans le SVG :
  // les couleurs sont validees au format (#rgb → #rrggbbaa) et le logo doit etre une URL d'image.
  const moduleColor = couleurValide(moduleColorBrut, '#111827');
  const background = couleurValide(backgroundBrut, '#ffffff');
  const finderColor = finderColorBrut ? couleurValide(finderColorBrut, background) : null;
  const logo = urlImageValide(logoBrut);
  if (logoBrut && !logo) warnings.push('Logo ignoré : ce n’est pas une URL d’image (data: ou https:).');

  const quiet = Math.max(0, Math.round(Number.isFinite(+margin) ? +margin : 0));
  const square = size * cell + quiet * 2;
  const get = (r, c) => Boolean(matrix.get ? matrix.get(r, c) : matrix[r]?.[c]);

  // Logo : carré centré. On le réduit s'il menace les repères/timings, puis on
  // le fait redescendre jusqu'à ce qu'il masque moins de modules foncés que ce
  // que le niveau de correction peut rétablir — au-delà, aucun lecteur ne
  // décode (bug n°5 de l'original : 35 % de logo tolérés avec une correction M).
  let logoBox = null;
  let logoCoverage = 0;
  if (logo) {
    let usable = 0;
    for (let r = 0; r < size; r += 1) for (let c = 0; c < size; c += 1) if (!isFinderZone(r, c, size)) usable += 1;
    const dark = getDarkCount(get, size);

    const room = Math.max(cell * 4, (size - 15) * cell);
    let side = Math.round(square * (clamp(+logoPercent || 22, 10, 45) / 100));
    if (side > room) warnings.push('Logo réduit : il aurait recouvert la zone des repères ou des timings.');
    side = clamp(side, cell * 4, room);

    const budget = ECC_LOGO_BUDGET[ecc] ?? ECC_LOGO_BUDGET.M;
    const asked = side;
    const coverageAt = (s) => coverageOf(logoCoverageBox(s, square, cell, size, quiet), get, size, usable);
    while (side > cell * 4 && coverageAt(side) > budget) side -= cell;
    logoCoverage = coverageAt(side);
    if (side < asked) {
      warnings.push(
        `Logo réduit de ${Math.round((asked / square) * 100)} % à ${Math.round((side / square) * 100)} % : ` +
          `à la taille demandée il masquait ${Math.round(coverageAt(asked) * 100)} % de la trame, plus que la correction ${ecc} ne peut le rétablir.`,
      );
    }
    if (logoCoverage > budget) {
      warnings.push(
        `Même réduit au minimum, ce logo masque ${Math.round(logoCoverage * 100)} % de la trame (budget ${Math.round(budget * 100)} % en ${ecc}) : ce QR risque de ne pas se lire — raccourcissez le contenu ou retirez le logo.`,
      );
    }

    const offset = (square - side) / 2;
    logoBox = { x: offset, y: offset, size: side, pad: Math.round(side * 0.12) };
  }

  const underLogo = (x, y, s) =>
    Boolean(logoBox) &&
    x < logoBox.x + logoBox.size + logoBox.pad &&
    x + s > logoBox.x - logoBox.pad &&
    y < logoBox.y + logoBox.size + logoBox.pad &&
    y + s > logoBox.y - logoBox.pad;

  const isAlignment = buildAlignmentMask(size);
  let body = '';
  for (let r = 0; r < size; r += 1) {
    for (let c = 0; c < size; c += 1) {
      if (!get(r, c)) continue;
      if (isFinderZone(r, c, size)) continue;
      const x = quiet + c * cell;
      const y = quiet + r * cell;
      if (underLogo(x, y, cell)) continue;
      if (isTiming(r, c) || isAlignment(r, c) || style === 'square') body += moduleShape(x, y, cell, 'square');
      else body += moduleShape(x, y, cell, style);
    }
  }

  let finders = '';
  for (const [fr, fc] of [[0, 0], [0, size - 7], [size - 7, 0]]) {
    finders += finderShape(quiet + fc * cell, quiet + fr * cell, cell, finderStyle, finderColor || moduleColor, moduleColor, background);
  }

  const totalHeight = square + (caption ? Math.round(square * 0.12) : 0);
  let svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${square}" height="${totalHeight}" viewBox="0 0 ${square} ${totalHeight}">` +
    `<rect width="${square}" height="${totalHeight}" fill="${background}"/>` +
    `<g fill="${moduleColor}">${body}${finders}</g>`;

  if (logoBox) {
    const { x, y, size: side, pad } = logoBox;
    svg +=
      `<rect x="${round2(x - pad)}" y="${round2(y - pad)}" width="${round2(side + pad * 2)}" height="${round2(side + pad * 2)}" rx="${round2(side * 0.16)}" fill="${background}"/>` +
      `<image x="${round2(x)}" y="${round2(y)}" width="${side}" height="${side}" href="${logo}"/>`;
  }
  if (caption) {
    const fontSize = Math.max(12, Math.round(square * 0.055));
    svg +=
      `<text x="${round2(square / 2)}" y="${square + Math.round(fontSize * 0.95)}" text-anchor="middle" ` +
      `font-family="Segoe UI, system-ui, Arial, sans-serif" font-size="${fontSize}" font-weight="600" fill="${moduleColor}">` +
      escapeXml(caption) +
      `</text>`;
  }
  svg += '</svg>';

  return {
    svg,
    size: square,
    height: totalHeight,
    version: versionFromModules(size),
    warnings,
    logoCoverage,
    logoPercent: logoBox ? Math.round((logoBox.size / square) * 100) : 0,
    darkModules: getDarkCount(get, size),
  };
}

export const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

/** Boîte du logo (avec sa bordure), dans le repère du SVG. */
function logoCoverageBox(side, square, cell, size, quiet) {
  const offset = (square - side) / 2;
  const pad = Math.round(side * 0.12);
  return { x0: offset - pad, y0: offset - pad, x1: offset + side + pad, y1: offset + side + pad, cell, quiet };
}

/** Fraction des modules (zone de données) masqués par cette boîte. */
function coverageOf(box, get, size, usable) {
  if (!usable) return 0;
  let covered = 0;
  for (let r = 0; r < size; r += 1) {
    for (let c = 0; c < size; c += 1) {
      if (!get(r, c) || isFinderZone(r, c, size)) continue;
      const x = box.quiet + c * box.cell;
      const y = box.quiet + r * box.cell;
      if (x < box.x1 && x + box.cell > box.x0 && y < box.y1 && y + box.cell > box.y0) covered += 1;
    }
  }
  return covered / usable;
}

/** Nombre de modules foncés (sert au comptage de la jauge). */
function getDarkCount(get, size) {
  let dark = 0;
  for (let r = 0; r < size; r += 1) for (let c = 0; c < size; c += 1) if (get(r, c)) dark += 1;
  return dark;
}

/**
 * Les valeurs qui entrent dans un attribut du SVG sont validees, pas seulement echappees.
 *
 * `Preview.jsx` injecte ce SVG avec `dangerouslySetInnerHTML` : c'est le seul endroit du front ou du HTML
 * construit a la main atterrit dans le DOM. Les couleurs viennent de `<input type=color>` et le logo d'un
 * `FileReader`, donc *aujourd'hui* rien d'exterieur n'y a acces — mais un composant qui ne tient que tant
 * que l'appelant est sage est un composant qui cassera au premier theme partage par lien. On refuse donc
 * la valeur, on ne l'echappe pas « en esperant ».
 */
const COULEUR = /^#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
// Ce qui est interdit ici n'est pas le protocole : c'est tout ce qui peut fermer un attribut.
const CARACTERES_INTERDITS = /["'<>`\\\s]/;

export const couleurValide = (valeur, defaut) => {
  const v = typeof valeur === 'string' ? valeur.trim() : '';
  return COULEUR.test(v) ? v : defaut;
};

export const urlImageValide = (valeur) => {
  const v = typeof valeur === 'string' ? valeur.trim() : '';
  if (!v || CARACTERES_INTERDITS.test(v)) return null;
  // `data:` limité aux sous-types image : un `data:text/html` ou un `javascript:` n'a rien à faire
  // dans un `href`. Les SVG passent (le champ « logo » du studio les accepte) ; dans une balise
  // `<image>`, un SVG est rendu en mode sans script, ce qui suffit ici.
  if (/^data:image\/[a-z0-9.+-]+[,;]/i.test(v)) return v;
  if (/^https?:\/\/\S+$/i.test(v)) return v;
  return null;
};

export const escapeXml = (value) =>
  String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
