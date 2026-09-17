import { TYPES, TYPE_COUNT } from './lib/types.js';
import { Header, Hero, Features, HowItWorks, Footer } from './components/Layout.jsx';
import { Studio } from './components/Studio.jsx';
import { Facturation } from './components/Facturation.jsx';
import { ROUTE_FACTURATION, useRoute } from './lib/router.js';
import { FEATURES, STEPS, FOOTER_NOTE } from './content.js';

/**
 * L'application tient en deux routes : le studio (page publique, hors ligne) et `/facturation`, que le
 * backend utilise comme URL de retour de Stripe. Pas de routeur embarqué pour si peu — voir `lib/router.js`.
 */
export default function App() {
  const { route } = useRoute();
  return (
    <div className="qrc-app" id="top">
      <Header />
      <main className="qrc-wrap">{route === ROUTE_FACTURATION ? <Facturation /> : <Accueil types={TYPES} typesCount={TYPE_COUNT} />}</main>
      <Footer note={FOOTER_NOTE} />
    </div>
  );
}

function Accueil({ types, typesCount }) {
  return (
    <>
      <Hero />
      <Studio types={types} />
      <Features types={typesCount} features={FEATURES} />
      <HowItWorks steps={STEPS} />
    </>
  );
}
