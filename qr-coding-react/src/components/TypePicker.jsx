import { useRef } from 'react';

/**
 * Grille des types de contenu.
 * L'original utilisait des `<div onclick>` : inaccessible au clavier. Ici de
 * vrais `<button>` avec `aria-pressed`, dans un groupe rôlé « radio ».
 */
export function TypePicker({ types, value, onSelect }) {
  const firstRef = useRef(null);

  const onKeyDown = (event) => {
    const index = types.findIndex((t) => t.id === value);
    let next = null;
    if (event.key === 'ArrowRight') next = (index + 1) % types.length;
    else if (event.key === 'ArrowLeft') next = (index - 1 + types.length) % types.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = types.length - 1;
    if (next == null) return;
    event.preventDefault();
    onSelect(types[next].id);
    firstRef.current?.focus();
  };

  return (
    <div className="qrc-types" role="group" aria-label="Type de contenu" onKeyDown={onKeyDown}>
      {types.map((t, i) => (
        <button
          key={t.id}
          type="button"
          ref={i === 0 ? firstRef : null}
          className={`qrc-type${t.id === value ? ' is-active' : ''}`}
          aria-pressed={t.id === value}
          onClick={() => onSelect(t.id)}
        >
          <span className="qrc-type-ico" aria-hidden="true">
            {t.ico}
          </span>
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** Étiquettes de statut (capacité, avertissements) — remplace le `#warnBox`. */
export function Notices({ items, code }) {
  if (!items.length && !code) return null;
  return (
    <div className="qrc-notices" role="status" aria-live="polite">
      {items.map((n) => (
        <p key={n.text} className={`qrc-notice is-${n.tone}`}>
          {n.tone === 'warn' ? '⚠️' : 'ℹ️'} {n.text}
        </p>
      ))}
      {!items.length && code ? <p className="qrc-notice is-info">👌 {code}</p> : null}
    </div>
  );
}
