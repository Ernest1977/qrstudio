import sharp from 'sharp';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire('/home/user/.probe/');
const { readBarcodesFromImageData, prepareZXingModule } = require('zxing-wasm/reader');
const wasmPath = path.resolve('node_modules/zxing-wasm/reader/zxing_reader.wasm');
await prepareZXingModule({ overrideURL: wasmPath, fallback: () => fetch('file://'+wasmPath).then(r=>r.arrayBuffer()) });
async function dec(svgStr, label, opts={density:200}) {
  const {data,info} = await sharp(Buffer.from(svgStr), opts).flatten({background:'#ffffff'}).raw().ensureAlpha().toBuffer({resolveWithObject:true});
  const r = await readBarcodesFromImageData({data:new Uint8ClampedArray(data),width:info.width,height:info.height},{tryHarder:true});
  console.log(label.padEnd(56), r.length?'DECODABLE':'NOT DECODABLE', `(${info.width}px)`);
}
const E = fs.readFileSync('/tmp/E.svg','utf8');
const D = fs.readFileSync('/tmp/D.svg','utf8');
await dec(D,'D plain (density 200):');
await dec(E,'E stroke finder (density 200):');
await dec(E,'E stroke finder (density 900):',{density:900});
await dec(E.replace('<svg ','<svg shape-rendering="crispEdges" '),'E stroke finder + crispEdges (density 200):');
await dec(E,'E stroke finder (density 200, no flatten):',{density:200});
// fill-rule version (the fix I plan)
const Efix = E.replace(/<rect x="(\d+)" y="(\d+)" width="70" height="70" rx="0" fill="none" stroke="#171a26" stroke-width="10"\/>/g,
 (m,x,y)=>`<path fill-rule="evenodd" d="M${x} ${y}h70v70h-70zM${+x+10} ${+y+10}h50v50h-50z"/>`);
await dec(Efix,'E with fill-rule path finder (density 200):');
fs.writeFileSync('/tmp/Efix.svg', Efix);
