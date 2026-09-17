import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import sharp from 'sharp';
import { createRequire } from 'node:module';
const require = createRequire('/home/user/.probe/');
const { readBarcodesFromImageData, prepareZXingModule } = require('zxing-wasm/reader');

const wasmPath = path.resolve('node_modules/zxing-wasm/reader/zxing_reader.wasm');
await prepareZXingModule({ overrideURL: wasmPath, fallback: () => fetch('file://' + wasmPath).then(r => r.arrayBuffer()) });

const lib = fs.readFileSync('/home/user/.probe/qrcode.min.js', 'utf8');
let orig = fs.readFileSync('/home/user/orig/orig.js', 'utf8') + '\n;globalThis.__api={state,generate};\n';
let lastSvg = '';
const mkNode = id => ({ id, style: {}, dataset: {}, _h: '', set innerHTML(v) { this._h = v }, get innerHTML() { return this._h }, set src(v) { lastSvg = v }, removeAttribute() { lastSvg = '' }, addEventListener() { }, textContent: '', querySelectorAll: () => [], click() { }, files: [] });
const nodes = {};
const doc = { getElementById: id => (nodes[id] ??= mkNode(id)), querySelectorAll: () => [], documentElement: mkNode('h'), createElement: () => mkNode('c'), addEventListener() { } };
const ctx = vm.createContext({ document: doc, console, btoa: s => Buffer.from(s, 'binary').toString('base64'), unescape: s => decodeURIComponent(escape(s)), encodeURIComponent, decodeURIComponent, Image: class { }, FileReader: class { } });
vm.runInContext('var window=globalThis;' + lib, ctx);
vm.runInContext('var qrcode=window.qrcode;\n' + orig, ctx);
const api = vm.runInContext('globalThis.__api', ctx);

async function decode(svg, label) {
  const { data, info } = await sharp(Buffer.from(svg), { density: 150 }).flatten({ background: '#ffffff' }).raw().ensureAlpha().toBuffer({ resolveWithObject: true });
  const res = await readBarcodesFromImageData({ data: new Uint8ClampedArray(data), width: info.width, height: info.height }, { tryHarder: true });
  console.log(label.padEnd(40), res.length ? 'DECODABLE' : '>>> NOT DECODABLE <<<');
  return res.length;
}
function set(margin, payload = 'x'.repeat(300)) {
  api.state.type = 'text'; api.state.data = { txt: payload }; api.state.style = 'square'; api.state.finderShape = 'square'; api.state.margin = margin;
  api.generate(); return api.state.svg;
}
console.log('HYPOTHESIS: the finder ring stroke is clipped by the canvas edge at small margins');
for (const margin of [24, 40, 60, 80]) {
  await decode(set(margin), `margin=${margin}, v14 payload (300 chars)`);
}
console.log('\nSame test with a SMALL payload (v1, no alignment patterns):');
for (const margin of [24, 80]) {
  await decode(set(margin, 'HELLO'), `margin=${margin}, "HELLO"`);
}
console.log('\nDumping finder geometry for margin=24 vs margin=80 (first finder rect in the svg):');
for (const margin of [24, 80]) {
  const svg = set(margin, 'HELLO');
  console.log(` margin=${margin}:`, (svg.match(/<rect[^>]*width="70"[^>]*>/) || ['none'])[0]);
}
