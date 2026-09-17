import { useId, useRef, useState } from 'react';
import { copyText } from '../lib/export.js';
import { byteLength } from '../lib/qrcode.js';
import { CapacityBar } from './DynamicFields.jsx';

/**
 * Panneau de droite : aperçu + personnalisation + exports.
 *
 * L'aperçu est le SVG lui-même (plus de data-URI base64 recréée à chaque frappe) :
 * affichage instantané, net à toute taille, identique à l'export au grain près.
 */
export function Preview({ studio }) {
  const { type, data, payload, result, warnings, state, actions } = studio;
  const design = state.design;
  const [copied, setCopied] = useState(false);
  const logoInputRef = useRef(null);
  const bytes = byteLength(payload);
  const svg = result.export?.svg ?? '';
  const active = result.ok;

  const pickLogo = (file) => {
    if (!file) return;
    if (!/^image\//.test(file.type)) {
      actions.setNotice({ tone: 'warn', text: 'Le logo doit être une image (PNG, JPG ou SVG).' });
      return;
    }
    if (file.size > 400_000) {
      actions.setNotice({ tone: 'warn', text: 'Image trop lourde pour un logo (max 400 Ko).' });
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const url = String(reader.result);
      if (!url.startsWith('data:image/')) {
        actions.setNotice({ tone: 'warn', text: 'Format de logo non pris en charge.' });
        return;
      }
      actions.setLogo(url, { tone: 'info', text: 'Logo appliqué — réduisez-le si la trame devient difficile à lire.' });
    };
    reader.onerror = () => actions.setNotice({ tone: 'warn', text: 'Lecture du logo impossible.' });
    reader.readAsDataURL(file);
  };

  const copy = async () => {
    if (!payload) return;
    setCopied(await copyText(payload));
    setTimeout(() => setCopied(false), 1600);
  };

  return (
    <>
      <div className="qrc-preview">
        {active ? (
          <div
            className="qrc-preview-svg"
            role="img"
            aria-label={`QR code ${type.label} — ${result.matrix.size}×${result.matrix.size} modules`}
            dangerouslySetInnerHTML={{ __html: result.preview.svg }}
          />
        ) : (
          <div className="qrc-preview-empty">
            <span aria-hidden="true">▦</span>
            <p>{result.code === 'TOO_LONG' ? 'Contenu trop volumineux pour un QR code' : 'Remplissez le formulaire pour voir le QR code'}</p>
          </div>
        )}
        <CapacityBar bytes={bytes} ok={result.ok} />
      </div>

      {warnings.length > 0 ? (
        <ul className="qrc-notices" aria-live="polite">
          {warnings.map((w) => (
            <li key={w.text} className={`qrc-notice is-${w.tone}`}>
              {w.tone === 'warn' ? '⚠️' : 'ℹ️'} {w.text}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="qrc-custom">
        <div className="qrc-custom-grid">
          <ColorField id="qrc-fg" label="Couleur des modules" value={design.moduleColor} onChange={actions.setModuleColor} />
          <ColorField id="qrc-bg" label="Couleur de fond" value={design.background} onChange={(v) => actions.setDesign({ background: v })} />
          <p className="qrc-field">
            <label htmlFor="qrc-margin">Marge (px)</label>
            <input id="qrc-margin" type="number" min={0} max={96} step={2} value={design.margin} onChange={(e) => actions.setMargin(e.target.value)} />
            <small className="qrc-hint">40 px = 4 modules, le minimum recommandé par la norme ISO 18004.</small>
          </p>
        </div>

        <fieldset className="qrc-fieldset">
          <legend>Style des modules</legend>
          <Chips
            group="Style des modules"
            value={design.style}
            onChange={(v) => actions.setDesign({ style: v })}
            options={[
              { value: 'square', label: '■ Carré' },
              { value: 'round', label: '● Rond' },
              { value: 'rounded', label: '▢ Arrondi' },
              { value: 'diamond', label: '◆ Diamant' },
            ]}
          />
        </fieldset>

        <fieldset className="qrc-fieldset">
          <legend>Forme des coins (repères)</legend>
          <Chips
            group="Forme des coins"
            value={design.finderStyle}
            onChange={(v) => actions.setDesign({ finderStyle: v })}
            options={[
              { value: 'square', label: '■ Carré' },
              { value: 'rounded', label: '▢ Arrondi' },
              { value: 'circle', label: '● Cercle' },
            ]}
          />
        </fieldset>

        <fieldset className="qrc-fieldset">
          <legend>Couleur des coins</legend>
          <div className="qrc-inline">
            <input
              type="color"
              id="qrc-finder"
              value={design.finderFollowsModules ? design.moduleColor : design.finderColor}
              disabled={design.finderFollowsModules}
              onChange={(e) => actions.setDesign({ finderColor: e.target.value, finderFollowsModules: false })}
            />
            <label className="qrc-check" htmlFor="qrc-finder-follow">
              <input id="qrc-finder-follow" type="checkbox" checked={design.finderFollowsModules} onChange={actions.toggleFinderFollow} />
              Assortis aux modules
            </label>
          </div>
        </fieldset>

        <fieldset className="qrc-fieldset">
          <legend>Ajouter un logo (optionnel)</legend>
          <button type="button" className={`qrc-drop${design.logo ? ' is-filled' : ''}`} onClick={() => logoInputRef.current?.click()}>
            {design.logo ? '✓ Logo importé — cliquez pour remplacer' : 'Cliquez pour importer une image (PNG, JPG, SVG…)'}
          </button>
          <input ref={logoInputRef} id="qrc-logo-input" type="file" accept="image/png,image/jpeg,image/svg+xml" hidden onChange={(e) => pickLogo(e.target.files?.[0])} />

          {design.logo ? (
            <div className="qrc-range">
              <label htmlFor="qrc-logo-size">
                Taille du logo : {design.logoPercent} %
                {result.preview?.logoPercent && result.preview.logoPercent !== design.logoPercent ? (
                  <em className="qrc-muted"> (ramené à {result.preview.logoPercent} % pour rester lisible)</em>
                ) : null}
              </label>
              <input id="qrc-logo-size" type="range" min={10} max={35} step={1} value={design.logoPercent} onChange={(e) => actions.setLogoPercent(e.target.value)} />
              <button type="button" className="qrc-btn qrc-btn-ghost qrc-mini" onClick={actions.clearLogo}>
                Retirer
              </button>
            </div>
          ) : null}
        </fieldset>

        <details className="qrc-payload">
          <summary>Contenu encodé ({bytes.toLocaleString('fr-FR')} o)</summary>
          <pre>{payload || '—'}</pre>
          <button id="qrc-copy" type="button" className="qrc-btn qrc-btn-ghost qrc-mini" onClick={copy} disabled={!payload}>
            {copied ? '✓ Copié' : 'Copier le contenu'}
          </button>
        </details>

        <div className="qrc-dl">
          <button id="qrc-dl-png" type="button" className="qrc-btn qrc-btn-primary" disabled={!active} onClick={() => studio.export('png', svg)}>
            ⬇ Télécharger PNG
          </button>
          <button id="qrc-dl-svg" type="button" className="qrc-btn qrc-btn-ghost" disabled={!active} onClick={() => studio.export('svg', svg)}>
            ⬇ SVG
          </button>
        </div>
      </div>
    </>
  );
}

function ColorField({ id, label, value, onChange }) {
  return (
    <p className="qrc-field qrc-field--color">
      <label htmlFor={id}>{label}</label>
      <input id={id} type="color" value={value} onChange={(e) => onChange(e.target.value)} />
    </p>
  );
}

function Chips({ options, value, onChange, group }) {
  return (
    <div className="qrc-chips" role="group" aria-label={group}>
      {options.map((o) => (
        <button key={o.value} type="button" className={`qrc-chip${o.value === value ? ' is-active' : ''}`} aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
