/**
 * Encodage : payload → grille de modules.
 *
 * L'original utilisait `qrcode-generator` chargé depuis un CDN, avec
 * `qrcode(0, 'M')` et un `try/catch` qui avalait l'exception pour afficher un
 * message. Ici l'encodeur est une dépendance npm (pas de réseau requis) et le
 * dépassement de capacité remonte une erreur explicite, taille comprise.
 */

import { create } from 'qrcode';

/** Niveau de correction choisi pour laisser passer un logo (≈ 22 %). */
export const ERROR_CORRECTION = 'M';
export const ECC_LEVELS = ['L', 'M', 'Q', 'H'];

/** Capacité utile en mode byte (octets) par version, correction M. */
export const MAX_QR_BYTES = 2331;

export const byteLength = (text) => new TextEncoder().encode(String(text ?? '')).length;

/**
 * @returns {{size:number, get:(r:number,c:number)=>boolean, version:number, bytes:number}}
 * @throws {{code:'EMPTY'|'TOO_LONG', message:string, bytes:number}}
 */
export function createMatrix(payload, options = {}) {
  const text = String(payload ?? '');
  const bytes = byteLength(text);
  if (!text) throw error('EMPTY', 'Aucun contenu à encoder — remplissez le formulaire.', 0);

  try {
    const qr = create(text, {
      errorCorrectionLevel: options.ecc ?? ERROR_CORRECTION,
      version: options.version,
      maskPattern: options.mask,
    });
    return {
      size: qr.modules.size,
      get: (r, c) => Boolean(qr.modules.get(r, c)),
      version: qr.version ?? versionFromSize(qr.modules.size),
      bytes,
    };
  } catch (err) {
    if (/too long|code length overflow|too big/i.test(String(err?.message))) {
      throw error(
        'TOO_LONG',
        `Contenu trop volumineux pour un QR code (${bytes} octets, max ${MAX_QR_BYTES} en correction ${options.ecc ?? ERROR_CORRECTION}). Raccourcissez le texte ou utilisez une URL courte.`,
        bytes,
      );
    }
    throw err;
  }
}

function error(code, message, bytes) {
  const e = new Error(message);
  e.code = code;
  e.bytes = bytes;
  return e;
}

export const versionFromSize = (size) => Math.max(1, Math.round((size - 17) / 4));
