/**
 * Constructeurs de payload : contenu du formulaire → chaîne encodée dans le QR.
 *
 * Chaque type renvoie la chaîne à encoder, ou '' quand rien n'est saisi
 * (le studio affiche alors un message au lieu d'un QR vide).
 *
 * Corrections importantes par rapport à la version d'origine :
 *  - vCard : l'adresse (`ADR`) était calculée puis oubliée, et l'ordre des
 *    composants était faux (le code d'origine sautait le champ « BP ») ;
 *  - Wi-Fi : les caractères `;` `:` `,` `"` `\` doivent être échappés, sinon
 *    le réseau est lu tronqué ;
 *  - vCard/iCal : fins de ligne CRLF + repli des lignes longues (RFC 6350 §3.1,
 *    RFC 5545 §3.1) — l'original émettait du LF et des lignes > 75 octets ;
 *  - iCal : un `DTSTART` sans « Z » ni `TZID` est lu en heure locale par
 *    l'agenda importateur, soit un décalage aléatoire ; l'heure est ici
 *    convertie en UTC ;
 *  - le texte est encodé en UTF-8 (mode byte) explicitement.
 */

import { cleanHandle, cleanLinkedIn, absolutize } from './types.js';

const CRLF = '\r\n';
const isBlank = (v) => v == null || String(v).trim() === '';
/** Les données sont stockées brutes (saut de ligne possible dans le champ) : on tond à l'encodage. */
const raw = (v) => (typeof v === 'string' ? v.trim() : v ?? '');
const first = (...values) => values.find((v) => !isBlank(v)) ?? '';

/** Échappement vCard 3.1 : `\` `;` `,` et sauts de ligne → `\n` littéral. */
export function escapeVCard(value) {
  return String(value)
    .replace(/\\/g, '\\\\')
    .replace(/\r\n|\r|\n/g, '\\n')
    .replace(/;/g, '\\;')
    .replace(/,/g, '\\,');
}

/** Échappement iCalendar (RFC 5545 §3.3.11) : `\` `;` `,` et texte libre. */
export function escapeICal(value) {
  return String(value)
    .replace(/\\/g, '\\\\')
    .replace(/;/g, '\\;')
    .replace(/,/g, '\\,')
    .replace(/\r\n|\r|\n/g, '\\n');
}

/**
 * Échappement du format Wi-Fi (ZXing / ME卡) : `\` `;` `:` `"` (le `:` n'est
 * obligatoire que dans le SSID, on l'échappe partout : c'est toléré).
 */
export function escapeWifi(value, { inSsid = false } = {}) {
  const chars = inSsid ? ['\\', ';', ',', ':', '"'] : ['\\', ';', ',', '"'];
  return chars.reduce(
    (acc, ch) => acc.split(ch).join('\\' + ch),
    String(value ?? ''),
  );
}

/**
 * Repli des lignes longues à ≤ `limit` octets UTF-8, avec continuation
 * « CRLF + espace » (RFC 6350 §3.1). Découpe au niveau des caractères pour ne
 * jamais couper un emoji en deux.
 */
export function foldLines(text, limit = 74) {
  const byteLen = (s) => new TextEncoder().encode(s).length;
  return text
    .split(CRLF)
    .map((line) => {
      if (byteLen(line) <= limit) return line;
      const out = [];
      let current = '';
      for (const char of Array.from(line)) {
        if (byteLen(current + char) > limit) {
          out.push(current);
          current = ' ' + char;
          // la continuation démarre déjà avec l'espace : on la compte
          while (byteLen(current) > limit) {
            out.push(current.slice(0, 1));
            current = ' ' + current.slice(1);
          }
        } else {
          current += char;
        }
      }
      if (current) out.push(current);
      return out.join(CRLF);
    })
    .join(CRLF);
}

