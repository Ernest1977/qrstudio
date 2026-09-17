import { useMemo, useReducer } from 'react';
import { getType } from '../lib/types.js';
import { buildPayload } from '../lib/payload.js';
import { createMatrix } from '../lib/qrcode.js';
import { renderQrSvg, CELL } from '../lib/render.js';
import { initialState, reducer, currentData } from '../state.js';
import { useDebouncedValue } from './useDebouncedValue.js';

/**
 * Un seul grain pour l'aperçu ET l'export : les deux rendus sont alors
 * strictement identiques (le PNG monte en résolution grâce à `scale`, pas grâce
 * à un nombre de modules différent). Deux grains différents faisaient diverger
 * la réduction automatique du logo entre ce qu'on voit et ce qu'on télécharge.
 */
export const PREVIEW_CELL = 10;
export const EXPORT_CELL = CELL;
export const QR_CELL = PREVIEW_CELL;

/**
 * Toute la logique du studio : formulaire → payload → grille → SVG.
 * Un seul point d'entrée pour les panneaux, ce qui remplace la vingtaine de
 * `document.getElementById` + `addEventListener` du fichier d'origine.
 */
export function useStudio() {
  const [state, dispatch] = useReducer(reducer, undefined, initialState);

  const type = getType(state.type);
  const data = currentData(state);

  // Un gros QR (v40 ≈ 177 modules) se rend en ~30 ms : on débounce le rendu,
  // jamais les champs (l'original recalculait tout à chaque frappe).
  const debounced = useDebouncedValue({ type: state.type, data, embedded: state.embedded }, 120);
  const settling = debounced.type !== state.type || debounced.data !== data || debounced.embedded !== state.embedded;

  const { design, notice } = state;

  const payload = useMemo(
    () => buildPayload(getType(debounced.type), debounced.data, { embedded: debounced.embedded?.dataUrl }),
    [debounced.type, debounced.data, debounced.embedded],
  );

  // Un logo couvre ~20 % des modules : avec la correction M de l'original, le
  // code devient indécodable dès que le contenu est dense. On relève le niveau.
  const ecc = design.logo ? 'H' : design.ecc;

  const result = useMemo(() => {
    if (!payload) return { ok: false, code: 'EMPTY', message: '', matrix: null, preview: null, export: null };
    let matrix;
    try {
      matrix = createMatrix(payload, { ecc });
    } catch (err) {
      return { ok: false, code: err.code ?? 'ERROR', message: err.message, matrix: null, preview: null, export: null };
    }
    const base = {
      style: design.style,
      finderStyle: design.finderStyle,
      moduleColor: design.moduleColor,
      background: design.background,
      finderColor: design.finderFollowsModules ? null : design.finderColor,
      margin: design.margin,
      logo: design.logo,
      logoPercent: design.logoPercent,
      ecc,
      caption: type.kind === 'file' ? String(debounced.data.desc ?? '').trim() : '',
    };
    const preview = renderQrSvg(matrix, { ...base, cell: QR_CELL });
    const exported = renderQrSvg(matrix, { ...base, cell: QR_CELL });
    if (!preview || !exported) return { ok: false, code: 'RENDER', message: 'Rendu impossible.', matrix, preview: null, export: null };
    return { ok: true, code: 'OK', message: '', matrix, preview, export: exported, ecc };
  }, [payload, design, ecc, type.kind, debounced.data]);

  const warnings = useMemo(() => {
    const list = [];
    // Les messages du formulaire (import de fichier, logo…) passent en premier :
    // ils doivent rester lisibles même quand le QR ne peut pas être généré.
    if (notice) list.push({ tone: notice.tone ?? 'info', text: notice.text });
    if (!result.ok) {
      if (result.code !== 'EMPTY') list.push({ tone: 'warn', text: result.message || 'Contenu incomplet.' });
      return list;
    }
    for (const text of result.preview?.warnings ?? []) list.push({ tone: 'warn', text });
    if (design.logo && ecc !== design.ecc) list.push({ tone: 'info', text: `Niveau de correction automatiquement relevé en ${ecc} pour absorber le logo.` });
    if (type.id === 'url' && data.url && !/^[a-z][a-z0-9+.-]*:/i.test(String(data.url).trim())) {
      list.push({ tone: 'info', text: 'Protocole ajouté automatiquement : https://' });
    }
    if (type.id === 'mail' && data.to && !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(data.to.trim())) {
      list.push({ tone: 'warn', text: "L'adresse du destinataire semble incomplète." });
    }
    return list;
  }, [result, notice, type.id, data.url, data.to, design.logo, design.ecc, ecc]);

  const actions = useMemo(
    () => ({
      selectType: (id) => dispatch({ type: 'select-type', id }),
      setField: (key, value) => dispatch({ type: 'set-field', key, value }),
      setDesign: (patch) => dispatch({ type: 'set-design', patch }),
      setModuleColor: (value) => dispatch({ type: 'set-module-color', value }),
      toggleFinderFollow: () => dispatch({ type: 'toggle-finder-follow' }),
      setMargin: (value) => dispatch({ type: 'set-margin', value }),
      setLogo: (dataUrl, note) => dispatch({ type: 'set-logo', dataUrl, notice: note }),
      clearLogo: () => dispatch({ type: 'set-logo', dataUrl: null, notice: null }),
      setLogoPercent: (value) => dispatch({ type: 'set-logo-percent', value }),
      setEmbedded: (embedded, note) => dispatch({ type: 'set-embedded', embedded, notice: note }),
      clearEmbedded: () => dispatch({ type: 'clear-embedded' }),
      setNotice: (note) => dispatch({ type: 'notice', notice: note }),
      reset: () => dispatch({ type: 'reset' }),
    }),
    [],
  );

  return { state, type, data, payload, result, warnings, settling, actions, design };
}
