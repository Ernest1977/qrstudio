import { useRef } from 'react';
import { MAX_QR_BYTES } from '../lib/qrcode.js';
import { checkSignature, headBytes } from '../lib/files.js';

/**
 * Champs du type courant, générés depuis le schéma.
 *
 * L'original reconstruisait `innerHTML` à chaque changement de type en gardant
 * les valeurs par défaut uniquement dans le HTML (le state restait vide, d'où
 * des payloads incohérents au premier rendu) : ici les valeurs vivent dans le
 * reducer et le DOM n'est qu'une projection.
 */
export function DynamicFields({ type, data, onChange }) {
  return (
    <div className="qrc-fields">
      {type.fields.map((f) => (
        <Field key={f.key} field={f} type={type} data={data} onChange={onChange} />
      ))}
    </div>
  );
}

function Field({ field, type, data, onChange }) {
  // id dérivé du type + de la clé : stable, unique (un seul type affiché à la
  // fois), et réutilisable par les tests — contrairement à `useId()` qui produit
  // des `:r7:` changeants.
  const id = `${type.id}-${field.key}`;
  const value = data[field.key] ?? '';

  return (
    <p className={`qrc-field qrc-field--${field.type}`}>
      <label htmlFor={id}>{field.label}</label>
      {field.type === 'textarea' ? (
        <textarea
          id={id}
          rows={3}
          value={value}
          placeholder={field.placeholder || undefined}
          onChange={(e) => onChange(field.key, e.target.value)}
        />
      ) : field.type === 'select' ? (
        <select id={id} value={value} onChange={(e) => onChange(field.key, e.target.value)}>
          {field.options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      ) : (
        <input
          id={id}
          type={field.type}
          value={value}
          placeholder={field.placeholder || undefined}
          autoComplete="off"
          onChange={(e) => onChange(field.key, e.target.value)}
        />
      )}
      {field.hint ? (
        <small className="qrc-hint">{field.hint}</small>
      ) : null}
    </p>
  );
}

/**
 * Import de fichier (types « partage de fichier »).
 *
 * Contrôles ajoutés par rapport à l'origine : le type MIME est enfin vérifié
 * (`file.mime` était déclaré mais ignoré), le nom aussi, et la limite de taille
 * découle de la capacité réelle du QR plutôt que d'un plafond arbitraire.
 */
export function FileDrop({ file, embedded, bytes = 0, onPick, onReject }) {
  const inputRef = useRef(null);

  const handleFiles = async (list) => {
    const f = list?.[0];
    if (!f) return;
    const lower = f.name.toLowerCase();
    if (!matchMime(file.mime, f, lower)) {
      onReject(`« ${f.name} » n'est pas un fichier ${labelFor(file.mime)} — import refusé.`);
      return;
    }
    // Le champ `accept` de <input> ne filtre que la boîte de dialogue : on
    // regarde aussi les premiers octets, sinon un .pdf renommé .png (ou
    // l'inverse) passe et le QR encode un fichier que personne n'ouvrira.
    const { ok, reason } = checkSignature(await headBytes(f), file.mime);
    if (!ok) {
      onReject(`« ${f.name} » : ${reason}`);
      return;
    }
    const approxBytes = Math.ceil(f.size / 3) * 4 + `data:${f.type || 'application/octet-stream'};base64,`.length;
    if (approxBytes > MAX_QR_BYTES) {
      onReject(
        `« ${f.name} » fait ${(f.size / 1024).toFixed(1)} Ko : trop volumineux pour être intégré ` +
          `(la limite utile est d'environ ${Math.floor(((MAX_QR_BYTES - 80) * 3) / 4 / 1024)} Ko en base64). ` +
          'Hébergez le fichier en ligne (Google Drive, Dropbox, votre site…) et collez son URL.',
      );
      return;
    }
    const reader = new FileReader();
    reader.onload = () => onPick({ name: f.name, size: f.size, dataUrl: String(reader.result) });
    reader.onerror = () => onReject('Lecture du fichier impossible.');
    reader.readAsDataURL(f);
  };

  return (
    <div className="qrc-filedrop">
      <button
        type="button"
        className={`qrc-drop${embedded ? ' is-filled' : ''}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          handleFiles(e.dataTransfer?.files);
        }}
      >
        {embedded
          ? `✓ ${embedded.name} intégré (${(embedded.size / 1024).toFixed(1)} Ko) — cliquez pour remplacer`
          : `📎 ${file.hint}${bytes ? ` — ${bytes.toLocaleString('fr-FR')} o encodés` : ''}`}
      </button>
      <input ref={inputRef} id="qrc-file-input" type="file" accept={file.accept} hidden onChange={(e) => handleFiles(e.target.files)} />
      <small className="qrc-hint">
        Fichier intégré = contenu du QR. Un QR ne peut pas héberger un vrai document : au-delà de quelques centaines
        d'octets, préférez l'URL.
      </small>
    </div>
  );
}

const MIME_LABELS = { pdf: 'PDF', word: 'Word', excel: 'Excel', image: 'image' };
const labelFor = (mime) => MIME_LABELS[mime] ?? mime;

function matchMime(mime, file, lowerName) {
  if (mime === 'image') return /^image\/(png|jpe?g)$/.test(file.type) || /\.(png|jpe?g)$/.test(lowerName);
  if (mime === 'pdf') return file.type === 'application/pdf' || lowerName.endsWith('.pdf');
  if (mime === 'word') return /word|msword/.test(file.type) || /\.(docx?|rtf)$/.test(lowerName);
  if (mime === 'excel') return /excel|ms-excel/.test(file.type) || /\.(xlsx?|csv)$/.test(lowerName);
  return true;
}

/** Jauge de capacité : un QR sature vers 2,3 Ko en correction M. */
export function CapacityBar({ bytes, ok }) {
  const ratio = Math.min(1, bytes / MAX_QR_BYTES);
  const tone = !ok ? 'is-error' : ratio > 0.9 ? 'is-full' : ratio > 0.7 ? 'is-high' : 'is-ok';
  return (
    <div className={`qrc-capacity ${tone}`} aria-label={`Contenu : ${bytes} octets sur ${MAX_QR_BYTES} utilisables`}>
      <div className="qrc-capacity-bar">
        <span style={{ width: `${Math.max(2, ratio * 100)}%` }} />
      </div>
      <small>
        {bytes.toLocaleString('fr-FR')} o / {MAX_QR_BYTES.toLocaleString('fr-FR')} o
      </small>
    </div>
  );
}
