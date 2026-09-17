import fs from 'node:fs';
import { createRequire } from 'node:module';
import sharp from 'sharp';
import { readBarcodesFromImageData, prepareZXingModule } from 'zxing-wasm/reader';
import path from 'node:path';
const require = createRequire('/home/user/.probe/');

const svg = fs.readFileSync('/tmp/hello-app.svg', 'utf8');
// Rasterize at 1 px per module-unit * 10 and sample the grid EXACTLY using the known geometry
const { data, info } = await sharp(Buffer.from(svg), { density: 72 }).resize(290, 290, { fit: 'contain', background: '#ffffff' }).flatten({ background: '#ffffff' }).greyscale().raw().toBuffer({ resolveWithObject: true });
console.log('raster', info.width, info.height);
const PX = 290;
const at = (r, c) => data[r * 10 + 5 + (c * 10 + 5) * PX] < 128 ? '#' : '.';
let grid = [];
for (let r = 0; r < 21; r++) { let row = ''; for (let c = 0; c < 21; c++) row += at(r, c); grid.push(row); }
console.log('=== APP OUTPUT (21x21) ===');
grid.forEach((r, i) => console.log(String(i).padStart(2), r));

// Reference: what a v1 HELLO QR should be
const QR = require('qrcode/lib/core/qrcode.js');
const ref = QR.create('HELLO', { errorCorrectionLevel: 'M' });
let refGrid = [];
for (let r = 0; r < 21; r++) { let row = ''; for (let c = 0; c < 21; c++) row += ref.modules.get(r, c) ? '#' : '.'; refGrid.push(row); }
console.log('\n=== REFERENCE (node-qrcode, ECC M, 21x21) ===');
refGrid.forEach((r, i) => console.log(String(i).padStart(2), r));
let diffs = [];
for (let r = 0; r < 21; r++) for (let c = 0; c < 21; c++) if (grid[r][c] !== refGrid[r][c]) diffs.push(`${r},${c}:${refGrid[r][c]}->${grid[r][c]}`);
console.log('\ndiffs (count ' + diffs.length + '):', diffs.slice(0, 40).join(' '));

// Decode the exact rasterization too
const { data: d2, info: i2 } = await sharp(Buffer.from(svg), { density: 300 }).flatten({ background: '#ffffff' }).raw().ensureAlpha().toBuffer({ resolveWithObject: true });
const wasmPath = path.resolve('node_modules/zxing-wasm/reader/zxing_reader.wasm');
await prepareZXingModule({ overrideURL: wasmPath, fallback: () => fetch('file://' + wasmPath).then(r => r.arrayBuffer()) });
const res = await readBarcodesFromImageData({ data: new Uint8ClampedArray(d2), width: i2.width, height: i2.height }, { tryHarder: true });
console.log('decode @density300 of app svg:', res.length ? 'OK ' + res[0].text : 'FAIL', '| raster', i2.width + 'x' + i2.height);
