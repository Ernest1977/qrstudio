import sharp from 'sharp';
import path from 'node:path';
import { readBarcodesFromImageData, prepareZXingModule } from 'zxing-wasm/reader';
import { createMatrix } from '/home/user/qr-coding-react/src/lib/qrcode.js';
import { renderQrSvg } from '/home/user/qr-coding-react/src/lib/render.js';
const wasm = '/home/user/qr-coding-react/node_modules/zxing-wasm/reader/zxing_reader.wasm';
await prepareZXingModule({ overrideURL: wasm, fallback: () => fetch('file://'+wasm).then(r=>r.arrayBuffer()) });
const logo = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" fill="#000"/></svg>');
const payload = 'https://exemple.com?avec=logo&encore=1';
async function dec(svg, density){ const {data,info}=await sharp(Buffer.from(svg),{density}).flatten({background:'#fff'}).raw().ensureAlpha().toBuffer({resolveWithObject:true});
 const r=await readBarcodesFromImageData({data:new Uint8ClampedArray(data),width:info.width,height:info.height},{tryHarder:true}); return r.length?r[0].text:null; }
for (const ecc of ['Q','H']) for (const pct of [10,15,20,25,30,35]) for (const d of [220,400,700]) {
  const m = createMatrix(payload, { ecc });
  const svg = renderQrSvg(m, { margin: 32, cell: 10, logo, logoPercent: pct }).svg;
  console.log(`ecc=${ecc} logo=${pct}% density=${d} modules=${m.size} -> ${await dec(svg,d) ? 'OK' : 'FAIL'}`);
}
