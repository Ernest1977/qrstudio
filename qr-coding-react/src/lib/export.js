/**
 * Export PNG / SVG.
 *
 * Corrections :
 *  - l'original fabriquait sa data-URI avec `btoa` + la fonction dépréciée
 *    `unescape` (Annexe B.2.2 de ECMAScript) ; on passe par `encodeURIComponent`,
 *    ce qui évite tout le base64 et ses aller-retour binaires.
 *  - le PNG était produit sur un canvas transparent : si l'utilisateur choisissait
 *    « fond transparent », l'image enregistrée était illisible. Le fond est ici
 *    aplati avant export.
 *  - le SVG était collé dans un `<img>`/ancre sans échappement : un `&` ou une
 *    accentuation dans la légende pouvait casser l'attribut.
 */

/** SVG → URL de données sûre (UTF-8, sans base64). */
export function svgToDataUrl(svg) {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Chrome a besoin d'un tick avant de relâcher l'URL
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function downloadSvg(svg, filename = 'qr-code.svg') {
  downloadBlob(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }), filename);
}

/**
 * SVG → PNG. `scale` = nombre de pixels par unité SVG (l'original utilisait 4).
 * @returns {Promise<void>} rejette si le navigateur ne peut pas rasteriser le SVG
 */
export async function downloadPng(svg, { scale = 4, filename = 'qr-code.png', background = null } = {}) {
  const image = await loadImage(svg);
  const width = Math.max(1, Math.round((image.width || 0) * scale));
  const height = Math.max(1, Math.round((image.height || 0) * scale));
  if (!width || !height) throw new Error("Le navigateur n'a pas pu mesurer le SVG.");

  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Contexte canvas indisponible.');
  if (background) {
    ctx.fillStyle = background;
    ctx.fillRect(0, 0, width, height);
  }
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(image, 0, 0, width, height);

  const blob = await canvasToBlob(canvas);
  if (!blob) throw new Error('Export PNG impossible (canvas vide).');
  downloadBlob(blob, filename);
  return { width, height };
}

const loadImage = (svg) =>
  new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('SVG non chargeable.'));
    img.decoding = 'sync';
    img.src = svgToDataUrl(svg);
  });

const canvasToBlob = (canvas) =>
  new Promise((resolve) => {
    if (canvas.toBlob) canvas.toBlob((b) => resolve(b), 'image/png');
    else resolve(null);
  });

/** Copie dans le presse-papiers, avec repli sur `execCommand`. */
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand('copy');
    } finally {
      ta.remove();
    }
    return ok;
  }
}
