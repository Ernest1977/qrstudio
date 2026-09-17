import fs from 'node:fs'; import vm from 'node:vm';
const lib = fs.readFileSync('/home/user/.probe/qrcode.min.js','utf8');
let orig = fs.readFileSync('/home/user/orig/orig.js','utf8');
orig += "\n;globalThis.__api={state,buildPayload,generate};\n";
const nodes={}; const mkNode=id=>({id,style:{},dataset:{},_h:'',set innerHTML(v){this._h=v},get innerHTML(){return this._h},set src(v){},removeAttribute(){},addEventListener(){},textContent:'',querySelectorAll(){return[]},click(){},files:[]});
const doc={getElementById:id=>(nodes[id]??=mkNode(id)),querySelectorAll:()=>[],documentElement:mkNode('h'),createElement:()=>mkNode('c')};
const ctx=vm.createContext({document:doc,console,btoa:s=>Buffer.from(s,'binary').toString('base64'),encodeURIComponent,decodeURIComponent,Image:class{},FileReader:class{}});
vm.runInContext("var window=globalThis;"+lib,ctx);
vm.runInContext("var qrcode=window.qrcode;\n"+orig,ctx);
const api = vm.runInContext("globalThis.__api",ctx);

function tryMake(chars, ecc){
  const q = vm.runInContext(`qrcode(0,'${ecc}')`,ctx);
  try { q.addData('a'.repeat(chars)); q.make(); return `OK modules=${q.getModuleCount()}`; }
  catch(e){ return `ERROR "${e.message}" (modules=${q.getModuleCount()})`; }
}
console.log('== capacity, byte mode (qrcode-generator, auto version) ==');
for (const n of [1000,1500,1800,2000,2600,4000]) console.log(`  ${String(n).padStart(4)} chars, ECC M -> ${tryMake(n,'M')}`);
const dataUriLen = Math.ceil(1900/3)*4 + 'data:application/pdf;base64,'.length;
console.log('\n  MAX_EMBED=1900 raw bytes -> data URI =', dataUriLen, 'chars ->', tryMake(dataUriLen,'M'));

// svg + module geometry for round style, and whether alignment pattern modules exist
api.state.type='text'; api.state.data={txt:'x'.repeat(300)}; api.state.style='round';
api.generate();
const svg = api.state.svg;
console.log('\n  round/300chars: size attr =', svg.match(/width="(\d+)"/)[1], '| circles:', (svg.match(/<circle/g)||[]).length, '| rects:', (svg.match(/<rect/g)||[]).length);
fs.writeFileSync('/home/user/orig/out-round.svg', svg);
api.state.type='text'; api.state.data={txt:'x'.repeat(300)}; api.state.style='square'; api.state.finderShape='square';
api.generate(); fs.writeFileSync('/home/user/orig/out-square.svg', api.state.svg);
// vcard full
api.state.type='bcard'; api.state.data={first:'Éric',last:'Dupont',org:'Kamco Farm',street:'12 rue de Rivoli',city:'Paris',zip:'75001',country:'France',linkedin:'https://linkedin.com/in/eric'};
api.generate(); fs.writeFileSync('/home/user/orig/out-vcard.svg', api.state.svg);
console.log('\n== wifi payload escaping ==');
for (const [ssid,pwd] of [['MaBox;5G','p:a"s\\e'],['Net,work','"q"']]) {
  api.state.type='wifi'; api.state.data={ssid,enc:'WPA',pwd};
  console.log('  ', JSON.stringify(ssid), JSON.stringify(pwd), '->', JSON.stringify(api.buildPayload()));
}
console.log('\n== event payload (no TZ) ==');
api.state.type='event'; api.state.data={title:'Réu',loc:'Paris',start:'2026-10-01T09:30',end:'2026-10-01T11:00'};
console.log('  '+api.buildPayload().split('\n').join('\n  '));
