import { useCallback, useEffect, useState } from 'react';
import { api, codesPromo } from '../lib/api.js';

/**
 * Les codes promo de la campagne — palier Entreprise.
 *
 * Ce panneau ne facture rien et ne décide rien : la validité d'un code se juge **au scan**, côté serveur
 * (`apps.qr.promo.statut`, la même fonction que la redirection). Ce que l'écran affiche est donc un état,
 * pas une promesse : la colonne « verdict » vient du serveur à chaque lecture.
 *
 * Le code n'est jamais saisi : il est généré, et il n'est jamais modifiable après coup — un flyer imprimé
 * porte un code, pas un libellé.
 */
const REMISES = [
  { code: 'pourcentage', nom: 'Pourcentage' },
  { code: 'montant', nom: 'Montant' },
  { code: 'livraison', nom: 'Livraison offerte' },
  { code: 'acces', nom: 'Accès offert' },
];

export function CodesPromo({ connecte }) {
  const [lignes, setLignes] = useState([]);
  const [etat, setEtat] = useState('repos');
  const [avis, setAvis] = useState(null);
  const [form, setForm] = useState({ libelle: '', remise_type: 'pourcentage', remise_valeur: '15', jours: '30', usages_max: '', quantite: '1' });

  const relire = useCallback(async () => {
    if (!connecte) return;
    setEtat('chargement');
    try {
      const donnees = await codesPromo.liste();
      setLignes(donnees?.results || []);
      setEtat('pret');
    } catch (erreur) {
      setEtat(erreur.statut === 402 ? 'refuse' : 'erreur');
      setAvis(erreur.message);
    }
  }, [connecte]);

  useEffect(() => {
    relire();
  }, [relire]);

  const creer = useCallback(
    async (lot) => {
      setAvis(null);
      const expire = new Date(Date.now() + Math.max(1, Number(form.jours) || 30) * 86400000).toISOString();
      const corps = {
        libelle: form.libelle.trim(),
        remise_type: form.remise_type,
        remise_valeur: form.remise_type === 'pourcentage' || form.remise_type === 'montant' ? form.remise_valeur : null,
        expire_le: expire,
        usages_max: form.usages_max ? Number(form.usages_max) : null,
      };
      try {
        if (lot) {
          const n = Math.min(500, Math.max(1, Number(form.quantite) || 1));
          await codesPromo.generer({ ...corps, quantite: n });
          setAvis(`${n} code(s) créé(s) — chaque lot est unique par compte.`);
        } else {
          await codesPromo.creer(corps);
          setAvis('Code créé.');
        }
        await relire();
      } catch (erreur) {
        setAvis(erreur.message || 'Le service a refusé la demande.');
      }
    },
    [form, relire],
  );

  const prolonger = useCallback(
    async (ligne) => {
      try {
        await codesPromo.prolonger(ligne.id, 7);
        await relire();
      } catch (erreur) {
        setAvis(erreur.message || 'Rallonge refusée.');
      }
    },
    [relire],
  );

  const clore = useCallback(
    async (ligne) => {
      try {
        await codesPromo.supprimer(ligne.id);
        await relire();
      } catch (erreur) {
        setAvis(erreur.message || 'Suppression refusée.');
      }
    },
    [relire],
  );

  if (!connecte) return null;

  if (etat === 'refuse') {
    return (
      <section className="qrc-card" aria-label="Codes promo">
        <h2>Codes promo</h2>
        <p className="qrc-notice is-info">{avis || "Les codes promo sont réservés au palier Entreprise."}</p>
      </section>
    );
  }

  return (
    <section className="qrc-card" aria-label="Codes promo">
      <h2>Codes promo de vos campagnes</h2>
      <p className="qrc-hint">
        Chaque code porte une date de fin obligatoire : le flyer reste imprimable, l’offre s’arrête toute
        seule au scan. Les usages sont comptés en direct — la colonne de droite est le verdict que verra le
        client, pas un calcul de cette page.
      </p>

      <div className="qrc-promo-form">
        <label>
          Intitulé affiché
          <input value={form.libelle} onChange={(e) => setForm({ ...form, libelle: e.target.value })} placeholder="-30% sur la cave" />
        </label>
        <label>
          Remise
          <select
            value={form.remise_type}
            onChange={(e) => setForm({ ...form, remise_type: e.target.value })}
          >
            {REMISES.map((r) => (
              <option key={r.code} value={r.code}>
                {r.nom}
              </option>
            ))}
          </select>
        </label>
        {form.remise_type === 'pourcentage' || form.remise_type === 'montant' ? (
          <label>
            Valeur
            <input
              value={form.remise_valeur}
              inputMode="decimal"
              onChange={(e) => setForm({ ...form, remise_valeur: e.target.value })}
            />
          </label>
        ) : null}
        <label>
          Jours
          <input value={form.jours} inputMode="numeric" onChange={(e) => setForm({ ...form, jours: e.target.value })} />
        </label>
        <label>
          Usages max
          <input value={form.usages_max} inputMode="numeric" onChange={(e) => setForm({ ...form, usages_max: e.target.value })} />
        </label>
        <label>
          Codes à générer
          <input value={form.quantite} inputMode="numeric" onChange={(e) => setForm({ ...form, quantite: e.target.value })} />
        </label>
        <div className="qrc-bill-actions">
          <button type="button" className="qrc-btn qrc-btn-primary" onClick={() => creer(false)}>
            Créer un code
          </button>
          <button type="button" className="qrc-btn" onClick={() => creer(true)}>
            Générer le lot
          </button>
        </div>
      </div>

      {avis ? <p className="qrc-notice is-info">{avis}</p> : null}

      {etat === 'chargement' ? <p className="qrc-muted">Lecture des codes…</p> : null}

      {lignes.length ? (
        <table className="qrc-promo-table">
          <thead>
            <tr>
              <th>Code</th>
              <th>Campagne</th>
              <th>Remise</th>
              <th>Fin</th>
              <th>Usages</th>
              <th>Verdict</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {lignes.map((ligne) => (
              <tr key={ligne.id}>
                <td>
                  <code>{ligne.code}</code>
                  {ligne.lien ? (
                    <button
                      type="button"
                      className="qrc-mini qrc-btn qrc-btn-ghost"
                      onClick={() => navigator.clipboard?.writeText(ligne.lien)}
                    >
                      Copier le lien
                    </button>
                  ) : null}
                </td>
                <td>{ligne.libelle || '—'}</td>
                <td>{remise(ligne)}</td>
                <td>{ligne.expire_le ? new Date(ligne.expire_le).toLocaleDateString('fr-FR') : '—'}</td>
                <td>
                  {ligne.usages}
                  {ligne.usages_max ? ` / ${ligne.usages_max}` : ''}
                </td>
                <td>
                  <span className={`qrc-badge is-${ligne.statut?.valide ? 'ok' : 'warn'}`}>
                    {ligne.statut?.valide ? 'valide' : ligne.statut?.motif || 'close'}
                  </span>
                  {ligne.expire_bientot ? <span className="qrc-hint"> · moins d’une semaine</span> : null}
                </td>
                <td>
                  <button type="button" className="qrc-mini qrc-btn" onClick={() => prolonger(ligne)}>
                    +7 j
                  </button>{' '}
                  <button type="button" className="qrc-mini qrc-btn qrc-btn-ghost" onClick={() => clore(ligne)}>
                    Retirer
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : etat === 'pret' ? (
        <p className="qrc-muted">Aucun code pour l’instant.</p>
      ) : null}
    </section>
  );
}

function remise(ligne) {
  if (ligne.remise_type === 'pourcentage' && ligne.remise_valeur) return `-${ligne.remise_valeur} %`;
  if (ligne.remise_type === 'montant' && ligne.remise_valeur) return `-${ligne.remise_valeur} ${ligne.devise || ''}`;
  const trouve = REMISES.find((r) => r.code === ligne.remise_type);
  return trouve ? trouve.nom : ligne.remise_type;
}
