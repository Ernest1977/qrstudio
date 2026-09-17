import { useCallback, useRef } from 'react';
import { useStudio } from '../hooks/useStudio.js';
import { downloadPng, downloadSvg } from '../lib/export.js';
import { byteLength } from '../lib/qrcode.js';
import { TypePicker } from './TypePicker.jsx';
import { DynamicFields, FileDrop } from './DynamicFields.jsx';
import { Preview } from './Preview.jsx';
import { RendusServeur } from './RendusServeur.jsx';

/**
 * Le « studio » : 1. Contenu | 2. Aperçu & personnalisation.
 * Toute la logique est dans `useStudio` ; ce composant ne fait que câbler.
 */
export function Studio({ types }) {
  const studio = useStudio();
  const { state, type, data, payload, result, warnings, actions } = studio;
  const exportBusy = useRef(false);
  const fileBytes = byteLength(payload);

  const onExport = useCallback(
    async (kind, svg) => {
      if (!svg) return;
      exportBusy.current = true;
      try {
        if (kind === 'svg') {
          downloadSvg(svg, 'qr-code.svg');
          actions.setNotice({ tone: 'info', text: 'QR code vectoriel enregistré (qr-code.svg).' });
        } else {
          const size = await downloadPng(svg, {
            scale: 4,
            filename: 'qr-code.png',
            background: state.design.background,
          });
          actions.setNotice({ tone: 'info', text: `PNG enregistré — ${size.width}×${size.height} px.` });
        }
      } catch (err) {
        actions.setNotice({ tone: 'warn', text: `Export impossible : ${err.message}` });
      } finally {
        exportBusy.current = false;
      }
    },
    [actions, state.design.background],
  );

  return (
    <section className="qrc-studio" id="studio" aria-label="Générateur de QR code">
      <Card step={1} title="Contenu" side="left">
        <TypePicker types={types} value={state.type} onSelect={actions.selectType} />
        <DynamicFields type={type} data={data} onChange={actions.setField} />
        {type.file ? (
          <FileDrop
            file={type.file}
            embedded={state.embedded}
            bytes={fileBytes}
            onPick={(embedded) => {
              actions.setEmbedded(embedded, { tone: 'info', text: `« ${embedded.name} » intégré au QR code (${(embedded.size / 1024).toFixed(1)} Ko).` });
            }}
            onReject={(text) => actions.setNotice({ tone: 'warn', text })}
          />
        ) : null}
      </Card>

      <Card step={2} title="Aperçu & personnalisation" side="right">
        <Preview studio={{ ...studio, export: onExport }} />
        {/* Le rendu serveur vient aprés l'aperçu local, et ne le remplace pas : le canvas reste la
            réactivité immédiate (hors ligne, aucun octet envoyé), le serveur apporte la preuve de
            lisibilité et les formats qu'on imprime. */}
        <RendusServeur payload={payload} design={state.design} />
      </Card>
    </section>
  );
}

function Card({ step, title, side, children }) {
  return (
    <div className={`qrc-card qrc-card--${side}`}>
      <h2>
        <span className="qrc-card-step" aria-hidden="true">
          {step}
        </span>
        {title}
      </h2>
      {children}
    </div>
  );
}
