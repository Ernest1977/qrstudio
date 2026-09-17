import { useCallback, useState } from 'react';
import { AuthPanel } from './AuthPanel.jsx';
import { CodesPromo } from './CodesPromo.jsx';
import { LIBELLES_ABONNEMENT, LIBELLES_MOBILE, formaterMontant, useFacturation } from '../hooks/useFacturation.js';
import { BILL_COPY, LIMITES_LABELS, PALIERS_LABELS, PALIERS_SURTITRE } from '../content.js';

/**
 * `/facturation` : la grille réelle, le bouton d'abonnement, et le suivi d'un paiement mobile.
 *
 * Ce que cette page ne fait pas, et c'est le point : elle n'accorde rien. Le clic « S'abonner » ouvre la
 * page Stripe, le mobile renvoie une instruction à valider sur le téléphone, et le palier n'apparaît ici
 * que quand `/billing/etat` le confirme — donc quand le webhook est passé côté serveur.
 */

function Badge({ tone = 'info', children }) {
  return <span className={`qrc-badge is-${tone}`}>{children}</span>;
}

function ResumeLicence({ licence, abonnement, acorde, attendStripe, onGerer }) {
  const periode = abonnement && abonnement.periode_fin ? new Date(abonnement.periode_fin) : null;
  const sursis = abonnement && abonnement.en_sursis_jusqu_a ? new Date(abonnement.en_sursis_jusqu_a) : null;
  return (
    <section className="qrc-card qrc-bill-resume" aria-live="polite">
      <div>
        <p className="qrc-bill-kicker">Votre palier</p>
        <h2>
          {licence ? licence.nom : 'Lecture en cours…'}{' '}
          {licence ? <span className="qrc-bill-prix">{licence.prix_eur} €/mois</span> : null}
        </h2>
        <p className="qrc-muted">
          {attendStripe
            ? 'Paiement en cours d’enregistrement côté service…'
            : acorde
              ? 'Accord en cours de validité : les fonctionnalités du palier sont ouvertes.'
              : 'Aucun droit payant actif : le compte tourne sur le palier Gratuit.'}
        </p>
        {abonnement ? (
          <ul className="qrc-bill-meta">
            <li>
              Statut <Badge tone={abonnement.en_registre ? 'ok' : 'warn'}>{LIBELLES_ABONNEMENT[abonnement.statut] || abonnement.statut}</Badge>
            </li>
            {periode ? <li>Échéance le {periode.toLocaleDateString('fr-FR')}</li> : null}
            {sursis ? <li>Maintenu jusqu’au {sursis.toLocaleDateString('fr-FR')} (paiement en retard)</li> : null}
            {abonnement.fournisseur && abonnement.fournisseur !== 'none' ? <li>Via {abonnement.fournisseur}</li> : null}
          </ul>
        ) : null}
      </div>
      <div className="qrc-bill-actions">
        {abonnement ? (
          <button type="button" className="qrc-btn qrc-btn-ghost" onClick={onGerer}>
            Gérer mon abonnement
          </button>
        ) : null}
      </div>
    </section>
  );
}

function CartePalier({ palier, connecte, occupe, exonere, onSouscrire, onMobile }) {
  const apporte = (palier.apporte || []).concat(palier.caracteristiques || []);
  const limites = Object.entries(palier.limites || {})
    .filter(([, v]) => v === null || v === -1 || v > 0)
    .map(([clef, v]) => `${LIMITES_LABELS[clef] || clef} : ${v === null || v === -1 ? 'illimité' : v}`);
  return (
    <article className={`qrc-card qrc-plan${palier.estActuel ? ' is-current' : ''}`}>
      {PALIERS_SURTITRE[palier.code] ? <p className="qrc-bill-kicker">{PALIERS_SURTITRE[palier.code]}</p> : null}
      <h3>{palier.nom}</h3>
      <p className="qrc-plan-prix">
        {palier.prix_eur} <span>€ / mois</span>
      </p>
      {apporte.length ? (
        <ul className="qrc-plan-liste">
          {apporte.map((c) => (
            <li key={c}>✓ {PALIERS_LABELS[c] || c}</li>
          ))}
        </ul>
      ) : null}
      {limites.length ? <p className="qrc-hint">{limites.join(' · ')}</p> : null}
      <div className="qrc-bill-actions">
        {palier.estActuel ? (
          <p className="qrc-hint">Palier actuellement accordé.</p>
        ) : exonere ? (
          <p className="qrc-hint">
            Compte d’administration : la grille ne s’applique pas, rien ne sera jamais débité ici.
          </p>
        ) : (
          <>
            <button
              type="button"
              className="qrc-btn qrc-btn-primary"
              disabled={!connecte || occupe}
              title={connecte ? undefined : 'Connexion requise'}
              onClick={() => onSouscrire(palier.code)}
            >
              {occupe ? 'Ouverture du paiement…' : 'S’abonner'}
            </button>
            <button type="button" className="qrc-btn qrc-btn-ghost" disabled={!connecte || occupe} onClick={() => onMobile(palier.code)}>
              Payer par mobile
            </button>
          </>
        )}
        {!connecte && !palier.estActuel ? <p className="qrc-hint">Connexion requise pour souscrire.</p> : null}
      </div>
    </article>
  );
}

