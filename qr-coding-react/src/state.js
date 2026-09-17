/**
 * State du studio.
 *
 * L'original gardait un objet mutable global `state` + un `state.data` vidé à
 * chaque changement de type (les valeurs par défaut ne vivaient que dans le DOM).
 * Ici : un reducer, et des données conservées **par type** — revenir sur un type
 * ne perd plus la saisie.
 */

import { TYPES, getType, defaultData, initialDataByType } from './lib/types.js';

export const DESIGN_DEFAULTS = {
  style: 'square',
  finderStyle: 'square',
  moduleColor: '#171a26',
  background: '#ffffff',
  finderColor: '#171a26',
  finderFollowsModules: true,
  /** 40 px = 4 modules au grain du rendu : le minimum de la norme ISO 18004. */
  margin: 40,
  logo: null,
  logoPercent: 22,
  /** M = marge de sécurité pour un logo ; l'original codait en M aussi. */
  ecc: 'M',
};

const dataByType = () => initialDataByType();

export const initialState = () => ({
  type: 'url',
  dataByType: dataByType(),
  design: { ...DESIGN_DEFAULTS },
  /** Fichier importé pour les types « partage de fichier » : { name, bytes, dataUrl }. */
  embedded: null,
  /** Message d'avertissement (capacité, fichier, logo…). */
  notice: null,
  /** Clé incrémentée à chaque action annulant un import. */
  resetToken: 0,
});

export function reducer(state, action) {
  switch (action.type) {
    case 'select-type': {
      if (action.id === state.type) return state;
      return {
        ...state,
        type: action.id,
        embedded: null,
        notice: null,
        dataByType: state.dataByType[action.id] ? state.dataByType : { ...state.dataByType, [action.id]: defaultData(getType(action.id)) },
        resetToken: state.resetToken + 1,
      };
    }

    case 'set-field': {
      const current = state.dataByType[state.type] ?? {};
      // surtout pas de .trim() ici : sur un champ contrôlé, effacer l'espace en
      // fin de saisie empêche d'écrire quoi que ce soit contenant un espace.
      const value = action.value;
      return {
        ...state,
        embedded: state.embedded ? null : state.embedded,
        notice: state.notice?.kind === 'file' ? null : state.notice,
        dataByType: { ...state.dataByType, [state.type]: { ...current, [action.key]: value } },
      };
    }

    case 'set-design':
      return { ...state, design: { ...state.design, ...action.patch } };

    case 'set-module-color':
      return {
        ...state,
        design: {
          ...state.design,
          moduleColor: action.value,
          ...(state.design.finderFollowsModules ? { finderColor: action.value } : {}),
        },
      };

    case 'toggle-finder-follow': {
      const follow = !state.design.finderFollowsModules;
      return {
        ...state,
        design: {
          ...state.design,
          finderFollowsModules: follow,
          finderColor: follow ? state.design.moduleColor : state.design.finderColor,
        },
      };
    }

    case 'set-margin': {
      const margin = clamp(Number(action.value), 0, 96);
      return { ...state, design: { ...state.design, margin: Number.isFinite(margin) ? margin : DESIGN_DEFAULTS.margin } };
    }

    case 'set-logo':
      return { ...state, design: { ...state.design, logo: action.dataUrl }, notice: action.notice ?? null };

    case 'set-logo-percent':
      return { ...state, design: { ...state.design, logoPercent: clamp(Number(action.value), 10, 45) } };

    case 'set-embedded':
      return { ...state, embedded: action.embedded, notice: action.notice ?? null };

    case 'clear-embedded':
      return { ...state, embedded: null, notice: null };

    case 'notice':
      return { ...state, notice: action.notice ?? null };

    case 'reset':
      return { ...initialState(), design: state.design };

    default:
      return state;
  }
}

export const clamp = (value, min, max) => (Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : min);

export const TYPE_IDS = TYPES.map((t) => t.id);

/** Données du type courant. */
export const currentData = (state) => state.dataByType[state.type] ?? defaultData(getType(state.type));
