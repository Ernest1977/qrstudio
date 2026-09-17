import { useTheme } from '../hooks/useTheme.js';
import { Lien } from './Lien.jsx';

const LINKS = [
  { href: '#studio', label: 'Créer' },
  { href: '#features', label: 'Fonctionnalités' },
  { href: '#how', label: 'Comment ça marche' },
  // Vraie URL, pas une ancre : `/facturation` est la page que `cancel_url` du checkout designe cote
  // serveur. Un lien `#facturation` aurait casse le bouton precedent du navigateur.
  { href: '/facturation', label: 'Facturation' },
];

export function Header() {
  const { theme, toggle, label } = useTheme();
  return (
    <header className="qrc-header">
      <div className="qrc-wrap qrc-nav">
        <Lien className="qrc-logo" to="#top">
          <span className="qrc-logo-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="currentColor">
              <path d="M3 3h8v8H3zM13 3h8v8h-8zM3 13h8v8H3zM13 13h3v3h-3zM18 13h3v3h-3zM13 18h3v3h-3zM18 18h3v3h-3z" />
            </svg>
          </span>
          QR Coding
        </Lien>
        <nav className="qrc-nav-links" aria-label="Navigation principale">
          {LINKS.map((l) => (
            <Lien key={l.href} to={l.href} className={l.href.startsWith('/') ? 'qrc-nav-bill' : undefined}>
              {l.label}
            </Lien>
          ))}
          <button type="button" className="qrc-btn-theme" onClick={toggle} title={label} aria-label={label} aria-pressed={theme === 'light'}>
            {theme === 'light' ? '🌙' : '☀️'}
          </button>
        </nav>
      </div>
    </header>
  );
}

export function Hero() {
  return (
    <div className="qrc-hero">
      <h1>
        Créez des <span>QR codes</span> élégants en quelques secondes
      </h1>
      <p>
        URL, carte de visite, 10 réseaux sociaux, Wi-Fi, PDF, Word, Excel, images, SMS, e-mail, événement… Personnalisez couleurs, formes et logo, puis
        exportez en haute qualité. Gratuit, sans inscription.
      </p>
    </div>
  );
}

export function Features({ types, features }) {
  return (
    <section id="features" className="qrc-section">
      <h2 className="qrc-sec-title">Fonctionnalités</h2>
      <div className="qrc-feat-grid">
        {features.map((f) => (
          <article className="qrc-feat" key={f.title}>
            <div className="qrc-feat-ico" aria-hidden="true">
              {f.ico}
            </div>
            <h3>{f.title}</h3>
            <p>{typeof f.text === 'function' ? f.text(types) : f.text}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

export function HowItWorks({ steps }) {
  return (
    <section id="how" className="qrc-section">
      <h2 className="qrc-sec-title">Comment ça marche</h2>
      <div className="qrc-steps">
        {steps.map((s, i) => (
          <div className="qrc-step" key={s.title}>
            <div className="qrc-step-num" aria-hidden="true">
              {i + 1}
            </div>
            <h3>{s.title}</h3>
            <p>{s.text}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

export function Footer({ note }) {
  return (
    <footer className="qrc-footer">
      <div className="qrc-wrap">{note}</div>
    </footer>
  );
}
