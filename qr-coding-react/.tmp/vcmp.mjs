import fs from 'node:fs';
import vm from 'node:vm';
import { buildPayload } from '../src/lib/payload.js';

const lines = fs.readFileSync('/home/user/orig/orig.js', 'utf8').split('\n');
const head = lines.slice(0, 40).join('\n'); // F + TYPES + état d'origine
const fn = lines.slice(94, 137).join('\n'); // buildPayload()
const stub = new Proxy(function () {}, { get: () => () => null, apply: () => null });
const ctx = {
  console,
  encodeURIComponent,
  document: new Proxy({}, { get: () => stub }),
  btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
};
vm.createContext(ctx);
vm.runInContext(
  head + '\n' + fn + '\nglobalThis.__run=(t,d)=>{state.type=t;state.data=d;return buildPayload();};',
  ctx,
);
const run = ctx.__run;
const typeObj = (id) => ctx.__type(id);
vm.runInContext('globalThis.__type=(id)=>TYPES.find(x=>x.id===id);', ctx);

const card = {
  first: 'Marie', last: 'Dupont', org: 'Dupont & Associés', title: 'Dir, Commerciale',
  street: '5 via Roma; scala B', city: 'Paris', zip: '75001', country: 'France',
  email: 'm@x.fr', tel: '', cell: '', web: '', linkedin: '',
};
console.log('=== ORIGINAL vCard ===\n' + run('bcard', card));
console.log('\n=== REACT (même saisie) ===\n' + buildPayload(typeObj('bcard'), card, {}));

const w = { ssid: 'Café; Net:5G', pwd: 'p"a,s\\s', enc: 'nopass' };
console.log('\n=== ORIGINAL Wi-Fi (nopass, SSID avec ; et :) ===\n' + run('wifi', w));
console.log('=== REACT ===\n' + buildPayload(typeObj('wifi'), w, {}));

const ev = { title: 'Réu:ni/on', loc: 'Salle 2, 3e étage', start: '2026-06-01T09:00', end: '2026-06-01T10:00' };
console.log('\n=== ORIGINAL événement ===\n' + run('event', ev));
console.log('=== REACT ===\n' + buildPayload(typeObj('event'), ev, {}));

const s = { num: '0033 6-12-34-56-78', msg: 'salut à tous' };
console.log('\n=== ORIGINAL SMS ===\n' + JSON.stringify(run('sms', s)));
console.log('=== REACT SMS ===\n' + JSON.stringify(buildPayload(typeObj('sms'), s, {})));
