import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import sharp from 'sharp';
import { createRequire } from 'node:module';
const require = createRequire('/home/user/.probe/');
const { readBarcodesFromImageData, prepareZXingModule } = require('zxing-wasm/reader');
const QR = require('qrcode/lib/core/qrcode.js');

const wasmPath = path.resolve('node_modules/zxing-wasm/reader/zxing_reader.wasm');
await prepareZXingModule({ overrideURL: wasmPath, fallback: () => fetch('file://' + wasmPath).then(r => r.arrayBuffer()) });

const payload = 'x'.repeat(300);
function gridSvg(n, get, cell = 10, margin = 4) {
  const S = n * cell + margin * 2;
  let s = `<svg xmlns="http://www.w3.org/2000/svg" width="${S}" height="${S}"><rect width="${S}" height="${S}" fill="#fff"/><g fill="#000">`;
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (get(r, c)) s += `<rect x="${margin + c * cell}" y="${margin + r * cell}" width="${cell}" height="${cell}"/>`;
  return s + '</g></svg>';
}
async function decode(svg, label) {
  const { data, info } = await sharp(Buffer.from(svg), { density: 150 }).flatten({ background: '#ffffff' }).raw().ensureAlpha().toBuffer({ resolveWithObject: true });
  const res = await readBarcodesFromImageData({ data: new Uint8ClampedArray(data), width: info.width, height: info.height }, { tryHarder: true });
  console.log(label.padEnd(38), res.length ? `DECODABLE (${res[0].text.length})` : '>>> NOT DECODABLE <<<');
  return res;
}

// node-qrcode matrix
const ref = QR.create(payload, { errorCorrectionLevel: 'M' });
await decode(gridSvg(ref.modules.size, (r, c) => !!ref.modules.get(r, c)), '1. node-qrcode matrix, perfect:');

// qrcode-generator matrix
const lib = fs.readFileSync('/home/user/.probe/qrcode.min.js', 'utf8');
const ctx = vm.createContext({ console });
vm.runInContext('var window=globalThis;' + lib, ctx);
const gen = vm.runInContext('var q=qrcode(0,"M"); q.addData(' + JSON.stringify(payload) + '); q.make(); ({n:q.getModuleCount(),m:q.isDark})', ctx);
await decode(gridSvg(gen.n, (r, c) => gen.m(r, c)), '2. qrcode-generator matrix, perfect:');
fs.writeFileSync('/tmp/g.png', await sharp(Buffer.from(gridSvg(gen.n, (r, c) => gen.m(r, c))), { density: 150 }).flatten({ background: '#fff' }).png().toBuffer());

// the app's own output
let lastSvg = '';
const mkNode = id => ({ id, style: {}, dataset: {}, _h: '', set innerHTML(v) { this._h = v }, get innerHTML() { return this._h }, set src(v) { lastSvg = v }, removeAttribute() { lastSvg = '' }, addEventListener() { }, textContent: '', querySelectorAll: () => [], click() { }, files: [] });
const nodes = {};
let orig = fs.readFileSync('/home/user/orig/orig.js', 'utf8') + '\n;globalThis.__api={state,generate};\n';
const doc = { getElementById: id => (nodes[id] ??= mkNode(id)), querySelectorAll: () => [], documentElement: mkNode('h'), createElement: () => mkNode('c'), addEventListener() { } };
const c2 = vm.createContext({ document: doc, console, btoa: s => Buffer.from(s, 'binary').toString('base64'), unescape: s => decodeURIComponent(escape(s)), encodeURIComponent, decodeURIComponent, Image: class { }, FileReader: class { } });
vm.runInContext('var window=globalThis;' + lib, c2);
vm.runInContext('var qrcode=window.qrcode;\n' + orig, c2);
const api = vm.runInContext('globalThis.__api', c2);
api.state.type = 'text'; api.state.data = { txt: payload }; api.state.style = 'square'; api.state.finderShape = 'square'; api.state.margin = 24;
api.generate();
let appSvg = api.state.svg;
fs.writeFileSync('/tmp/app.png', await sharp(Buffer.from(appSvg), { density: 150 }).flatten({ background: '#fff' }).png().toBuffer());
await decode(appSvg, '3. original app generate():');

// Now the same but with square finders drawn as *filled* squares of the raw matrix (i.e. no custom finder) to see which part breaks
const finderFree = (() => {
  const n = gen.n, cell = 10, m = 24, S = n * cell + m * 2;
  let s = `<svg xmlns="http://www.w3.org/2000/svg" width="${S}" height="${S}"><rect width="${S}" height="${S}" fill="#fff"/><g fill="#000">`;
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (gen.m(r, c)) s += `<rect x="${m + c * cell}" y="${m + r * cell}" width="${cell}" height="${cell}"/>`;
  return s + '</g></svg>';
})();
await decode(finderFree, '4. app geometry, no finder draw:');

// print app svg tail to inspect the 3 finder rects
const i = appSvg.indexOf('</g>');
console.log('\napp svg: chars after </g>:', JSON.stringify(appSvg.slice(i, i + 420)));
console.log('app svg: does it end with </svg>? ->', appSvg.trimEnd().endsWith('</svg>'), '| tail:', JSON.stringify(appSvg.slice(-80)));
console.log('app svg: count of "<g ":', (appSvg.match(/<g /g) || []).length, 'closing </g>:', (appSvg.match(/<\/g>/g) || []).length, 'rects:', (appSvg.match(/<rect/g) || []).length);
const bgm = appSvg.match(/^<svg[^>]*>/); console.log('svg tag:', bgm && bgm[0]);
