/**
 * Contrôle minimal d'intégrité des fichiers importés.
 *
 * L'original déclarait un `mime` par type sans jamais le lire : n'importe quel
 * fichier était accepté dans n'importe quel emplacement. Un `accept=` côté HTML
 * ne filtre que la boîte de dialogue (par extension), pas le contenu — on
 * vérifie donc la signature binaire, ce qui est la seule chose réellement
 * vérifiable sans dépendance lourde.
 */

const ascii = (bytes, start, end) => String.fromCharCode(...bytes.slice(start, end));
const starts = (bytes, sig) => sig.every((v, i) => bytes[i] === v);

const ZIP = [0x50, 0x4b, 0x03, 0x04]; // docx / xlsx / zip
const OLE = [0xd0, 0xcf, 0x11, 0xe0]; // doc / xls (format composé OLE2)
const PNG = [0x89, 0x50, 0x4e, 0x47];
const JPEG = [0xff, 0xd8, 0xff];
const GIF87 = [0x47, 0x49, 0x46, 0x38, 0x37];
const GIF89 = [0x47, 0x49, 0x46, 0x38, 0x39];

/** Ce que la signature observée autorise comme formats. */
export function describeSignature(bytes) {
  const b = Array.from(bytes ?? []);
  if (starts(b, PNG)) return { kind: 'image', label: 'PNG' };
  if (starts(b, JPEG)) return { kind: 'image', label: 'JPEG' };
  if (starts(b, GIF87) || starts(b, GIF89)) return { kind: 'image', label: 'GIF' };
  if (ascii(b, 0, 5) === '<svg ' || ascii(b, 0, 4) === '<?xm' || ascii(b, 0, 5) === '<?xml') return { kind: 'image', label: 'SVG' };
  if (ascii(b, 0, 5) === '%PDF-') return { kind: 'pdf', label: 'PDF' };
  if (starts(b, ZIP)) return { kind: 'office-zip', label: 'docx/xlsx (ZIP)' };
  if (starts(b, OLE)) return { kind: 'office-ole', label: 'doc/xls (OLE2)' };
  if (ascii(b, 0, 5) === '{\\rtf') return { kind: 'office-zip', label: 'RTF' };
  return { kind: 'unknown', label: 'inconnu' };
}

/**
 * @param {Uint8Array} bytes premiers octets du fichier
 * @param {'pdf'|'word'|'excel'|'image'} expected
 * @returns {{ok:boolean, reason?:string, found:string}}
 */
export function checkSignature(bytes, expected) {
  const found = describeSignature(bytes);
  const ok =
    expected === 'pdf'
      ? found.kind === 'pdf'
      : expected === 'image'
        ? found.kind === 'image'
        : expected === 'word'
          ? found.kind === 'office-zip' || found.kind === 'office-ole'
          : expected === 'excel'
            ? found.kind === 'office-zip' || found.kind === 'office-ole'
            : true;
  return {
    ok,
    found: found.label,
    reason: ok
      ? undefined
      : `signature ${found.label} au lieu d'un fichier ${expected.toUpperCase()} — le contenu ne correspond pas à l'extension.`,
  };
}

/** Lit les `n` premiers octets d'un File (sans charger tout le fichier). */
export async function headBytes(file, n = 16) {
  const slice = typeof file?.slice === 'function' ? file.slice(0, n) : null;
  if (!slice) return new Uint8Array(0);
  if (slice.arrayBuffer) return new Uint8Array(await slice.arrayBuffer());
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(new Uint8Array(fr.result));
    fr.onerror = () => resolve(new Uint8Array(0));
    fr.readAsArrayBuffer(slice);
  });
}
