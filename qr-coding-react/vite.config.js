import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// host: true + allowedHosts → le serveur répond aussi derrière le proxy d'aperçu.
export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: Number(process.env.PORT ?? 5173),
    strictPort: true,
    allowedHosts: true,
    // Proxifié vers l'API de dev (voir api/: `make run`). Le navigateur ne doit jamais parler à
    // localhost:8000 directement : derrière le proxy d'aperçu, « localhost » serait le poste de
    // l'utilisateur, pas la machine qui sert le service. Avec ces règles, `/api/v1/...` part du
    // même origines que la page, et les cookies de session du back-end restent utilisables.
    proxy: {
      // `/manage/` : l'espace admin Django, relie depuis /facturation par un lien simple. Sans cette
      // entree, le lien ne marche qu'en production (ou quand Django sert le front) et casse en dev.
      // Le motif ancre est volontaire : `/manage` seul ferait aussi correspondre `/manage-9f2`, l'URL de
      // django-admin, et la regle la plus generale gagnerait sur la plus specifique.
      '^/manage/': { target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000', changeOrigin: true },
      '/api': { target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000', changeOrigin: true },
      '/r': { target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000', changeOrigin: true },
      '/healthz': { target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000', changeOrigin: true },
      '/manage-9f2': { target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000', changeOrigin: true },
      '/accounts': { target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  preview: { host: '0.0.0.0', port: 4173, allowedHosts: true },
  build: { target: 'es2020', sourcemap: false },
});
