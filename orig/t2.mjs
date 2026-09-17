import fs from 'node:fs';
import vm from 'node:vm';
const lib = fs.readFileSync('/home/user/.probe/qrcode.min.js','utf8');
let orig = fs.readFileSync('/home/user/orig/orig.js','utf8');
orig += "\n;globalThis.__api = {state, buildPayload, generate, inFinder, isTiming};\n";
let lastSvg='';
const nodes={};
function mkNode(id){return{id,_html:'',style:{},dataset:{},set innerHTML(v){this._html=v},get innerHTML(){return this._html},set src(v){lastSvg=v},get src(){return lastSvg},removeAttribute(){lastSvg=''},addEventListener(){},textContent:'',value:'',checked:true,files:[],querySelectorAll(){return[]},click(){}};}
const doc={getElementById:id=>(nodes[id]??=mkNode(id)),querySelectorAll:()=>[],documentElement:mkNode('html'),createElement:()=>mkNode('c'),addEventListener(){}};
const ctx=vm.createContext({document:doc,console,btoa:s=>Buffer.from(s,'binary').toString('base64'),atob:s=>Buffer.from(s,'base64').toString('binary'),encodeURIComponent,decodeURIComponent,Image:class{},FileReader:class{}});
vm.runInContext("var window=globalThis;"+lib,ctx);
vm.runInContext("var qrcode = window.qrcode;\n"+orig,ctx);
const api = vm.runInContext("globalThis.__api", ctx);

function run(label, setup){
  lastSvg=''; api.state.svg='';
  setup(api.state);
  let err=null;
  try{ api.generate(); }catch(e){ err = `${e.constructor.name}: ${e.message}`; }
  const payload = api.buildPayload();
  console.log(`\n### ${label}`);
  console.log('  thrown        :', err ?? 'no');
  console.log('  payload       :', JSON.stringify(payload).slice(0,400));
  const svg = api.state.svg;
  console.log('  svg in state  :', svg?`${svg.length} chars`:'none');
  if(svg){
    const b64part = lastSvg.startsWith('data:image/svg+xml;base64,');
    let dec = null, decOk = true;
    try { dec = b64part ? Buffer.from(lastSvg.split('base64,')[1],'base64').toString('utf8') : lastSvg; }
    catch(e){ decOk=false; }
    if (b64part){
      const raw = Buffer.from(lastSvg.split('base64,')[1],'base64');
      try { dec = new TextDecoder('utf-8',{fatal:true}).decode(raw); } catch(e){ decOk=false; dec=`<binary, ${raw.length} bytes>`; }
    }
    console.log('  data-URI utf8 :', b64part?'base64':'plain', '| strict-utf8:', decOk);
    if (dec) fs.writeFileSync(`/home/user/orig/out-${label}.svg`, dec);
    const cap = dec && dec.match(/<text[^>]*>([^<]*)</); if(cap) console.log('  caption       :', JSON.stringify(cap[1]));
  }
}
run('A-vcard', s=>{ s.type='bcard'; s.data={first:'Éric',last:'Dupont',org:'Kamco Farm',linkedin:'https://linkedin.com/in/eric'}; });
console.log('  --- full vcard payload ---\n' + api.buildPayload().split('\n').map(l=>'  '+l).join('\n'));
run('B-wifi', s=>{ s.type='wifi'; s.data={ssid:'MaBox;5G',enc:'WPA',pwd:'p:a"s\\e'}; });
run('C-round-long', s=>{ s.type='text'; s.data={txt:'x'.repeat(300)}; s.style='round'; s.finderShape='circle'; });
run('D-accent', s=>{ s.type='text'; s.data={txt:'café école — été 2026'}; });
run('E-emoji-caption', s=>{ s.type='image'; s.data={url:'https://exemple.com/a.jpg',desc:'Équipe au salon 🎉'}; });
run('F-empty', s=>{ s.type='url'; s.data={url:''}; });
console.log('\n### capacity check (qrcode-generator, typeNumber 0, M)');
for (const n of [1000, 1500, 1800, 2000]) {
  try { const q = vm.runInContext(`qrcode(0,'M')`, ctx); q.addData('a'.repeat(n)); q.make(); console.log(`  ${n} chars -> ok, version ${q.getModuleCount()}`); }
  catch(e){ console.log(`  ${n} chars -> ${e.message}`); }
}
console.log('\n### svg escape of desc with </script><b>  (XSS-ish into SVG text)');
api.state.type='image'; api.state.data={url:'https://x.com/a.png',desc:'a" onload="alert(1)'};
try { api.generate(); const d = new TextDecoder().decode(Buffer.from(lastSvg.split('base64,')[1],'base64')); console.log('  ', d.match(/<text[^>]*>.*?<\/text>/s)[0].slice(0,220)); } catch(e){ console.log('  err', e.message); }