function SuiviMobile({ mobile, restant, onAnnuler, onRevérifier }) {
  const [copie, setCopie] = useState(false);
  const terminal = mobile.statut !== 'pending';

  const copier = useCallback(async () => {
    if (!mobile.code_a_utiliser) return;
    try {
      // Le presse-papiers est refusé hors HTTPS et dans certains cadres : le code reste lisible à
      // l'écran, on n'en fait jamais une condition d'accès au paiement.
      await navigator.clipboard.writeText(mobile.code_a_utiliser);
      setCopie(true);
      window.setTimeout(() => setCopie(false), 2000);
    } catch {
      setCopie(false);
    }
  }, [mobile.code_a_utiliser]);

  return (
    <section className="qrc-card qrc-mobile" aria-live="polite">
      <div>
        <p className="qrc-bill-kicker">Paiement mobile</p>
        <h3>{LIBELLES_MOBILE[mobile.statut] || mobile.statut}</h3>
        {mobile.instruction ? <p className="qrc-muted">{mobile.instruction}</p> : null}
        {mobile.montant_affiche ? (
          // Le montant que le client doit valider sur son téléphone, tel que le réseau le verra : devise
          // locale comprise. `montant_centimes` reste la valeur de la grille, dans nos livres.
          <p className="qrc-mobile-montant">{mobile.montant_affiche}</p>
        ) : mobile.montant_centimes != null ? (
          <p className="qrc-mobile-montant">{formaterMontant(mobile.montant_centimes, mobile.devise)}</p>
        ) : null}
        {mobile.montant_affiche && mobile.montant_centimes != null ? (
          <p className="qrc-hint">
            Grille : {formaterMontant(mobile.montant_centimes, 'eur')} — le montant ci-dessus est celui
            demandé au réseau mobile, conversion incluse.
          </p>
        ) : null}
      </div>

      {mobile.code_a_utiliser ? (
        <div className="qrc-mobile-code">
          <p className="qrc-bill-kicker">Code à saisir sur votre téléphone</p>
          <p className="qrc-code">
            {mobile.code_a_utiliser}
          </p>
          <button type="button" className="qrc-btn qrc-btn-ghost qrc-mini" onClick={copier}>
            {copie ? 'Copié' : 'Copier'}
          </button>
        </div>
      ) : null}

      <div className="qrc-bill-actions qrc-mobile-actions">
        {!terminal && restant ? (
          <p className={`qrc-countdown${restant.epuise ? ' is-out' : ''}`}>
            {restant.epuise ? 'Délai dépassé' : `Expire dans ${restant.libelle}`}
          </p>
        ) : null}
        <span className="qrc-hint">Réf. {mobile.reference}</span>
        {!terminal ? (
          <button type="button" className="qrc-btn qrc-btn-ghost qrc-mini" onClick={onRevérifier}>
            J’ai confirmé
          </button>
        ) : null}
        <button type="button" className="qrc-btn qrc-btn-ghost qrc-mini" onClick={onAnnuler}>
          {terminal ? 'Fermer' : 'Annuler le suivi'}
        </button>
      </div>
    </section>
  );
}

