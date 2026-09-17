/**
 * Accès à l'API Django (`/api/v1/…`).
 *
 * Trois règles, toutes dictées par ce que le backend renvoie réellement :
 *
 * 1. **URLs relatives.** On appelle `/api/v1/...`, jamais `http://localhost:8000/...`. Derrière un
 *    proxy d'aperçu ou sur `qrstudio.kamcofarm.com`, « localhost » désignerait la machine du visiteur ;
 *    le proxy Vite (`vite.config.js`) et nginx font le travail en amont.
 * 2. **Une erreur API a une forme.** `{ "error": { "code", "message" } }`, parfois `details` pour les
 *    champs. On n'affiche donc jamais un objet JSON brut : on sort `message`, et `code` sert à brancher
 *    un comportement (rejet de CSRF, palier déjà actif, fournisseur non configuré…).
 * 3. **CSRF.** DRF n'exige le jeton que quand une session authentifiée est présente (les POST à froid
 *    n'en ont pas besoin). Le jeton vient de la réponse de `login` (le backend le rotate à la
 *    connexion : un jeton lu avant vaudrait un 403), puis du cookie `csrftoken` après rechargement.
 */

const BASE = '/api/v1';
const METHODES_SURES = new Set(['GET', 'HEAD', 'OPTIONS']);

/** Jeton en mémoire : prioritaire sur le cookie, car `login` en renvoie un neuf. */
let jetonEnMemoire = '';

export class ErreurApi extends Error {
  constructor(message, { code = 'erreur', statut = 0, details = null } = {}) {
    super(message);
    this.name = 'ErreurApi';
    this.code = code;
    this.statut = statut;
    this.details = details;
  }
}

export function definirJetonCsrf(valeur) {
  if (valeur) jetonEnMemoire = valeur;
}

function jetonCsrf() {
  if (jetonEnMemoire) return jetonEnMemoire;
  const trouve = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return trouve ? decodeURIComponent(trouve[1]) : '';
}

/** Un en-tête `Accept` explicite : sinon DRF négocie la page HTML du navigateur applicatif. */
function enTetes(avecJeton) {
  const t = { Accept: 'application/json' };
  if (avecJeton) t['X-CSRFToken'] = avecJeton;
  return t;
}

async function lire(reponse) {
  const texte = await reponse.text();
  if (!texte) return null;
  try {
    return JSON.parse(texte);
  } catch {
    // Une 500 de proxy, une page de login Django, du HTML d'erreur… : du texte, pas du JSON.
    return { _brut: texte.slice(0, 300) };
  }
}

function lever(corps, statut) {
  const erreur = corps && corps.error ? corps.error : null;
  const code = erreur ? erreur.code || 'erreur' : 'reponse_inattendue';
  const message = erreur
    ? erreur.message || 'Le service a refusé la demande.'
    : `Réponse inattendue du service (${statut}).`;
  throw new ErreurApi(message, { code, statut, details: erreur ? erreur.details || null : corps && corps._brut ? { brut: corps._brut } : null });
}

/**
 * Un appel, une promesse, une erreur typée.
 *
 * `on403Csrf` : si le jeton a expiré (session ouverte dans un autre onglet), on relit le cookie — et
 * on retente **une** fois. Deux tentatives et plus serait rejouer une écriture non idempotente.
 */
export async function appeler(chemin, { methode = 'GET', corps, sansJeton = false } = {}) {
  const envoyer = async (jeton) => {
    const options = {
      method: methode,
      credentials: 'same-origin',
      headers: enTetes(jeton || undefined),
    };
    if (corps !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(corps);
    }
    const reponse = await fetch(BASE + chemin, options);
    return { reponse, corps: await lire(reponse) };
  };

  let { reponse, corps: donnees } = await envoyer(sansJeton || METHODES_SURES.has(methode) ? '' : jetonCsrf());

  if (reponse.status === 403 && !METHODES_SURES.has(methode) && !sansJeton) {
    const relate = jetonCsrf();
    if (relate && relate !== '') {
      ({ reponse, corps: donnees } = await envoyer(relate));
    }
  }

  if (!reponse.ok) lever(donnees, reponse.status);
  if (reponse.status === 204) return null;
  return donnees;
}

export const api = {
  config: () => appeler('/auth/config'),
  moi: () => appeler('/auth/me'),
  connexion: (email, password) => appeler('/auth/login', { methode: 'POST', corps: { email, password } }),
  inscription: (corpsInscription) => appeler('/auth/register', { methode: 'POST', corps: corpsInscription, sansJeton: true }),
  deconnexion: () => appeler('/auth/logout', { methode: 'POST' }),
  verifier: (email, code) => appeler('/auth/verify', { methode: 'POST', corps: { email, code }, sansJeton: true }),
  renvoyerCode: (email) => appeler('/auth/verify/resend', { methode: 'POST', corps: { email }, sansJeton: true }),
  etat: () => appeler('/billing/etat'),
  checkout: (palier) => appeler('/billing/checkout', { methode: 'POST', corps: { palier } }),
  portail: () => appeler('/billing/portail', { methode: 'POST', corps: {} }),
  mobileDemande: (palier, telephone) => appeler('/billing/mobile/demande', { methode: 'POST', corps: { palier, telephone } }),
  mobileEtat: (reference) => appeler(`/billing/mobile/${encodeURIComponent(reference)}`),
  styles: () => appeler('/qr/styles'),
};

/**
 * Récupère un **binaire** rendu par le serveur (PNG artistique, GIF animé).
 *
 * Séparé de `appeler` parce que le corps n'est pas du JSON : le chemin d'erreur est le même (un 402 de
 * capacité doit ouvrir le panneau d'abonnement depuis un bouton « télécharger », pas afficher un blob
 * corrompu), mais la lecture du corps est différente.
 */
export async function chargerRendu(url) {
  const reponse = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'image/*,*/*' } });
  if (!reponse.ok) {
    lever(await lire(reponse), reponse.status);
  }
  return { blob: await reponse.blob(), reponse };
}

/** Codes promo d'une campagne (palier Entreprise) — la caisse du client, pas notre facturation. */
export const codesPromo = {
  liste: (filtre = {}) => {
    const q = new URLSearchParams(Object.entries(filtre).filter(([, v]) => v !== undefined && v !== ''));
    const s = q.toString();
    return appeler(`/promo/codes/${s ? `?${s}` : ''}`);
  },
  creer: (corps) => appeler('/promo/codes/', { methode: 'POST', corps }),
  generer: (corps) => appeler('/promo/codes/generer/', { methode: 'POST', corps }),
  verifier: (code) => appeler('/promo/codes/verifier/', { methode: 'POST', corps: { code } }),
  prolonger: (id, jours) => appeler(`/promo/codes/${id}/prolonger/`, { methode: 'POST', corps: { jours } }),
  supprimer: (id) => appeler(`/promo/codes/${id}/`, { methode: 'DELETE' }),
};
