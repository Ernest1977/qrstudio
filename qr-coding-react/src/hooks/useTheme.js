import { useCallback, useEffect, useState } from 'react';

const STORAGE_KEY = 'qr-coding:theme';

/** Thème lisible dès le premier rendu (voir inline script dans index.html). */
export function readInitialTheme() {
  if (typeof window === 'undefined' || typeof document === 'undefined') return 'dark';
  const attr = document.documentElement.getAttribute('data-theme');
  if (attr === 'light' || attr === 'dark') return attr;
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === 'light' || saved === 'dark') return saved;
  } catch {
    /* stockage indisponible (navigation privée) : on garde le thème par défaut */
  }
  return 'dark';
}

export function useTheme() {
  const [theme, setTheme] = useState(readInitialTheme);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'light') root.setAttribute('data-theme', 'light');
    else root.removeAttribute('data-theme');
    root.style.colorScheme = theme === 'light' ? 'light' : 'dark';
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  const toggle = useCallback(() => setTheme((t) => (t === 'light' ? 'dark' : 'light')), []);
  return { theme, toggle, label: theme === 'light' ? 'Passer en thème sombre' : 'Passer en thème clair' };
}
