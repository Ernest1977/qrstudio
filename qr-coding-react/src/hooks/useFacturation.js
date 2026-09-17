/**
 * Toute la logique de `/facturation` : qui est connecté, ce que coûte chaque palier, où en est un
 * paiement mobile, et ce que Stripe nous a laissé comme retour.
 *
 * Deux principes, parce que le sujet est l'argent :
 *
 * - **Aucun prix n'est écrit ici.** La grille vient de `GET /billing/etat` (ou `/auth/config` à froid),
 *   donc de `apps/accounts/plans.py`. Un tarif recopié côté front est un tarif qui ment le jour où la
 *   grille bouge — et c'est déjà l'erreur classique des pages de prix.
 * - **Aucun état n'est déduit d'un clic.** Le bouton « S'abonner » ouvre la page Stripe ; c'est le
 *   webhook qui accorde le palier côté serveur. Le front ne fait que *relire* `/etat`, y compris au
 *   retour de Stripe où l'on attend quelques secondes, sans jamais déclarer le client abonné lui-même.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ErreurApi, api, definirJetonCsrf } from '../lib/api.js';
import { parametres, routeDe, ROUTE_FACTURATION } from '../lib/router.js';

const CLE_REPRISE = 'qrc.facturation.mobile';
const SONDAGE_MOBILE_MS = 5000;
const DELAI_RETOUR_STRIPE_MS = 2000;
const TENTATIVES_RETOUR_STRIPE = 10;

/** Statuts de `PaiementMobile` : ce qui ne peut plus bouger, et ce qui attend encore. */
const MOBILE_TERMINAL = new Set(['confirmed', 'expired', 'failed', 'refunded']);

/** Codes qui ne sont pas la faute du client : à signaler à l'exploitation, pas à l'utilisateur. */
const HORS_SERVICE = new Set(['stripe_non_configure', 'stripe_price_manquant', 'mobile_provider_non_configure', 'mobile_provider_invalide']);

export const LIBELLES_MOBILE = {
  pending: 'En attente de confirmation dans votre application',
  confirmed: 'Paiement confirmé',
  expired: 'Expiré — aucune somme n’a été prélevée',
  failed: 'Échoué',
  refunded: 'Remboursé',
};

export const LIBELLES_ABONNEMENT = {
  trialing: "Période d'essai",
  active: 'Actif',
  past_due: 'Paiement en retard',
  paused: 'En pause',
  canceled: 'Annulé',
  incomplete: 'Paiement initial non abouti',
};

function lireReprise() {
  try {
    const brut = window.sessionStorage.getItem(CLE_REPRISE);
    if (!brut) return null;
    const objet = JSON.parse(brut);
    return objet && objet.reference ? objet : null;
  } catch {
    return null; // sessionStorage coupé (navigation privée stricte) : on suit le paiement sans reprise
  }
}

function ecrireReprise(paiement) {
  try {
    if (paiement) window.sessionStorage.setItem(CLE_REPRISE, JSON.stringify(paiement));
    else window.sessionStorage.removeItem(CLE_REPRISE);
  } catch {
    /* rien à faire, et rien à signaler : c'est un confort, pas une garantie de paiement */
  }
}

export function formaterMontant(centimes, devise = 'eur') {
  if (typeof centimes !== 'number' || !Number.isFinite(centimes)) return '';
  try {
    return new Intl.NumberFormat('fr-FR', { style: 'currency', currency: (devise || 'eur').toUpperCase() }).format(centimes / 100);
  } catch {
    return `${(centimes / 100).toFixed(2)} ${devise}`; // devise inconnue de Intl : on n'affiche pas « NaN » sur une page de caisse
  }
}