export function Facturation() {
  const {
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
    effacerNotice,
    actions,
  } = useFacturation();
  const [palierMobile, setPalierMobile] = useState('');
  const [telephone, setTelephone] = useState('');
  const [erreurMobile, setErreurMobile] = useState('');

  const connecte = session.etat === 'connecte';
  // Le serveur decide seul de l'exemption (`apps/accounts/exemption.py`). Ici on ne fait que la dire :
  // aucun bouton cache cote client n'a jamais empeche une facturation, et aucun d'entre eux n'en accorde une.
  const exonere = Boolean(session.utilisateur && session.utilisateur.exonere_de_facturation);

  const ouvrirMobile = useCallback(
    (code) => {
      if (!connecte) return;
      setPalierMobile(code);
    },
    [connecte],
  );

  const lancerMobile = useCallback(
    async (e) => {
      e.preventDefault();
      // On ne fait pas confiance au seul pattern HTML : un `+` manquant part chez l'agregateur et
      // revient en rejet opaque, trois minutes plus tard.
      if (!/^\+?[0-9]{8,15}$/.test(telephone.trim())) {
        setErreurMobile('Numéro au format international, avec l’indicatif pays (ex. +39 333 123 4567).');
        return;
      }
      setErreurMobile('');
      await actions.demarrerMobile(palierMobile, telephone.trim());
    },
    [telephone, palierMobile, actions],
  );

  return (
    <div className="qrc-bill">
      <header className="qrc-bill-entete">
        <h1>Facturation</h1>
        <p className="qrc-muted">{BILL_COPY.intro}</p>
        {session.etat === 'connecte' && session.utilisateur ? (
          <p className="qrc-bill-compte">
            {session.utilisateur.email}{' '}
            <button type="button" className="qrc-btn qrc-btn-ghost qrc-mini" onClick={actions.deconnexion}>
              Se déconnecter
            </button>
          </p>
        ) : null}
      </header>

      {notice ? (
        <p className={`qrc-notice is-${notice.tone}`} role="status">
          {notice.text}{' '}
          <button type="button" className="qrc-notice-close" onClick={effacerNotice} aria-label="Masquer">
            ✕
          </button>
        </p>
      ) : null}

      {exonere ? (
        <aside className="qrc-bill-exemption" role="note">
          <p>
            <strong>Session d’administration — facturation non applicable.</strong> Les paliers restent
            affichés pour référence : ils décrivent ce que paient les clients, pas ce que doit ce compte.
          </p>
          <p>
            Les QR — statiques comme dynamiques — se créent gratuitement dans{' '}
            <a className="qrc-lien-externe" href="/manage/qr/creer/">l’espace admin</a>. Ils y sont marqués{' '}
            <code>origine = admin</code>, ce qui les garde hors des revenus et les rend comptables dans
            l’analytique.
          </p>
        </aside>
      ) : null}

      {session.etat === 'chargement' ? <p className="qrc-muted">Chargement du compte…</p> : null}

      {!connecte && session.etat !== 'chargement' ? <AuthPanel onConnecte={actions.connexion} /> : null}

      {connecte ? <ResumeLicence licence={licence} abonnement={abonnement} acorde={acorde} attendStripe={attendStripe} onGerer={actions.gererAbonnement} /> : null}

      <section aria-label="Paliers">
        <h2 className="qrc-sec-title">Choisir un palier</h2>
        <div className="qrc-plans">
          {cartes.length ? (
            cartes.map((p) => (
              <CartePalier
                key={p.code}
                palier={p}
                connecte={connecte}
                occupe={Boolean(occupes[p.code])}
                exonere={exonere}
                onSouscrire={(code) => actions.souscrire(code)}
                onMobile={ouvrirMobile}
              />
            ))
          ) : (
            <p className="qrc-muted">La grille des paliers n’a pas pu être lue — rechargez la page.</p>
          )}
        </div>
        <p className="qrc-hint">{BILL_COPY.note}</p>
      </section>

      {connecte ? <CodesPromo connecte={connecte} /> : null}

      {connecte && palierMobile && !mobile ? (
        <form className="qrc-card qrc-mobile-form" onSubmit={lancerMobile}>
          <h3>Payer {palierMobile} par mobile</h3>
          <div className="qrc-field">
            <label htmlFor="qrc-mobile-tel">Numéro à débiter</label>
            <input
              id="qrc-mobile-tel"
              type="tel"
              inputMode="tel"
              autoComplete="tel"
              required
              value={telephone}
              onChange={(e) => setTelephone(e.target.value)}
              placeholder="+39 333 123 4567"
            />
          </div>
          <div className="qrc-bill-actions">
            <button type="submit" className="qrc-btn qrc-btn-primary" disabled={Boolean(occupes[`mobile:${palierMobile}`])}>
              {occupes[`mobile:${palierMobile}`] ? 'Envoi en cours…' : 'Demander le paiement'}
            </button>
            <button type="button" className="qrc-btn qrc-btn-ghost" onClick={() => setPalierMobile('')}>
              Retour
            </button>
          </div>
          {erreurMobile ? <p className="qrc-notice is-warn">{erreurMobile}</p> : null}
          <p className="qrc-hint">{BILL_COPY.mobile}</p>
        </form>
      ) : null}

      {mobile ? (
        <SuiviMobile
          mobile={mobile}
          restant={restant}
          onAnnuler={actions.annulerSuivi}
          onRevérifier={actions.revérifierMobile}
        />
      ) : null}
    </div>
  );
}
