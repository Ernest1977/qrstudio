import { TYPE_COUNT } from './lib/types.js';
import { MAX_QR_BYTES } from './lib/qrcode.js';

/**
 * Textes de la page. Contrairement à la version d'origine (tout codé en dur
 * dans le HTML), ils sont centralisés — et surtout : la promesse « partage de
 * fichiers PDF / Word / Excel » est nuancée, parce qu'un QR ne peut pas héberger
 * un document, seulement ~2 Ko de données (voir ANALYSE.md, bug n°4).
 */

export const HERO_TITLE = 'Créez des QR codes élégants en quelques secondes';

export const HERO_TEXT =
  'URL, carte de visite, 10 réseaux sociaux, Wi-Fi, SMS, e-mail, événement, texte libre… Personnalisez couleurs, formes et logo, puis exportez en haute qualité. Gratuit, sans inscription, tout reste dans votre navigateur.';

export const FEATURES = [
  {
    ico: '📱',
    title: '10 réseaux sociaux',
    text: 'Instagram, Facebook, X, LinkedIn, TikTok, YouTube, WhatsApp, Telegram, Snapchat et Pinterest en un clic.',
  },
  {
    ico: '💼',
    title: 'Carte de visite digitale',
    text: 'vCard 3.0 complète : nom, fonction, société, téléphones, e-mail, site web, adresse et LinkedIn — avec le champ ADR correctement rempli.',
  },
  {
    ico: '🔗',
    title: `${TYPE_COUNT} types de contenu`,
    text: 'URL, texte, Wi-Fi (échappé), téléphone, SMS, e-mail, événement iCalendar en UTC, et partage de lien vers un PDF, Word, Excel ou une image.',
  },
  {
    ico: '🎨',
    title: 'Design avancé',
    text: 'Couleurs, 4 styles de modules (carré, rond, arrondi, diamant), coins personnalisables (forme et couleur), marge et logo intégré.',
  },
  {
    ico: '🖨️',
    title: 'Export haute qualité',
    text: 'PNG ×4 pour le web et l’impression courante, SVG vectoriel pour le print pro. Le fond est aplati : pas de PNG transparent illisible.',
  },
  {
    ico: '⚡',
    title: 'Temps réel, sans rechargement',
    text: `Le QR se met à jour à chaque saisie (rendu débounce à 120 ms). Au-delà de ${MAX_QR_BYTES.toLocaleString('fr-FR')} octets, un message explique comment raccourcir.`,
  },
  {
    ico: '🔒',
    title: 'Sans inscription, sans serveur',
    text: 'Aucune donnée ne quitte l’onglet : pas d’appel réseau, pas de compte, pas de tracker. Le code source tient dans sept petits modules.',
  },
  {
    ico: '📎',
    title: 'Fichiers : l’URL reste la bonne voie',
    text: `L’import direct n’accepte que les fichiers tenant dans un QR (${Math.floor(((MAX_QR_BYTES - 80) * 3) / 4 / 1024)} Ko max en base64). Au-delà, l’outil le dit et propose l’URL hébergée.`,
  },
];

export const STEPS = [
  { title: 'Choisissez un type', text: 'Carte de visite, URL Wi-Fi, événement… et remplissez les champs correspondants.' },
  { title: 'Personnalisez', text: 'Couleurs noir & or ou votre identité, style de modules, logo : à votre image.' },
  { title: 'Téléchargez', text: 'Exportez en PNG ou SVG et utilisez-le partout, web comme print.' },
];

/* ---------------------------------------------------------------------------
   `/facturation`. Les montants, les listes de droits et les quotas viennent de l'API
   (`apps/accounts/plans.py` côté serveur) : ce fichier ne fournit que les LIBELLÉS. Un libellé inconnu
   s'affiche tel quel (clé brute) au lieu de disparaître — une carte de prix muette est pire qu'une
   carte technique, et la grille gagne des clés quand un palier est enrichi.
   --------------------------------------------------------------------------- */

export const BILL_COPY = {
  intro:
    'Le palier est accordé par le service de paiement, pas par cette page : vous payez, nous confirmons, le quota suit.',
  note:
    'Prix en euros, sans engagement, résiliable depuis le portail. Les QR déjà créés restent lisibles après un retour au palier Gratuit.',
  mobile:
    'Le paiement mobile part vers l’agrégateur : vous recevez une invitation à confirmer sur votre téléphone. Rien n’est débité si vous ne confirmez pas, et une demande expirée ne sera pas rattrapée plus tard.',
};

export const PALIERS_SURTITRE = {
  standard: 'Pour publier souvent',
  premium: 'Pour mesurer',
  business: 'Pour une équipe',
};

export const PALIERS_LABELS = {
  qr_statique: 'QR statiques',
  export_image: 'Export PNG et SVG',
  score_scannabilite: 'Score de scannabilité',
  cadres_cta: 'Cadres et appels à l’action',
  export_pdf: 'Export PDF prêt à imprimer',
  themes_sectoriels: 'Thèmes par secteur',
  qr_dynamique: 'QR dynamiques, modifiables après impression',
  analytics: 'Statistiques de scan par pays et par jour',
  carte_visite_connectee: 'Carte de visite connectée',
  qr_multi_liens: 'QR multi-liens',
  api_developpeurs: 'API développeurs',
  qr_artistique_ia: 'QR artistiques (motif dessiné, relu avant envoi)',
  qr_anime: 'QR animés (GIF à liseré mobile pour écrans et réseaux)',
  codes_promo: 'Codes promo à date de fin, vérifiés au scan',
  boutique_templates: 'Boutique de templates pour l’équipe',
};

export const LIMITES_LABELS = {
  statiques: 'QR statiques',
  dynamiques_30j: 'QR dynamiques par 30 jours',
  historique_jours: 'Historique conservé (jours)',
  exports_par_jour: 'Exports par jour',
  codes_promo_max: 'Codes promo actifs',
};

export const FOOTER_NOTE =
  'QR Coding — Générateur de QR codes · version React · la création reste dans votre navigateur ; l’abonnement et les QR dynamiques passent par le service.';
