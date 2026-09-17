import fs from 'node:fs';
import vm from 'node:vm';
import sharp from 'sharp';
import { readBarcodesFromImageData, prepareZXingModule } from 'zxing-wasm/reader';
import { createRequire } from 'node:module';
const require = createRequire('/home/user/.probe/');
const QR = require('/home/user/.probe/node_modules/qrcode/lib/core/qrcode.js');

const payload = 'x'.repeat(300);

// Reference matrix from node-qrcode (mature impl)
const ref = QR.create(payload, { errorCorrectionLevel: 'M' });
const N = ref.modules.size;
const refGrid = [];
for (let r = 0; r < N; r++) {
  let row = '';
  for (let c = 0; c < N; c++) row += ref.modules.get(r, c) ? '#' : '.';
  refGrid.push(row);
}
fs.writeFileSync('/tmp/ref-grid.txt', refGrid.join('\n'));

// Matrix produced by the library the original HTML file loads
const lib = fs.readFileSync('/home/user/.probe/qrcode.min.js', 'utf8');
const ctx = vm.createContext({ console });
vm.runInContext('var window=globalThis;' + lib, ctx);
const gen = vm.runInContext(
  'var q=qrcode(0,"M"); q.addData(' + JSON.stringify(payload) + '); q.make(); ({n:q.getModuleCount(), m:q.isDark})',
  ctx
);
console.log('reference size:', N, '| qrcode-generator size:', gen.n);
const genGrid = [];
for (let r = 0; r < gen.n; r++) {
  let row = '';
  for (let c = 0; c < gen.n; c++) row += gen.m(r, c) ? '#' : '.';
  genGrid.push(row);
}
fs.writeFileSync('/tmp/gen-grid.txt', genGrid.join('\n'));

let diff = 0;
const first = [];
if (N !== gen.n) console.log('!! SIZE MISMATCH');
else {
  for (let r = 0; r < N; r++) {
    for (let c = 0; c < N; c++) {
      if (refGrid[r][c] !== genGrid[r][c]) {
        diff++;
        if (first.length < 8) first.push([r, c, refGrid[r][c], genGrid[r][c]].join(':'));
      }
    }
  }
}
console.log('module diffs between the two encoders:', diff, first.slice(0, 8));

// Harness sanity check: does the ORIGINAL svg scan at all?
const wasmPath = '/home/user/.probe/node_modules/zxing-wasm/reader/zxing_reader.wasm';
await prepareZXingModule({ overrideURL: wasmPath, fallback: () => fetch('file://' + wasmPath).then(r => r.arrayBuffer()) });

async function tryDecodeSvg(file, label) {
  let svg = fs.readFileSync(file, 'utf8');
  if (svg.startsWith('data:')) svg = Buffer.from(svg.split('base64,')[1], 'base64').toString('utf8');
  const { data, info } = await sharp(Buffer.from(svg), { density: 200 })
    .flatten({ background: '#ffffff' }).raw().ensureAlpha()
    .toBuffer({ resolveWithObject: true });
  const res = await readBarcodesFromImageData(
    { data: new Uint8ClampedArray(data), width: info.width, height: info.height },
    { tryHarder: true }
  );
  const ok = res.length > 0;
  console.log(label, ok ? 'DECODABLE' : 'NOT DECODABLE', '| len', ok ? res[0].text.length : '-');
  return ok;
}
await tryDecodeSvg('/tmp/ref2.svg', 'zxing ref svg (HELLO)      :');
await tryDecodeSvg('/home/user/orig/out-square.svg', 'original html, square style  :');
await tryDecodeSvg('/home/user/orig/out-round.svg', 'original html, round style   :');
await tryDecodeSvg('/home/user/orig/out-vcard.svg', 'original html, vcard         :');