/** `2026-10-01T09:30` (heure locale du navigateur) → `20261001T073000Z`. */
export function toICalUtc(value) {
  if (isBlank(value)) return '';
  const stamp = new Date(value);
  if (Number.isNaN(stamp.getTime())) return '';
  const p = (n, l = 2) => String(n).padStart(l, '0');
  return (
    p(stamp.getUTCFullYear(), 4) +
    p(stamp.getUTCMonth() + 1) +
    p(stamp.getUTCDate()) +
    'T' +
    p(stamp.getUTCHours()) +
    p(stamp.getUTCMinutes()) +
    p(stamp.getUTCSeconds()) +
    'Z'
  );
}

/** `2026-10-01` → `20261001` (événement à la journée). */
export function toICalDate(value) {
  if (isBlank(value)) return '';
  return String(value).slice(0, 10).replace(/-/g, '');
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
export const isEmail = (value) => EMAIL_RE.test(String(value ?? '').trim());
export const isUrl = (value) => {
  const raw = absolutize(value);
  try {
    const u = new URL(raw);
    return Boolean(u.hostname) && u.hostname.includes('.');
  } catch {
    return false;
  }
};

/** UID iCalendar stable (dérivé du contenu) pour éviter les doublons d'import. */
function hashUid(text) {
  let h = 5381;
  for (let i = 0; i < text.length; i += 1) h = ((h << 5) + h + text.charCodeAt(i)) >>> 0;
  return h.toString(36);
}

export function buildVCard(input) {
  const d = Object.fromEntries(Object.entries(input ?? {}).map(([k, v]) => [k, raw(v)]));
  const name = [first(d.first), first(d.last)].filter(Boolean).join(' ');
  const lines = ['BEGIN:VCARD', 'VERSION:3.0'];
  // N = Nom;Prénom;Préfixe;Suffixe;Suffixes générés
  lines.push(`N:${escapeVCard(first(d.last))};${escapeVCard(first(d.first))};;;`);
  lines.push(`FN:${escapeVCard(name)}`);
  if (!isBlank(d.org)) lines.push(`ORG:${escapeVCard(d.org)}`);
  if (!isBlank(d.title)) lines.push(`TITLE:${escapeVCard(d.title)}`);
  if (!isBlank(d.tel)) lines.push(`TEL;TYPE=WORK,VOICE:${escapeVCard(d.tel)}`);
  if (!isBlank(d.cell)) lines.push(`TEL;TYPE=CELL:${escapeVCard(d.cell)}`);
  if (!isBlank(d.email)) lines.push(`EMAIL;TYPE=INTERNET,WORK:${escapeVCard(d.email.trim())}`);
  if (!isBlank(d.web)) lines.push(`URL:${escapeVCard(absolutize(d.web))}`);
  // ADR = BP;Adress2;Rue;Ville;Région;CP;Pays  ← l'original omettait cette ligne
  const address = [d.street, d.city, d.zip, d.country];
  if (address.some((v) => !isBlank(v))) {
    lines.push(
      'ADR;TYPE=WORK:;;' +
        [escapeVCard(first(d.street)), escapeVCard(first(d.city)), '', escapeVCard(first(d.zip)), escapeVCard(first(d.country))].join(';'),
    );
  }
  if (!isBlank(d.linkedin)) lines.push(`X-SOCIALPROFILE;TYPE=linkedin:${escapeVCard(absolutize(d.linkedin))}`);
  lines.push('END:VCARD');
  return foldLines(lines.join(CRLF));
}

export function buildWifi(input) {
  const d = Object.fromEntries(Object.entries(input ?? {}).map(([k, v]) => [k, raw(v)]));
  const ssid = escapeWifi(d.ssid, { inSsid: true });
  const type = ['WPA', 'WEP', 'nopass'].includes(d.enc) ? d.enc : 'WPA';
  const hidden = String(d.ssid ?? '').startsWith(' ');
  const parts = [`T:${type}`, `S:${ssid}`];
  if (type !== 'nopass' && !isBlank(d.pwd)) parts.push(`P:${escapeWifi(d.pwd)}`);
  if (hidden) parts.push('H:true');
  return `WIFI:${parts.join(';')};;`;
}

export function buildEvent(input) {
  const d = Object.fromEntries(Object.entries(input ?? {}).map(([k, v]) => [k, raw(v)]));
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//QR Coding//QR Coding FR//EN', 'CALSCALE:GREGORIAN', 'BEGIN:VEVENT'];
  const allDay = !isBlank(d.start) && /^\d{2}:\d{2}$/.test(String(d.start).slice(11)) === false;
  const startUtc = toICalUtc(d.start);
  const endUtc = toICalUtc(d.end);
  if (!isBlank(d.title)) lines.push(`SUMMARY:${escapeICal(d.title)}`);
  if (!isBlank(d.loc)) lines.push(`LOCATION:${escapeICal(d.loc)}`);
  if (allDay && !isBlank(d.start)) lines.push(`DTSTART;VALUE=DATE:${toICalDate(d.start)}`);
  else if (startUtc) lines.push(`DTSTART:${startUtc}`);
  if (allDay && !isBlank(d.end)) lines.push(`DTEND;VALUE=DATE:${toICalDate(d.end)}`);
  else if (endUtc) lines.push(`DTEND:${endUtc}`);
  lines.push(`UID:${hashUid([d.title, d.start, d.end, d.loc].join('|'))}@qr-coding`);
  lines.push('DTSTAMP:' + toICalUtc(new Date().toISOString()) || '');
  lines.push('END:VEVENT', 'END:VCALENDAR');
  return foldLines(lines.filter(Boolean).join(CRLF));
}

export function buildMailto(input) {
  const d = Object.fromEntries(Object.entries(input ?? {}).map(([k, v]) => [k, raw(v)]));
  const to = first(d.to).replace(/\s/g, '');
  if (!to) return '';
  const query = new URLSearchParams();
  if (!isBlank(d.sub)) query.set('subject', d.sub);
  if (!isBlank(d.body)) query.set('body', d.body);
  const q = query.toString();
  return `mailto:${to}${q ? `?${q.replace(/\+/g, '%20')}` : ''}`;
}

/**
 * Payload complet pour un type + ses données.
 * `extras.embedded` : data-URI d'un fichier importé (types « partage de fichier »).
 */
export function buildPayload(type, data, extras = {}) {
  const d = data ?? {};
  const trimmed = (key) => raw(d[key]);
  switch (type.kind) {
    case 'link':
      return absolutize(trimmed('url'));
    case 'social': {
      const handle = trimmed(type.handleKey ?? 'user');
      if (isBlank(handle)) return '';
      const value = type.id === 'linkedin' ? cleanLinkedIn(handle) : cleanHandle(handle);
      return value ? type.prefix + value : '';
    }
    case 'phone-link': {
      const digits = String(trimmed('num') ?? '').replace(/\D/g, '');
      return digits ? `https://wa.me/${digits}` : '';
    }
    case 'phone': {
      const num = String(trimmed('num') ?? '').replace(/[\s()\-.]/g, '');
      return num ? `tel:${num}` : '';
    }
    case 'sms': {
      const num = String(trimmed('num') ?? '').replace(/[\s()\-.]/g, '');
      if (!num) return '';
      return isBlank(d.msg) ? `smsto:${num}` : `smsto:${num}:${d.msg}`;
    }
    case 'mail':
      return buildMailto(d);
    case 'text':
      return String(d.txt ?? '').replace(/[ \t]+$/gm, (m) => m.trimEnd());
    case 'wifi':
      return isBlank(trimmed('ssid')) ? '' : buildWifi(d);
    case 'vcard':
      return buildVCard(d);
    case 'event':
      return buildEvent(d);
    case 'file':
      return first(extras.embedded, absolutize(d.url));
    default:
      return '';
  }
}
