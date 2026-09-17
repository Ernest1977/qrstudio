/**
 * Routage minimal — deux routes, zéro dépendance.
 *
 * `react-router` n'est pas installé et cette application n'a pas vocation à devenir un SPA à vingt
 * écrans. Or `/facturation` est une URL **promise par le backend** : c'est là que `cancel_url` ramène
 * un paiement interrompu, et `/facturation/retour` celui qui aboutit. Sans route réelle, ces liens
 * atterriraient sur une 404 ou, pire, sur l'accueil sans message.
 *
 * Ce qu'on accepte donc, et rien de plus : lire `location.pathname`, répondre aux boutons précédent /
 * suivant (`popstate`), intercepter les clics sur les liens internes, et remonter aux ancres (`#studio`)
 * depuis une autre page — le cas où l'ancre n'existe qu'après avoir changé de page.
 *
 * Le composant `<Lien>` vit à côté, dans `components/Lien.jsx` : un fichier `.js` n'a pas droit au JSX
 * sous Vite (le chargeur est choisi sur l'extension), et déplacer tout le routage en `.jsx` perdrait les
 * helpers Purs (`routeDe`, `parametres`) utilisables hors React, notamment en test.
 */

import { useCallback, useEffect, useState } from 'react';

export const ROUTE_ACCUEIL = 'accueil';
export const ROUTE_FACTURATION = 'facturation';

const TABLE = new Map([
  ['/facturation', ROUTE_FACTURATION],
  ['/facturation/', ROUTE_FACTURATION],
  ['/facturation/retour', ROUTE_FACTURATION],
  ['/facturation/retour/', ROUTE_FACTURATION],
]);

export function routeDe(chemin) {
  return TABLE.get(chemin) || ROUTE_ACCUEIL;
}

/** Le chemin de retour de Stripe porte `?session_id=` ; celui d'annulation `?annule=1`. */
export function parametres(search = window.location.search) {
  return new URLSearchParams(search);
}

export function estInterne(href) {
  if (!href) return false;
  if (href.startsWith('#')) return true; // ancres de la page d'accueil
  try {
    const u = new URL(href, window.location.origin);
    return u.origin === window.location.origin && TABLE.has(u.pathname);
  } catch {
    return false;
  }
}

/**
 * Navigue, puis fait défiler vers `#ancre` une fois la page rendue.
 *
 * Le défilement est reporté par frames successives, pas par un `setTimeout` arbitraire : sur la route
 * d'accueil, le studio est monté dans le même commit et l'élément ciblé n'existe qu'une fois ce commit
 * peint. Une frame de trop ne coûte rien, un délai fixé à la main casse le comportement sur machine lente.
 */
export function naviguer(cheminOuAncre) {
  if (cheminOuAncre.startsWith('#')) {
    const id = cheminOuAncre.slice(1);
    if (routeDe(window.location.pathname) !== ROUTE_ACCUEIL) {
      window.history.pushState({}, '', '/');
      window.dispatchEvent(new PopStateEvent('popstate'));
      defilerApresRendu(id);
      return;
    }
    const cible = document.getElementById(id);
    if (cible) cible.scrollIntoView({ behavior: 'smooth', block: 'start' });
    return;
  }
  if (cheminOuAncre === window.location.pathname + window.location.search) {
    window.scrollTo({ top: 0, behavior: 'smooth' });
    return;
  }
  window.history.pushState({}, '', cheminOuAncre);
  window.dispatchEvent(new PopStateEvent('popstate'));
  window.scrollTo({ top: 0 });
}

function defilerApresRendu(id, tentatives = 30) {
  let reste = tentatives;
  const cherche = () => {
    const cible = document.getElementById(id);
    if (cible) {
      cible.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    if (--reste > 0) window.requestAnimationFrame(cherche);
  };
  window.requestAnimationFrame(cherche);
}

export function useRoute() {
  const [etat, setEtat] = useState(() => ({
    route: routeDe(window.location.pathname),
    search: window.location.search,
  }));

  useEffect(() => {
    const sync = () =>
      setEtat({
        route: routeDe(window.location.pathname),
        search: window.location.search,
      });
    window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, []);

  return { ...etat, naviguer };
}
