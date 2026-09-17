import fs from 'node:fs';
import vm from 'node:vm';

const lib = fs.readFileSync('/home/user/.probe/qrcode.min.js','utf8');
const orig = fs.readFileSync('/home/user/orig/orig.js','utf8');

let currentSvg = '';
const nodes = {};
function mkNode(id){
  return {
    id, _html:'', style:{}, dataset:{}, children:[],
    set innerHTML(v){ this._html=v; }, get innerHTML(){ return this._html; },
    set src(v){ currentSvg = v; }, get src(){ return currentSvg; },
    removeAttribute(){ currentSvg=''; },
    addEventListener(){}, textContent:'', value:'', checked:true, files:[],
    querySelectorAll(){ return []; }, click(){},
  };
}
const doc = {
  getElementById: id => (nodes[id] ||= mkNode(id)),
  querySelectorAll: () => [],
  documentElement: mkNode('html'),
  createElement: () => mkNode('created'),
  addEventListener(){},
};
const ctx = vm.createContext({
  document: doc, window: {}, console,
  btoa: s => Buffer.from(s,'binary').toString('base64'),
  atob: s => Buffer.from(s,'base64').toString('binary'),
  unescape: decodeURIComponentEscape(),
  encodeURIComponent, decodeURIComponent, Image: class {}, FileReader: class {},
  qrcode: undefined,
});
function decodeURIComponentEscape(){ return s => { try { return decodeURIComponent(s.replace(/%u/g,'%u')); } catch { return s; } }; }

// lib defines window.qrcode; run it inside ctx with a fake global
vm.runInContext("var window = globalThis;" + lib, ctx);
vm.runInContext("if (typeof qrcode === 'undefined') { var qrcode = window.qrcode; }", ctx);
vm.runInContext(orig.replace(/qrcode\(0,'M'\)/g, "qrcode(0,'M')"), ctx);

function svgFromState(){ return ctx.currentSvg || currentSvg; }

function scenario(name, mutate){
  try { mutate(ctx); ctx.generate(); } catch(e){ console.log(`[${name}] THREW:`, e.message); return null; }
  const s = currentSvg;
  if(!s){ console.log(`[${name}] no svg`); return null; }
  const svg = decodeURIComponent(s.replace('data:image/svg+xml;base64,','') && '' ) ;
  let decoded;
  if (s.startsWith('data:image/svg+xml;base64,')) {
    decoded = Buffer.from(s.slice('data:image/svg+xml;base64,'.length), 'base64').toString('utf8');
  } else decoded = s;
  return decoded;
}
export { ctx, scenario, currentSvgGetter: () => currentSvg };
