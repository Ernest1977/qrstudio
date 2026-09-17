/**
 * Rendus vérifiés par le serveur : QR artistique (PNG) et QR animé (GIF).
 *
 * Le studio dessine l'aperçu instantané en local ; ce module ne fabrique que des **URL** et des
 * requêtes. Deux raisons à ce partage :
 *  - la preuve de lisibilité (relecture du code après rendu) ne peut se faire que côté serveur, et un
 *    aperçu local qui ne coïnciderait pas avec le fichier livré serait un mensonge aimable ;
 *  - l'URL est partageable : `<img src>`, lien de borne Wi-Fi, `curl` de vérification.
 *
 * Les bornes ci-dessous sont celles de l'API (`apps/qr/art.py`, `apps/qr/animation.py`). Elles servent à
 * ne pas envoyer de requêtes refusées — pas à remplacer le serveur : si les deux divergent, c'est le
 * serveur qui a raison, et `test_les_bornes_de_rendu_sont_les_memes_que_celles_du_serveur` rougit.
 */

export const BORNES_RENDU = {
  taille: { min: 240, max: 1024, defaut: 512, pas: 16 },
  frames: { min: 8, max: 48, defaut: 24, pas: 1 },
  duree: { min: 40, max: 400, defaut: 90, pas: 10 },
  liser: { min: 0, max: 64, defaut: 24, pas: 2 },
};

export const STYLE_DEFAUT = 'naples';
export const ANIMATIONS = ['aucune', 'bordure'];

/** Nombre de frames qui restent immobiles : le scanner a besoin d'une fenêtre stable. */
export const PART_STATIQUE_MIN = 0.6;

export function borner(valeur, { min, max, defaut }) {
  const n = Number.parseInt(valeur, 10);
  if (!Number.isFinite(n)) return defaut;
  return Math.min(max, Math.max(min, n));
}

/**
 * URL du rendu serveur. `params.fmt` ∈ `png|svg|pdf|art|gif`.
 *
 * Les paramètres à leur valeur par défaut sont **omis** : une URL courte est une URL qu'on recopie sur
 * un flyer, et `?size=512&style=naples&frames=24…` répété mille fois ne rend le lien ni plus exact ni
 * plus cacheable.
 */
export function urlRendu(idQr, params = {}) {
  const fmt = params.fmt || 'art';
  const recherche = new URLSearchParams();
  const design = params.design || {};
  if (fmt !== 'art') recherche.set('fmt', fmt);

  const taille = borner(params.taille ?? design.taille, BORNES_RENDU.taille);
  if (taille !== BORNES_RENDU.taille.defaut) recherche.set('size', String(taille));

  if (fmt === 'art' || fmt === 'gif') {
    const style = params.style || design.art || STYLE_DEFAUT;
    if (style !== STYLE_DEFAUT) recherche.set('style', style);
  }
  if (fmt === 'gif') {
    const frames = borner(params.frames ?? design.frames, BORNES_RENDU.frames);
    const duree = borner(params.duree ?? design.duree, BORNES_RENDU.duree);
    const liser = borner(params.liser ?? design.liser, BORNES_RENDU.liser);
    if (frames !== BORNES_RENDU.frames.defaut) recherche.set('frames', String(frames));
    if (duree !== BORNES_RENDU.duree.defaut) recherche.set('duree', String(duree));
    if (liser !== BORNES_RENDU.liser.defaut) recherche.set('liser', String(liser));
    if (params.accroche) recherche.set('accroche', String(params.accroche).slice(0, 40));
  }
  const question = recherche.toString();
  return `/api/v1/qr/${encodeURIComponent(idQr)}/rendu/${question ? `?${question}` : ''}`;
}

/** Le PNG artistique à imprimer : style uniquement (pas de paramètres par défaut dans l'URL). */
export function urlRenduArt(idQr, { style, taille, design } = {}) {
  return urlRendu(idQr, { fmt: 'art', style, taille, design });
}

/** Le GIF animé pour les écrans. `animation === 'aucune'` doit être traité **avant** d'appeler ici. */
export function urlRenduAnime(idQr, params = {}) {
  return urlRendu(idQr, { ...params, fmt: 'gif' });
}

export function animationDemandee(design = {}) {
  return (design.animation || 'aucune') !== 'aucune';
}

