import puppeteer from 'puppeteer';
import fs from 'node:fs';
const b = await puppeteer.launch({ args: ['--no-sandbox','--disable-dev-shm-usage'] });
const p = await b.newPage();
const errs = [];
p.on('pageerror', e => errs.push('pageerror: ' + e.message));
p.on('console', m => { if (m.type()==='error') errs.push('console: ' + m.text()); });
await p.setViewport({ width: 1400, height: 1000, deviceScaleFactor: 2 });
await p.goto('http://127.0.0.1:5173/', { waitUntil: 'networkidle0' });
// carte de visite + style arrondi : ce qui montre le mieux l'outil
await p.evaluate(() => { document.querySelectorAll('.qrc-type')[11].click(); });
await new Promise(r => setTimeout(r, 400));
await p.evaluate(() => {
  const set=(sel,v)=>{const el=document.querySelector(sel);const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(el,v);el.dispatchEvent(new Event('input',{bubbles:true}));};
  set('#bcard-first','Marie'); set('#bcard-last','Dupont'); set('#bcard-org','Kamco Farm'); set('#bcard-title','Directrice Commerciale');
  document.querySelectorAll('.qrc-custom .qrc-chip')[2].click();
  document.querySelectorAll('.qrc-custom .qrc-chip')[6].click();
});
await new Promise(r => setTimeout(r, 700));
await p.screenshot({ path: '/home/user/screenshots/studio-dark.png', fullPage: false });
const box = await p.$('.qrc-studio');
await box.screenshot({ path: '/home/user/screenshots/studio-crop.png' });
await p.evaluate(() => document.querySelector('.qrc-btn-theme').click());
await new Promise(r => setTimeout(r, 500));
await (await p.$('.qrc-studio')).screenshot({ path: '/home/user/screenshots/studio-light.png' });
console.log('errors:', errs.length ? errs.join('\n') : 'aucune');
await b.close();
