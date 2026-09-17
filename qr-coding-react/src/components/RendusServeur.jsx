import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, chargerRendu } from '../lib/api.js';
import { downloadBlob } from '../lib/export.js';
import {
  BORNES_RENDU,
  STYLE_DEFAUT,
  borner,
  decrirePreuve,
  estCapaciteRefusee,
  framesStatiques,
  lirePreuve,
  urlApercu,
} from '../lib/rendus.js';

/**
 * Rendus vérifiés par le serveur : style artistique et GIF animé.
 *
 * L'aperçu ci-dessous n'est **pas** celui du canvas local : c'est exactement le fichier que le serveur produit
 * (`/api/v1/qr/apercu/`), relû et décodé avant d'être servi. C'est le seul moyen pour que « ce que je
 * vois » et « ce que j'imprime » soient le même octet — et pour qu'un style trop faible se signale ici,
 * pas sur 500 flyers.
 */
export function RendusServeur({ payload, design }) {
  const [styles, setStyles] = useState([]);
  const [style, setStyle] = useState(design?.art || STYLE_DEFAUT);
  const [anime, setAnime] = useState((design?.animation || 'aucune') !== 'aucune');
  const [frames, setFrames] = useState(borner(design?.frames, BORNES_RENDU.frames));
  const [liser, setLiser] = useState(borner(design?.liser, BORNES_RENDU.liser));
  const [taille, setTaille] = useState(borner(design?.taille, BORNES_RENDU.taille));
  const [preuve, setPreuve] = useState(null);
  const [avis, setAvis] = useState(null);
  const [imageCassee, setImageCassee] = useState(false);
  const [occuppe, setOccupe] = useState(false);

  useEffect(() => {
    let vivant = true;
    api
      .styles()
      .then((donnees) => {
        if (vivant) setStyles(donnees?.styles || []);
      })
      .catch(() => {
        // Pas de table de styles = selecteur reduit a l'alternative sure ; on n'invente pas un style.
        if (vivant) setStyles([]);
      });
    return () => {
      vivant = false;
    };
  }, []);

  const contenu = (payload || '').trim();
  // L'aperçu n'est demandé que si le service a répondu (la table des styles vient de l'API). Un `<img>`
  // dont la requête échoue imprime une erreur de console : nos tests de navigation considèrent — à juste
  // titre — une console bruitée comme un échec, et chez le client un cadre vide ne s'explique pas.
  const service = styles.length > 0;
  const src = useMemo(
    () =>
      contenu && service
        ? urlApercu({ fmt: anime ? 'gif' : 'art', contenu, style, taille, frames, liser })
        : '',
    [anime, contenu, frames, liser, service, style, taille],
  );

  const telecharger = useCallback(
    async (fmt) => {
      if (!contenu) {
        setAvis({ ton: 'warn', texte: 'Renseignez un contenu avant de télécharger.' });
        return;
      }
      setOccupe(true);
      try {
        const url = urlApercu({ fmt, contenu, style, taille, frames, liser });
        const { blob, reponse } = await chargerRendu(url);
        downloadBlob(blob, `qr-${fmt === 'gif' ? 'anime' : style}.${fmt === 'gif' ? 'gif' : 'png'}`);
        const p = lirePreuve(reponse);
        setPreuve(p);
        setAvis({
          ton: p.verifie ? 'ok' : 'warn',
          texte: decrirePreuve(p, { format: fmt }) || 'Image enregistrée.',
        });
      } catch (erreur) {
        setAvis({
          ton: estCapaciteRefusee(erreur) ? 'info' : 'warn',
          texte: estCapaciteRefusee(erreur)
            ? `${erreur.message} Voir la page d’abonnement.`
            : `Rendu impossible : ${erreur.message}`,
        });
      } finally {
        setOccupe(false);
      }
    },
    [contenu, frames, liser, style, taille],
  );

  const styleActif = styles.find((s) => s.code === style);

  return (
    <div className="qrc-rendus" aria-label="Rendu artistique et animation">
      <div className="qrc-field">
        <label htmlFor="qrc-style">Style du QR</label>
        <select id="qrc-style" value={style} onChange={(e) => setStyle(e.target.value)} disabled={!styles.length}>
          {styles.length ? null : <option value={style}>Styles indisponibles pour l’instant</option>}
          {styles.map((s) => (
            <option key={s.code} value={s.code}>
              {s.nom} — contraste {s.contraste}:1
            </option>
          ))}
        </select>
        {styleActif ? (
          <p className="qrc-hint">
            Contraste {styleActif.contraste}:1, modules {styleActif.module}. Le seuil de lecture est de 4,5:1 :
            en dessous, le rendu est refusé par le serveur, pas atténué.
          </p>
        ) : null}
      </div>

      <div className="qrc-field qrc-field--ligne">
        <label className="qrc-case">
          <input type="checkbox" checked={anime} onChange={(e) => setAnime(e.target.checked)} />
          Animer le liseré (GIF pour écrans et réseaux)
        </label>
      </div>

      {anime ? (
        <div className="qrc-rendus-reglages">
          <label>
            Frames {frames}
            <input
              type="range"
              min={BORNES_RENDU.frames.min}
              max={BORNES_RENDU.frames.max}
              step={BORNES_RENDU.frames.pas}
              value={frames}
              onChange={(e) => setFrames(Number(e.target.value))}
            />
          </label>
          <label>
            Liseré {liser} px
            <input
              type="range"
              min={BORNES_RENDU.liser.min}
              max={BORNES_RENDU.liser.max}
              step={BORNES_RENDU.liser.pas}
              value={liser}
              onChange={(e) => setLiser(Number(e.target.value))}
            />
          </label>
          <p className="qrc-hint">
            {framesStatiques(frames)} frames sur {frames} resteront immobiles : c’est la fenêtre de
            lecture du scanner, pas une option esthétique.
          </p>
        </div>
      ) : null}

      <div className="qrc-field">
        <label>
          Taille {taille} px
          <input
            type="range"
            min={BORNES_RENDU.taille.min}
            max={BORNES_RENDU.taille.max}
            step={BORNES_RENDU.taille.pas}
            value={taille}
            onChange={(e) => setTaille(Number(e.target.value))}
          />
        </label>
      </div>

      {src && !imageCassee ? (
        <figure className="qrc-rendu-apercu">
          {/* L'URL de l'apercu est aussi l'URL du fichier : la modifier, c'est modifier le telechargement. */}
          <img
            src={src}
            alt={`Aperçu du rendu ${anime ? 'animé' : `style ${style}`}`}
            width={220}
            height={220}
            loading="lazy"
            onError={() => {
              setImageCassee(true);
              setAvis({ ton: 'warn', texte: 'Aperçu indisponible : le service de rendu ne répond pas.' });
            }}
          />
          <figcaption className="qrc-hint">Aperçu servi par le serveur — le même octet que le téléchargement.</figcaption>
        </figure>
      ) : null}
      {!src ? (
        <p className="qrc-hint">
          {service
            ? 'Le rendu apparaît dès qu’un contenu est saisi.'
            : 'Le service de rendu ne répond pas : l’aperçu local reste disponible, le téléchargement est indisponible.'}
        </p>
      ) : null}
      {src && imageCassee ? (
        <p className="qrc-hint">Aperçu retiré de la page après un échec de chargement — les boutons, eux, réessaient.</p>
      ) : null}

      <div className="qrc-rendus-actions">
        <button type="button" className="qrc-btn" onClick={() => telecharger('art')} disabled={occuppe}>
          Télécharger le PNG artistique
        </button>
        <button type="button" className="qrc-btn" onClick={() => telecharger('gif')} disabled={occuppe || !anime}>
          Télécharger le GIF
        </button>
      </div>

      {avis ? <p className={`qrc-notice is-${avis.ton}`}>{avis.texte}</p> : null}
      {preuve && !preuve.verifie ? <p className="qrc-notice is-warn">Rendu non relecture : vérifiez le scan avant impression.</p> : null}
    </div>
  );
}