/** Combien de frames resteront immobiles — affiché au client, jamais promis à l'aveugle. */
export function framesStatiques(frames) {
  const n = borner(frames, BORNES_RENDU.frames);
  return Math.max(1, Math.ceil(n * PART_STATIQUE_MIN));
}

/** En-têtes de preuve posés par le serveur. Absents = rendu non vérifié, et cela doit se voir. */
export function lirePreuve(reponse) {
  const brut = reponse?.headers?.get?.('X-Score') || reponse?.headers?.get?.('X-Montage');
  let detail = null;
  if (brut) {
    try {
      detail = JSON.parse(brut);
    } catch {
      detail = null; // un en-tête tronqué par un proxy ne doit pas faire échouer le téléchargement
    }
  }
  return {
    verifie: (reponse?.headers?.get?.('X-Lisibilite') || '') === 'verifiee',
    repli: reponse?.headers?.get?.('X-Style-Repli') || '',
    logoRetire: reponse?.headers?.get?.('X-Logo-Retire') === '1',
    logoReduit: reponse?.headers?.get?.('X-Logo-Reduit') === '1',
    detail,
  };
}

/** Une phrase, un sens : ce que le client doit comprendre avant d'imprimer 500 flyers. */
export function decrirePreuve(preuve, { format = 'art' } = {}) {
  if (!preuve) return '';
  if (format === 'gif') {
    const m = preuve.detail || {};
    const statiques = m.frames_statiques ?? 0;
    const total = m.frames ?? 0;
    return `GIF vérifié image par image : ${statiques}/${total} frames immobiles (${Math.round(
      (preuve.detail?.part_statique ?? 0) * 100
    )} %).`;
  }
  const s = preuve.detail || {};
  const parties = [];
  if (preuve.verifie) parties.push(' relu et décodé sur le serveur');
  else parties.push(' non relu sur ce serveur');
  if (s.taux_contraste) parties.push(`contraste ${s.taux_contraste}:1`);
  if (s.module_px) parties.push(`${s.module_px} px par module`);
  if (preuve.repli) parties.push('style replié sur le plus prudent');
  if (preuve.logoRetire) parties.push('logo retiré pour garantir la lecture');
  else if (preuve.logoReduit) parties.push('logo réduit pour garantir la lecture');
  return `QR${parties.join(' · ')}.`;
}

/**
 * L'URL de l'aperçu servi par le serveur, pour un contenu **non enregistré** (`/api/v1/qr/apercu/`).
 *
 * C'est une `<img src>` légale : GET, sans jeton, sans corps. Elle est volontairement identique dans sa
 * forme à l'URL de téléchargement — l'aperçu que le client voit doit être l'octet qu'il récupère, et une
 * divergence entre les deux se paie en flyers imprimés à 500 exemplaires.
 */
export function urlApercu({ fmt = 'art', contenu, style, taille, frames, duree, liser, accroche } = {}) {
  const recherche = new URLSearchParams();
  recherche.set('fmt', fmt === 'gif' ? 'gif' : 'art');
  recherche.set('payload', String(contenu || ''));
  const borneStyle = style || STYLE_DEFAUT;
  if (borneStyle !== STYLE_DEFAUT) recherche.set('style', borneStyle);
  const borneTaille = borner(taille, BORNES_RENDU.taille);
  if (borneTaille !== BORNES_RENDU.taille.defaut) recherche.set('size', String(borneTaille));
  if (recherche.get('fmt') === 'gif') {
    const n = borner(frames, BORNES_RENDU.frames);
    const d = borner(duree, BORNES_RENDU.duree);
    const l = borner(liser, BORNES_RENDU.liser);
    if (n !== BORNES_RENDU.frames.defaut) recherche.set('frames', String(n));
    if (d !== BORNES_RENDU.duree.defaut) recherche.set('duree', String(d));
    if (l !== BORNES_RENDU.liser.defaut) recherche.set('liser', String(l));
    if (accroche) recherche.set('accroche', String(accroche).slice(0, 40));
  }
  return `/api/v1/qr/apercu/?${recherche.toString()}`;
}

/** Un 402 de capacité est une occasion, pas une erreur : le panneau d'abonnement sait l'afficher. */
export function estCapaciteRefusee(erreur) {
  // `statut` est le champ d'`ErreurApi` (français, comme le reste du contrat) ; `status` est celui d'une
  // `Response` crue. Les deux se présentent selon l'endroit d'où vient l'objet.
  return erreur?.code === 'plan_required' || erreur?.statut === 402 || erreur?.status === 402;
}
