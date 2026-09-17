import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import sharp from 'sharp';
const require = createRequire('/home/user/.probe/');
const { readBarcodesFromImageData, prepareZXingModule } = require('zxing-wasm/reader');
const QR = require('qrcode/lib/core/qrcode.js');
const wasmPath = path.resolve('node_modules/zxing-wasm/reader/zxing_reader.wasm');
await prepareZXingModule({ overrideURL: wasmPath, fallback: () => fetch('file://' + wasmPath).then(r => r.arrayBuffer()) });

async function dec(svgStr, label, opts = { density: 200 }) {
  const { data, info } = await sharp(Buffer.from(svgStr), opts).flatten({ background: '#ffffff' }).raw().ensureAlpha().toBuffer({ resolveWithObject: true });
  const r = await readBarcodesFromImageData({ data: new Uint8ClampedArray(data), width: info.width, height: info.height }, { tryHarder: true });
  console.log(label.padEnd(52), r.length ? 'DECODABLE' : 'NOT DECODABLE', `(${info.width}px)`);
  return r.length;
}

const ref = QR.create('HELLO', { errorCorrectionLevel: 'M' });
const n = ref.modules.size;              // 21
const cell = 10, margin = 40, S = n * cell + margin * 2;   // 290
const g = (r, c) => !!ref.modules.get(r, c);

// A) hand-built, plain rects, exact same canvas size/colour as the app
let rectsOnly = '';
for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (g(r, c)) rectsOnly += `<rect x="${margin + c * cell}" y="${margin + r * cell}" width="${cell}" height="${cell}"/>`;
const A = `<svg xmlns="http://www.w3.org/2000/svg" width="${S}" height="${S}" viewBox="0 0 ${S} ${S}"><rect width="${S}" height="${S}" fill="#ffffff"/><g fill="#171a26">${rectsOnly}</g></svg>`;

// B) same but finder ring drawn with a stroke (the app's technique), everything else plain
let b = '';
for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) { if (!g(r, c)) continue; const inF = (r < 7 && c < 7) || (r < 7 && c >= n - 7) || (r >= n - 7 && c < 7); if (inF) continue; b += `<rect x="${margin + c * cell}" y="${margin + r * cell}" width="${cell}" height="${cell}"/>`; }
for (const [fr, fc] of [[0, 0], [0, n - 7], [n - 7, 0]]) {
  const x = margin + fc * cell, y = margin + fr * cell, s = 7 * cell;
  b += `<rect x="${x}" y="${y}" width="${s}" height="${s}" rx="0" fill="none" stroke="#171a26" stroke-width="${cell}"/><rect x="${x + 2 * cell}" y="${y + 2 * cell}" width="${3 * cell}" height="${3 * cell}" rx="0" fill="#171a26"/>`;
}
const B = `<svg xmlns="http://www.w3.org/2000/svg" width="${S}" height="${S}" viewBox="0 0 ${S} ${S}"><rect width="${S}" height="${S}" fill="#ffffff"/><g fill="#171a26">${b}</g></svg>`;

// D) generator matrix (the lib the app uses) drawn as plain rects
const vm = await import('node:vm');
const ctx = vm.createContext({ console });
vm.runInContext('var window=globalThis;' + fs.readFileSync('/home/user/.probe/qrcode.min.js','utf8'), ctx);
const gen = vm.runInContext('var q=qrcode(0,"M"); q.addData("HELLO"); q.make(); ({n:q.getModuleCount(),m:q.isDark})', ctx);
let dr = '';
for (let r = 0; r < gen.n; r++) for (let c = 0; c < gen.n; c++) if (gen.m(r, c)) dr += `<rect x="${margin + c * cell}" y="${margin + r * cell}" width="${cell}" height="${cell}"/>`;
const D = `<svg xmlns="http://www.w3.org/2000/svg" width="${S}" height="${S}" viewBox="0 0 ${S} ${S}"><rect width="${S}" height="${S}" fill="#ffffff"/><g fill="#171a26">${dr}</g></svg>`;
fs.writeFileSync('/tmp/D.svg', D);

// E) generator matrix + the app's exact finder technique
let er = '';
for (let r = 0; r < gen.n; r++) for (let c = 0; c < gen.n; c++) { if (!gen.m(r, c)) continue; const inF = (r < 7 && c < 7) || (r < 7 && c >= gen.n - 7) || (r >= gen.n - 7 && c < 7); if (inF) continue; er += `<rect x="${margin + c * cell}" y="${margin + r * cell}" width="${cell}" height="${cell}"/>`; }
for (const [fr, fc] of [[0, 0], [0, gen.n - 7], [gen.n - 7, 0]]) {
  const x = margin + fc * cell, y = margin + fr * cell, s = 7 * cell;
  er += `<rect x="${x}" y="${y}" width="${s}" height="${s}" rx="0" fill="none" stroke="#171a26" stroke-width="${cell}"/><rect x="${x + 2 * cell}" y="${y + 2 * cell}" width="${3 * cell}" height="${3 * cell}" rx="0" fill="#171a26"/>`;
}
const E = `<svg xmlns="http://www.w3.org/2000/svg" width="${S}" height="${S}" viewBox="0 0 ${S} ${S}"><rect width="${S}" height="${S}" fill="#ffffff"/><g fill="#171a26">${er}</g></svg>`;
fs.writeFileSync('/tmp/E.svg', E);

const appSvg = fs.readFileSync('/tmp/hello-app.svg', 'utf8');

await dec(A, 'A) hand-built plain rects (290 canvas):');
await dec(B, 'B) hand-built + stroke finder ring (290):');
await dec(appSvg, 'C) ORIGINAL app output (margin 40):');
fs.writeFileSync('/tmp/A.svg', A); fs.writeFileSync('/tmp/B.svg', B);
await dec(D, 'D) qrcode-generator matrix, plain rects:');
await dec(E, 'E) qrcode-generator matrix + stroke finders:');
await dec(E.replace(/rx="0" fill="none" stroke="#171a26" stroke-width="10"/g, 'fill="#171a26"'), 'E2) same but finder as solid 7x7 fill:');
for (const [f, l] of [['/tmp/A.svg', 'A'], ['/tmp/B.svg', 'B'], ['/tmp/hello-app.svg', 'C']]) {
  const png = await sharp(f).resize(320, 320, { fit: 'contain', background: '#fff' }).flatten({ background: '#fff' }).png().toFile(`/tmp/cmp-${l}.png`);
}
console.log('wrote /tmp/cmp-{A,B,C}.png');
