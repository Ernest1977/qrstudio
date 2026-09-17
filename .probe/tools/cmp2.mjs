import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import sharp from 'sharp';
import { readBarcodesFromImageData, prepareZXingModule } from 'zxing-wasm/reader';

const wasmPath = path.resolve('node_modules/zxing-wasm/reader/zxing_reader.wasm');
await prepareZXingModule({ overrideURL: wasmPath, fallback: () => fetch('file://' + wasmPath).then(r => r.arrayBuffer()) });

const payload = 'x'.repeat(300);

/* --- 1. raw matrix of qrcode-generator (the lib the original file uses), drawn as perfect squares --- */
const lib = fs.readFileSync(path.resolve('/home/user/.probe/qrcode.min.js'), 'utf8');
const ctx = vm.createContext({ console });
vm.runInContext('var window=globalThis;' + lib, ctx);
const gen = vm.runInContext(
  'var q=qrcode(0,"M"); q.addData(' + JSON.stringify(payload) + '); q.make(); ({n:q.getModuleCount(), m:q.isDark})', ctx);
const n = gen.n;
let raw = `<svg xmlns="http://www.w3.org/2000/svg" width="${n * 10}" height="${n * 10}"><rect width="100%" height="100%" fill="#fff"/><g fill="#000">`;
for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (gen.m(r, c)) raw += `<rect x="${c * 10}" y="${r * 10}" width="10" height="10"/>`;
raw += '</g></svg>';
fs.writeFileSync('/tmp/raw-matrix.svg', raw);

/* --- 2. what the original app's generate() produces for the same payload --- */
let lastSvg = '';
const mkNode = id => ({ id, style: {}, dataset: {}, _h: '', set innerHTML(v) { this._h = v }, get innerHTML() { return this._h }, set src(v) { lastSvg = v }, get src() { return lastSvg }, removeAttribute() { lastSvg = '' }, addEventListener() { }, textContent: '', querySelectorAll: () => [], click() { }, files: [] });
const nodes = {};
let orig = fs.readFileSync('/home/user/orig/orig.js', 'utf8');
orig += '\n;globalThis.__api={state,generate};\n';
const doc = { getElementById: id => (nodes[id] ??= mkNode(id)), querySelectorAll: () => [], documentElement: mkNode('h'), createElement: () => mkNode('c'), addEventListener() { } };
const c2 = vm.createContext({ document: doc, console, btoa: s => Buffer.from(s, 'binary').toString('base64'), unescape: s => decodeURIComponent(escape(s)), encodeURIComponent, decodeURIComponent, Image: class { }, FileReader: class { } });
vm.runInContext('var window=globalThis;' + lib, c2);
vm.runInContext('var qrcode=window.qrcode;\n' + orig, c2);
const api = vm.runInContext('globalThis.__api', c2);
api.state.type = 'text'; api.state.data = { txt: payload }; api.state.style = 'square'; api.state.finderShape = 'square'; api.state.margin = 24;
api.generate();
let appSvg = api.state.svg;
// the app puts the svg straight into state.svg (unescaped), decode the data-uri for the preview
fs.writeFileSync('/tmp/app-svg.svg', appSvg);

/* --- 3. decode both --- */
async function decode(svgStr, label, density = 200) {
  let s = svgStr;
  if (s.startsWith('data:')) s = Buffer.from(s.split('base64,')[1], 'base64').toString('utf8');
  const { data, info } = await sharp(Buffer.from(s), { density }).flatten({ background: '#ffffff' }).raw().ensureAlpha().toBuffer({ resolveWithObject: true });
  const res = await readBarcodesFromImageData({ data: new Uint8ClampedArray(data), width: info.width, height: info.height }, { tryHarder: true });
  const ok = res.length > 0;
  console.log(label.padEnd(34), ok ? `DECODABLE (${res[0].text.length} chars)` : '>>> NOT DECODABLE <<<', `${info.width}px`);
  return ok;
}
console.log('payload len', payload.length, '| matrix', n, 'modules');
await decode(raw, 'A. raw module matrix, squares:');
await decode(appSvg, 'B. original app generate():');
api.state.style = 'round'; api.generate(); await decode(api.state.svg, 'C. original app, round style:');
api.state.finderShape = 'circle'; api.generate(); await decode(api.state.svg, 'D. original app, round+circle:');
api.state.margin = 8; api.generate(); await decode(api.state.svg, 'E. margin 8, round+circle:');
api.state.margin = 24; api.state.style = 'square'; api.state.finderShape = 'square'; api.generate(); await decode(api.state.svg, 'F. margin 24, all square:');
