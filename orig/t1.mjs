import fs from 'node:fs';
import vm from 'node:vm';
const lib = fs.readFileSync('/home/user/.probe/qrcode.min.js','utf8');
const orig = fs.readFileSync('/home/user/orig/orig.js','utf8');
let lastSvg='';
const nodes={};
function mkNode(id){return{id,_html:'',style:{},dataset:{},set innerHTML(v){this._html=v},get innerHTML(){return this._html},set src(v){lastSvg=v},get src(){return lastSvg},removeAttribute(){lastSvg=''},addEventListener(){},textContent:'',value:'',checked:true,files:[],querySelectorAll(){return[]},click(){}};}
const doc={getElementById:id=>(nodes[id]??=mkNode(id)),querySelectorAll:()=>[],documentElement:mkNode('html'),createElement:()=>mkNode('c'),addEventListener(){}};
const ctx=vm.createContext({document:doc,console,btoa:s=>Buffer.from(s,'binary').toString('base64'),atob:s=>Buffer.from(s,'base64').toString('binary'),unescape:s=>{try{return unescape(s)}catch{return s}},encodeURIComponent,decodeURIComponent,Image:class{},FileReader:class{},get currentSvg(){return lastSvg},set currentSvg(v){lastSvg=v}});
vm.runInContext("var window=globalThis;"+lib,ctx);
vm.runInContext("var qrcode = window.qrcode;"+orig,ctx);

function build(label, mutate){
  mutate(ctx);
  try{ ctx.generate(); }catch(e){ console.log(`\n### ${label}\nJS ERROR: ${e.constructor.name}: ${e.message}`); return; }
  const s = ctx.state.svg;
  console.log(`\n### ${label}`);
  console.log('payload   :', JSON.stringify(ctx.buildPayload()).slice(0,300));
  console.log('svg present:', !!s, s ? `(${s.length} chars)`:'');
  if(!s) return;
  // show the ADR line if present
  const dec = Buffer.from(s.split('base64,')[1],'base64').toString('utf8');
  console.log('svg valid utf8 decode:', dec.startsWith('<svg'));
  fs.writeFileSync(`/home/user/orig/out-${label}.svg`, dec);
}
const setBcard = c => {
  c.state.type='bcard';
  c.state.data={first:'Éric',last:'Dupont',org:'Kamco Farm',linkedin:'https://linkedin.com/in/eric'};
};
build('A-vcard-adr', c=>setBcard(c));
build('B-wifi-special', c=>{c.state.type='wifi'; c.state.data={ssid:'MaBox;5G',enc:'WPA',pwd:'p:a"s\\e'};});
build('C-round-long', c=>{c.state.type='text'; c.state.data={txt:'x'.repeat(300)}; c.state.style='round';});
build('D-accented', c=>{c.state.type='text'; c.state.data={txt:'café école — été 2026'};});
build('E-emoji-caption', c=>{c.state.type='image'; c.state.data={url:'https://exemple.com/a.jpg',desc:'Équipe au salon 🎉'};});
console.log('\n--- A: vcard lines containing ADR ---');
try{ ctx.state.type='bcard'; ctx.state.data={first:'Éric',last:'Dupont',org:'Kamco Farm',linkedin:'https://linkedin.com/in/eric'}; console.log(ctx.buildPayload()); }catch(e){console.log(e.message)}
console.log('\n--- B: wifi payload ---');
ctx.state.type='wifi'; ctx.state.data={ssid:'MaBox;5G',enc:'WPA',pwd:'p:a"s\\e'}; console.log(JSON.stringify(ctx.buildPayload()));
ctx.state.type='wifi'; ctx.state.data={ssid:'MaBox',enc:'WPA',pwd:'p\\a;s,s'}; console.log(JSON.stringify(ctx.buildPayload()));
