/**
 * Types de contenu du studio.
 *
 * Écarts assumés avec la version d'origine :
 *  - les valeurs par défaut (`value`) alimentent désormais le state au montage
 *    (avant, elles ne vivaient que dans l'attribut `value` du HTML : le state
 *    restait vide tant qu'aucune touche n'était frappée) ;
 *  - `file.accept` sert enfin à valider le fichier importé (il était ignoré) ;
 *  - le champ `def`/`value` n'est plus dupliqué entre label et placeholder.
 */

/** Définition de champ — équivalent du helper `F()` de l'original. */
export const field = (key, label, type = 'text', value = '', placeholder = '', options = null) => ({
  key,
  label,
  type,
  value,
  placeholder,
  options,
});

/**
 * Nettoyage d'un pseudo : espaces supprimés (et `@` initial retiré après la
 * tonte — l'original testait `^@` avant de retirer les espaces, si bien que
 * «  @marie  » produisait instagram.com/@marie, une URL morte).
 */
export const cleanHandle = (value) => String(value).trim().replace(/\s/g, '').replace(/^@/, '');

/** Identifiant LinkedIn : tolère une URL complète collée telle quelle. */
export const cleanLinkedIn = (value) => {
  const raw = String(value).trim();
  const stripped = raw.replace(/^https?:\/\/([a-z]+\.)?linkedin\.com\/in\/?/i, '').replace(/^\/?in\//, '');
  return cleanHandle(stripped);
};

/** Ajoute le schéma `https://` oublié. L'original encodait la saisie telle quelle. */
export function absolutize(raw) {
  const value = String(raw).trim();
  if (!value) return '';
  if (/^[a-z][a-z0-9+.-]*:/i.test(value) || value.startsWith('//')) return value;
  return `https://${value}`;
}

/**
 * Fabrique un type « réseau social » : un pseudo → une URL.
 * `fieldLabel`/`placeholder` reprennent les libellés exacts de la version d'origine.
 */
const social = (id, ico, label, prefix, fieldLabel, placeholder) => ({
  id,
  ico,
  label,
  kind: 'social',
  prefix,
  handleKey: 'user',
  fields: [field('user', fieldLabel, 'text', '', placeholder)],
});

/** Types « partage de fichier » : une URL, ou un fichier intégré si minuscule. */
const sharing = (id, ico, label, file, fields) => ({ id, ico, label, kind: 'file', file, fields });

export const TYPES = [
  {
    id: 'url',
    ico: '🔗',
    label: 'URL',
    kind: 'link',
    fields: [field('url', 'Adresse du site', 'url', 'https://exemple.com', 'https://exemple.com/page')],
  },

  social('instagram', '📷', 'Instagram', 'https://instagram.com/', "Nom d'utilisateur", 'votre.pseudo'),
  social('facebook', '👍', 'Facebook', 'https://facebook.com/', 'Nom de la page / profil', 'mapage'),
  social('x', '𝕏', 'X (Twitter)', 'https://x.com/', "Nom d'utilisateur", 'votreprofil'),
  social('tiktok', '🎵', 'TikTok', 'https://tiktok.com/@', "Nom d'utilisateur (@)", '@votrepseudo'),
  social('youtube', '▶️', 'YouTube', 'https://youtube.com/@', 'Nom de la chaîne (@)', 'votrechaine'),
  social('telegram', '✈️', 'Telegram', 'https://t.me/', "Nom d'utilisateur", 'votrepseudo'),
  social('snapchat', '👻', 'Snapchat', 'https://snapchat.com/add/', "Nom d'utilisateur", 'votrepseudo'),
  social('pinterest', '📌', 'Pinterest', 'https://pinterest.com/', "Nom d'utilisateur", 'votrepseudo'),
  {
    id: 'linkedin',
    ico: '💼',
    label: 'LinkedIn',
    kind: 'social',
    prefix: 'https://linkedin.com/in/',
    handleKey: 'user',
    fields: [field('user', 'Identifiant du profil (in/)', 'text', '', 'marie-dupont')],
  },
  {
    id: 'whatsapp',
    ico: '💬',
    label: 'WhatsApp',
    kind: 'phone-link',
    fields: [field('num', 'Numéro avec indicatif pays', 'tel', '', '+33 6 12 34 56 78')],
  },

  {
    id: 'bcard',
    ico: '💼',
    label: 'Carte de visite',
    kind: 'vcard',
    fields: [
      field('first', 'Prénom', 'text', 'Marie'),
      field('last', 'Nom', 'text', 'Dupont'),
      field('title', 'Fonction', 'text', 'Directrice Commerciale'),
      field('org', 'Société', 'text', 'Dupont & Associés'),
      field('tel', 'Téléphone fixe', 'tel', '+33 1 23 45 67 89'),
      field('cell', 'Mobile', 'tel', '+33 6 12 34 56 78'),
      field('email', 'E-mail', 'email', 'marie@exemple.com'),
      field('web', 'Site web', 'url', 'https://exemple.com'),
      field('street', 'Adresse', 'text', '12 rue de Rivoli'),
      field('city', 'Ville', 'text', 'Paris'),
      field('zip', 'Code postal', 'text', '75001'),
      field('country', 'Pays', 'text', 'France'),
      field('linkedin', 'LinkedIn (URL)', 'url', '', 'https://linkedin.com/in/…'),
    ],
  },

  sharing('pdf', '📄', 'PDF', { accept: '.pdf,application/pdf', mime: 'pdf', hint: 'ou importez un PDF très léger' }, [
    field('url', 'URL du PDF hébergé', 'url', '', 'https://exemple.com/document.pdf'),
  ]),
  sharing('word', '📃', 'Word', { accept: '.doc,.docx', mime: 'word', hint: 'ou importez un Word très léger' }, [
    field('url', 'URL du document Word hébergé', 'url', '', 'https://exemple.com/document.docx'),
  ]),
  sharing('excel', '📊', 'Excel', { accept: '.xls,.xlsx', mime: 'excel', hint: 'ou importez un Excel très léger' }, [
    field('url', 'URL du fichier Excel hébergé', 'url', '', 'https://exemple.com/tableau.xlsx'),
  ]),
  sharing('image', '🖼️', 'Image', { accept: 'image/png,image/jpeg', mime: 'image', hint: 'ou importez une image très légère' }, [
    field('url', "URL de l'image hébergée", 'url', '', 'https://exemple.com/photo.jpg'),
    field('desc', 'Description / légende (optionnel — affichée sous le QR)', 'text', '', 'Ex : Équipe au salon 2026'),
  ]),

  {
    id: 'text',
    ico: '📝',
    label: 'Texte',
    kind: 'text',
    fields: [field('txt', 'Texte libre', 'textarea', 'Bonjour le monde !')],
  },
  {
    id: 'wifi',
    ico: '📶',
    label: 'Wi-Fi',
    kind: 'wifi',
    fields: [
      field('ssid', 'Nom du réseau (SSID)', 'text', 'MaBox_5GHz'),
      field('enc', 'Sécurité', 'select', 'WPA', '', ['WPA', 'WEP', 'nopass']),
      field('pwd', 'Mot de passe', 'password'),
    ],
  },
  {
    id: 'tel',
    ico: '📞',
    label: 'Tél.',
    kind: 'phone',
    fields: [field('num', 'Numéro de téléphone', 'tel', '+33123456789')],
  },
  {
    id: 'sms',
    ico: '💬',
    label: 'SMS',
    kind: 'sms',
    fields: [
      field('num', 'Numéro de destination', 'tel', '+33612345678'),
      field('msg', 'Message', 'textarea'),
    ],
  },
  {
    id: 'mail',
    ico: '✉️',
    label: 'E-mail',
    kind: 'mail',
    fields: [
      field('to', 'Destinataire', 'email', 'contact@exemple.com'),
      field('sub', 'Objet', 'text', 'Demande de contact'),
      field('body', 'Message', 'textarea'),
    ],
  },
  {
    id: 'event',
    ico: '📅',
    label: 'Événement',
    kind: 'event',
    fields: [
      field('title', 'Titre', 'text', 'Lancement produit'),
      field('loc', 'Lieu', 'text', 'Paris'),
      field('start', 'Début', 'datetime-local'),
      field('end', 'Fin', 'datetime-local'),
    ],
  },
];

export const TYPE_COUNT = TYPES.length;

export const getType = (id) => TYPES.find((t) => t.id === id) ?? TYPES[0];

/** Valeurs initiales d'un type, prêtes à entrer dans le state. */
export function defaultData(type) {
  return Object.fromEntries(
    type.fields.map((f) => [
      f.key,
      f.type === 'select' ? (f.options?.includes(f.value) ? f.value : f.options?.[0] ?? '') : f.value,
    ]),
  );
}

/** State « données » initial de tous les types (sert au localStorage et au reset). */
export function initialDataByType() {
  return Object.fromEntries(TYPES.map((t) => [t.id, defaultData(t)]));
}
