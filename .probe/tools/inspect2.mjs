import fs from 'node:fs';
import { createRequire } from 'node:module';
import sharp from 'sharp';
import { readBarcodesFromImageData, prepareZXingModule } from 'zxing-wasm/reader';
import path from 'node:path';
const require = createRequire('/home/user/.probe/');
const wasmPath = path.resolve('node_modules/zxing-wasm/reader/zxing_reader.wasm');
await prepareZXingModule({ overrideURL: wasmPath, fallback: () => fetch('file://' + wasmPath).then(r => r.arrayBuffer()) });

const svg = fs.readFileSync('/tmp/hello-app.svg', 'utf8');
const { data, info } = await sharp(Buffer.from(svg), { density: 290 }).greyscale().flatten({ background: '#ffffff' }).raw().toBuffer({ resolveWithObject: true });
console.log('raster', info.width, 'x', info.height);
const W = info.width;                       // should be 290 * (290/72) ... print actual
const scale = W / 290;                      // px per svg unit
console.log('px per svg unit:', scale.toFixed(3), 'margin px:', 40 * scale, 'cell px:', 10 * scale);
const at = (r, c) => {
  const x = Math.round((40 + c * 10 + 5) * scale), y = Math.round((40 + r * 10 + 5) * scale);
  return data[y * W + x] < 128 ? '#' : '.';
};
let grid = [];
for (let r = 0; r < 21; r++) { let row = ''; for (let c = 0; c < 21; c++) row += at(r, c); grid.push(row); }
grid.forEach((row, i) => console.log(String(i).padStart(2), row));
const QR = require('qrcode/lib/core/qrcode.js');
const ref = QR.create('HELLO', { errorCorrectionLevel: 'M' });
async function diffAgainst(get, label){ let d=0, samples=[]; for(let r=0;r<21;r++)for(let c=0;c<21;c++){const e=!!get(r,c); const g=grid[r][c]==='#'; if(e!==g){d++; if(samples.length<6)samples.push(r+','+c);} } console.log('diffs vs '+label+':', d, samples.join(' ')); }
await diffAgainst((r,c)=>ref.modules.get(r,c), 'node-qrcode ref');
const vm = await import('node:vm');
const fs2 = await import('node:fs');
const lib = fs2.readFileSync('/home/user/.probe/qrcode.min.js','utf8');
const ctx = vm.createContext({console});
vm.runInContext('var window=globalThis;'+lib, ctx);
const gen = vm.runInContext('var q=qrcode(0,"M"); q.addData("HELLO"); q.make(); ({n:q.getModuleCount(),m:q.isDark})', ctx);
await diffAgainst((r,c)=>gen.m(r,c), 'qrcode-generator matrix (same lib as app)');

/* Now: draw the SAME module grid with the app's drawing code (square style) but WITHOUT the custom finder,
   and with the finder drawn as plain modules — decode both to isolate the fault. */
const n = 21, cell = 10, m = 40, S = n * cell + m * 2;
function rects(get) { let b = ''; for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (get(r, c)) b += `<rect x="${m + c * cell}" y="${m + r * cell}" width="${cell}" height="${cell}"/>`; return b; }
const good = `<svg xmlns="http://www.w3.org/2000/svg" width="${S}" height="${S}"><rect width="${S}" height="${S}" fill="#ffffff"/><g fill="#171a26">${rects((r, c) => !!ref.modules.get(r, c))}</g></svg>`;
fs.writeFileSync('/tmp/good.svg', good);
async function dec(s, label) {
  const { data: d2, info: i2 } = await sharp(Buffer.from(s), { density: 200 }).flatten({ background: '#ffffff' }).raw().ensureAlpha().toBuffer({ resolveWithObject: true });
  const r = await readBarcodesFromImageData({ data: new Uint8ClampedArray(d2), width: i2.width, height: i2.height }, { tryHarder: true });
  console.log(label.padEnd(46), r.length ? 'DECODABLE -> ' + JSON.stringify(r[0].text) : 'NOT DECODABLE', `(${i2.width}px)`);
}
await dec(good, 'reference grid, plain squares:');
await dec(svg, 'app output for the same payload:');
// app output but finders drawn as solid 7x7 (i.e. what the clip suggests)
const appModules = (r, c) => grid[r][c] === '#';
await dec(`<svg xmlns="http://www.w3.org/2000/svg" width="${S}" height="${S}"><rect width="${S}" height="${S}" fill="#fff"/><g fill="#171a26">${rects(appModules)}</g></svg>`, 'app grid, plain squares (round-trip):');
