import { useCallback } from 'react';
import { naviguer } from '../lib/router.js';

/**
 * Lien interne qui reste un `<a href>` vrai.
 *
 * Clic droit → « ouvrir dans un nouvel onglet », milieu de molette, Ctrl-clic : tous doivent garder le
 * comportement natif du navigateur, donc on n'appelle `preventDefault()` que sur le clic gauche simple
 * sans modificateur. Un `onClick` qui bloque tout est le défaut classique des SPA maison : il transforme
 * chaque lien en impasse pour le gestionnaire de mots de passe et pour l'accessibilité clavier.
 */
export function Lien({ to, children, className, title, onClick, ...reste }) {
  const gerer = useCallback(
    (e) => {
      if (onClick) onClick(e);
      if (e.defaultPrevented) return;
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      naviguer(to);
    },
    [to, onClick],
  );
  return (
    <a href={to} className={className} title={title} onClick={gerer} {...reste}>
      {children}
    </a>
  );
}