export function formaterDuree(epochMs) {
  const reste = Math.max(0, Math.floor((epochMs - Date.now()) / 1000));
  const m = Math.floor(reste / 60);
  const s = reste % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function useFacturation() {
  const [session, setSession] = useState({ etat: 'chargement', utilisateur: null });
  const [licence, setLicence] = useState(null);
  const [abonnement, setAbonnement] = useState(null);
  const [acorde, setAcorde] = useState(false);
  const [paliers, setPaliers] = useState([]);
  const [occupes, setOccupes] = useState({});
  const [mobile, setMobile] = useState(() => lireReprise());
  const [maintenant, setMaintenant] = useState(() => Date.now());
  const [notice, setNotice] = useState(null);
  const [attendStripe, setAttendStripe] = useState(false);

  const minuteurMobile = useRef(null);
  const minuteurRetour = useRef(null);

  const signaler = useCallback((tone, text) => setNotice({ tone, text }), []);

  const rafraichir = useCallback(async () => {
    try {
      const etat = await api.etat();
      setLicence(etat.licence || null);
      setAbonnement(etat.abonnement || null);
      setAcorde(Boolean(etat.acorde));
      if (etat.licence && Array.isArray(etat.licence.paliers)) setPaliers(etat.licence.paliers);
      return etat;
    } catch (e) {
      if (e instanceof ErreurApi && e.statut === 401) setSession({ etat: 'deconnecte', utilisateur: null });
      else signaler('warn', `Le état du compte n'a pas pu être relu : ${e.message}`);
      return null;
    }
  }, [signaler]);

  // --- session -------------------------------------------------------------------------
  useEffect(() => {
    let annule = false;
    api
      .moi()
      .then(async (utilisateur) => {
        if (annule) return;
        setSession({ etat: 'connecte', utilisateur });
        await rafraichir();
      })
      .catch((e) => {
        if (annule) return;
        // 401 = pas de session, pas une panne. Tout le reste = panne, et le dire comme tel.
        if (e instanceof ErreurApi && e.statut === 401) setSession({ etat: 'deconnecte', utilisateur: null });
        else {
          setSession({ etat: 'erreur', utilisateur: null });
          signaler('warn', `Service indisponible : ${e.message}`);
        }
      });
    return () => {
      annule = true;
    };
  }, [rafraichir, signaler]);

  // --- grille affichée à froid (aucune session) --------------------------------------
  useEffect(() => {
    if (paliers.length) return;
    api
      .config()
      .then((config) => setPaliers(Array.isArray(config?.paliers) ? config.paliers : []))
      .catch(() => {
        /* sans grille, la page reste utilisable : les boutons affichent l'erreur du service */
      });
  }, [paliers.length]);

  // --- retour de Stripe ----------------------------------------------------------------
  useEffect(() => {
    if (routeDe(window.location.pathname) !== ROUTE_FACTURATION) return undefined;
    const q = parametres();
    if (q.get('annule')) {
      signaler('info', 'Paiement interrompu. Aucun abonnement n’a été créé, aucune carte n’a été débitée.');
      // L'URL est nettoyée : sinon « précédent » rejoue l'annulation à chaque retour sur la page.
      window.history.replaceState({}, '', window.location.pathname);
      return undefined;
    }
    if (!q.get('session_id')) return undefined;

    // Un aller simple vers Stripe ne prouve rien : le webhook peut avoir quelques secondes de retard.
    // On relit `/etat` un nombre borné de fois, puis on dit la vérité — « pas encore confirmé ».
    setAttendStripe(true);
    let tentatives = 0;
    const relire = async () => {
      const etat = await rafraichir();
      tentatives += 1;
      const regle = etat && etat.acorde;
      if (regle) {
        setAttendStripe(false);
        signaler('ok', 'Paiement enregistré. Votre palier est actif.');
        window.history.replaceState({}, '', window.location.pathname);
        window.clearInterval(minuteurRetour.current);
        return;
      }
      if (tentatives >= TENTATIVES_RETOUR_STRIPE) {
        setAttendStripe(false);
        signaler('warn', "Nous n'avons pas encore confirmé votre paiement. Stripe met parfois une minute ; rechargez cette page.");
        window.clearInterval(minuteurRetour.current);
      }
    };
    minuteurRetour.current = window.setInterval(relire, DELAI_RETOUR_STRIPE_MS);
    relire();
    return () => window.clearInterval(minuteurRetour.current);
  }, [rafraichir, signaler]);

  // --- suivi du paiement mobile --------------------------------------------------------
  const sonder = useCallback(
    async (reference) => {
      try {
        const etat = await api.mobileEtat(reference);
        setMobile((precedent) => ({ ...(precedent || {}), ...etat }));
        ecrireReprise(MOBILE_TERMINAL.has(etat.statut) ? null : { reference, palier: etat.palier, expire_le: etat.expire_le });
        if (etat.statut === 'confirmed') {
          signaler('ok', 'Paiement mobile confirmé.');
          await rafraichir();
        } else if (MOBILE_TERMINAL.has(etat.statut)) {
          signaler('warn', LIBELLES_MOBILE[etat.statut] || `Paiement ${etat.statut}.`);
        }
        return etat.statut;
      } catch (e) {
        if (e instanceof ErreurApi && (e.statut === 401 || e.code === 'reference_inconnue')) {
          ecrireReprise(null);
          setMobile(null);
          return 'inconnu';
        }
        signaler('warn', `Suivi du paiement indisponible : ${e.message}`);
        return 'erreur';
      }
    },
    [rafraichir, signaler],
  );

  useEffect(() => {
    const reference = mobile && mobile.reference;
    if (!reference || MOBILE_TERMINAL.has(mobile.statut)) return undefined;
    // Un seul intervalle pour le compte à rebours et le sondage : deux minuteurs qui se marchent
    // dessus est la façon habituelle de sonder le double de la fréquence prévue.
    let ticks = 0;
    minuteurMobile.current = window.setInterval(() => {
      ticks += 1;
      setMaintenant(Date.now());
      if (ticks % Math.round(SONDAGE_MOBILE_MS / 1000) === 0) sonder(reference);
    }, 1000);
    sonder(reference);
    return () => window.clearInterval(minuteurMobile.current);
  }, [mobile && mobile.reference, mobile && mobile.statut, sonder]);

  // --- actions -------------------------------------------------------------------------
  const connexion = useCallback(
    async (email, motDePasse) => {
      const reponse = await api.connexion(email, motDePasse);
      if (reponse && reponse.csrf_token) definirJetonCsrf(reponse.csrf_token); // rotate_token() côté serveur
      setSession({ etat: 'connecte', utilisateur: reponse.user || null });
      await rafraichir();
      signaler('ok', 'Connecté.');
      return true;
    },
    [rafraichir, signaler],
  );

  const inscription = useCallback(
    async ({ email, motDePasse, acceptTerms }) => {
      await api.inscription({ email, password: motDePasse, accept_terms: acceptTerms });
      signaler('ok', 'Compte créé. Saisissez le code reçu par e-mail pour activer la vérification, puis connectez-vous.');
      return true;
    },
    [signaler],
  );

  const deconnexion = useCallback(async () => {
    try {
      await api.deconnexion();
    } finally {
      setSession({ etat: 'deconnecte', utilisateur: null });
      setLicence(null);
      setAbonnement(null);
      setAcorde(false);
      ecrireReprise(null);
      setMobile(null);
    }
  }, []);

  const souscrire = useCallback(
    async (code) => {
      setOccupes((o) => ({ ...o, [code]: true }));
      try {
        const { url } = await api.checkout(code);
        if (!url) throw new ErreurApi('Le service de paiement n’a renvoyé aucune URL.', { code: 'url_absente' });
        // Evenement de navigation réel : on ne simule pas un succès, on part chez Stripe.
        window.location.assign(url);
        return true;
      } catch (e) {
        signaler(
          HORS_SERVICE.has(e.code) ? 'warn' : 'err',
          HORS_SERVICE.has(e.code) ? `${e.message} (réglage d’exploitation manquant)` : e.message,
        );
        return false;
      } finally {
        setOccupes((o) => ({ ...o, [code]: false }));
      }
    },
    [signaler],
  );

  const gererAbonnement = useCallback(async () => {
    try {
      const { url } = await api.portail();
      window.location.assign(url);
    } catch (e) {
      signaler('warn', e.code === 'client_inconnu' ? 'Aucun abonnement Stripe sur ce compte : rien à gérer.' : e.message);
    }
  }, [signaler]);

  const demarrerMobile = useCallback(
    async (code, telephone) => {
      setOccupes((o) => ({ ...o, [`mobile:${code}`]: true }));
      try {
        const paiement = await api.mobileDemande(code, telephone);
        setMobile(paiement);
        ecrireReprise({ reference: paiement.reference, palier: code, expire_le: paiement.expire_le });
        signaler('info', 'Suivez l’invitation sur votre téléphone, puis confirmez-y le paiement.');
        return true;
      } catch (e) {
        signaler(HORS_SERVICE.has(e.code) ? 'warn' : 'err', e.message);
        return false;
      } finally {
        setOccupes((o) => ({ ...o, [`mobile:${code}`]: false }));
      }
    },
    [signaler],
  );

  const revérifierMobile = useCallback(async () => {
    const reference = mobile && mobile.reference;
    if (!reference) return;
    // Volontairement pas de `rafraichir()` d'abord : l'agrégateur peut avoir confirmé sans que notre
    // rappel soit arrivé. On relit la référence, et c'est elle qui décide du statut.
    await sonder(reference);
  }, [mobile, sonder]);

  const annulerSuivi = useCallback(() => {
    ecrireReprise(null);
    setMobile(null);
    setNotice(null);
  }, []);

  const cartes = useMemo(
    () =>
      paliers
        .filter((p) => p.code !== 'free')
        .map((p) => ({
          ...p,
          estActuel: abonnement ? abonnement.palier === p.code : licence && licence.code === p.code && acorde,
        })),
    [paliers, abonnement, licence, acorde],
  );

  const restant = useMemo(() => {
    if (!mobile || !mobile.expire_le) return null;
    const fin = typeof mobile.expire_le === 'number' ? mobile.expire_le * 1000 : Date.parse(mobile.expire_le);
    return { epoch: fin, libelle: formaterDuree(fin), epuise: fin - maintenant <= 0 };
  }, [mobile, maintenant]);

  return {
    session,
    licence,
    abonnement,
    acorde,
    cartes,
    notice,
    occupes,
    mobile,
    restant,
    attendStripe,
    effacerNotice: () => setNotice(null),
    actions: { connexion, inscription, deconnexion, souscrire, gererAbonnement, demarrerMobile, annulerSuivi, revérifierMobile, rafraichir },
  };
}
