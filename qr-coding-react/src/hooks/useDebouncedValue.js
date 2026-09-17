import { useEffect, useState } from 'react';

/** Valeur retardée — évite de reconstruire le SVG à chaque frappe. */
export function useDebouncedValue(value, delay = 120) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
}
