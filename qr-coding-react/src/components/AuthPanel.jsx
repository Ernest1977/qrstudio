import { useCallback, useEffect, useRef, useState } from 'react';
import { ErreurApi, api } from '../lib/api.js';

/**
 * Connexion / inscription, posés sur `/facturation` uniquement.
 *
 * Le site reste utilisable sans compte (tout le studio se passe dans le navigateur) ; seule la
 * souscription exige une session. Plutôt qu'un écran de login global, on met l'effort là où il sert :
 * la page de caisse.
 *
 * Deux détails qui viennent du backend, pas du goût du moment :
 *  - `accept_terms` est obligatoire à l'inscription (un 400 de champ manquant sinon) ;
 *  - après inscription, le compte a un code de vérification en attente. On propose donc le champ de
 *    saisie **ici**, sinon le message « saisissez le code reçu » serait une promesse sans fenêtre.
 */
export function AuthPanel({ onConnecte }) {
  const [mode, setMode] = useState('connexion');
  const [email, setEmail] = useState('');
  const [motDePasse, setMotDePasse] = useState('');
  const [accepte, setAccepte] = useState(false);
  const [consent, setConsent] = useState(true);
  const [code, setCode] = useState('');
  const [attendu, setAttendu] = useState(false);
  const [message, setMessage] = useState(null);
  const champRef = useRef(null);

  useEffect(() => {
    if (champRef.current && mode !== 'connexion') champRef.current.focus();
  }, [mode]);

  const soumettre = useCallback(
    async (e) => {
      e.preventDefault();
      setAttendu(true);
      setMessage(null);
      try {
        if (mode === 'connexion') {
          await onConnecte(email.trim(), motDePasse);
          return;
        }
        if (mode === 'inscription') {
          await api.inscription({ email: email.trim(), password: motDePasse, accept_terms: accepte, consent_tracking: consent });
          setMode('verifier');
          setMessage({ tone: 'ok', text: 'Compte créé. Un code à 6 chiffres vient d’être envoyé.' });
          return;
        }
        await api.verifier(email.trim(), code.trim());
        await onConnecte(email.trim(), motDePasse);
      } catch (err) {
        const details = err instanceof ErreurApi && err.details ? Object.values(err.details).flat().join(' ') : '';
        setMessage({ tone: 'err', text: `${err.message}${details ? ` — ${details}` : ''}` });
      } finally {
        setAttendu(false);
      }
    },
    [mode, email, motDePasse, accepte, consent, code, onConnecte],
  );

  const renvoyer = useCallback(async () => {
    try {
      await api.renvoyerCode(email.trim());
      setMessage({ tone: 'info', text: 'Code renvoyé (un seul envoi est accepté par minute).' });
    } catch (err) {
      setMessage({ tone: 'err', text: err.message });
    }
  }, [email]);

  return (
    <section className="qrc-card qrc-authbox" aria-label="Connexion">
      <h2>{mode === 'inscription' ? 'Créer un compte' : mode === 'verifier' ? 'Confirmer l’adresse e-mail' : 'Se connecter'}</h2>
      <p className="qrc-muted">
        {mode === 'verifier'
          ? 'La souscription fonctionne aussi sans vérification, mais un compte non confirmé ne peut pas recevoir ses e-mails de reçu.'
          : 'Un compte est nécessaire pour souscrire : la facture et le palier sont attachés à une adresse, pas à un navigateur.'}
      </p>

      <form onSubmit={soumettre} noValidate>
        <div className="qrc-field">
          <label htmlFor="qrc-auth-email">Adresse e-mail</label>
          <input
            id="qrc-auth-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="vous@exemple.com"
          />
        </div>

        {mode !== 'verifier' ? (
          <div className="qrc-field">
            <label htmlFor="qrc-auth-password">Mot de passe</label>
            <input
              id="qrc-auth-password"
              ref={champRef}
              type="password"
              autoComplete={mode === 'inscription' ? 'new-password' : 'current-password'}
              required
              minLength={mode === 'inscription' ? 12 : undefined}
              value={motDePasse}
              onChange={(e) => setMotDePasse(e.target.value)}
            />
            {mode === 'inscription' ? <p className="qrc-hint">12 caractères minimum.</p> : null}
          </div>
        ) : (
          <div className="qrc-field">
            <label htmlFor="qrc-auth-code">Code de confirmation</label>
            <input
              id="qrc-auth-code"
              ref={champRef}
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              required
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/[^0-9]/g, ''))}
            />
          </div>
        )}

        {mode === 'inscription' ? (
          <>
            <label className="qrc-check">
              <input type="checkbox" checked={accepte} onChange={(e) => setAccepte(e.target.checked)} />
              <span>J’accepte les conditions d’utilisation et la politique de confidentialité.</span>
            </label>
            <label className="qrc-check">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              <span>J’accepte que les scans de mes QR dynamiques soient mesurés (pays, heure).</span>
            </label>
          </>
        ) : null}

        <div className="qrc-bill-actions">
          <button type="submit" className="qrc-btn qrc-btn-primary" disabled={attendu}>
            {mode === 'connexion' ? 'Se connecter' : mode === 'inscription' ? 'Créer le compte' : 'Confirmer'}
          </button>
          {mode === 'verifier' ? (
            <button type="button" className="qrc-btn qrc-btn-ghost" onClick={renvoyer} disabled={attendu}>
              Renvoyer le code
            </button>
          ) : null}
          <button
            type="button"
            className="qrc-btn qrc-btn-ghost"
            onClick={() => setMode(mode === 'connexion' ? 'inscription' : 'connexion')}
            disabled={attendu}
          >
            {mode === 'connexion' ? 'Créer un compte' : 'J’ai déjà un compte'}
          </button>
        </div>

        {message ? <p className={`qrc-notice is-${message.tone}`}>{message.text}</p> : null}
      </form>
    </section>
  );
}
