import sharp from 'sharp'; import fs from 'node:fs'; import path from 'node:path'; import { createRequire } from 'node:module';
const require = createRequire('/home/user/.probe/');
const { readBarcodesFromImageData, prepareZXingModule } = require('zxing-wasm/reader');
const QR = require('qrcode/lib/core/qrcode.js');
const wasmPath = path.resolve('node_modules/zxing-wasm/reader/zxing_reader.wasm');
await prepareZXingModule({ overrideURL: wasmPath, fallback: () => fetch('file://'+wasmPath).then(r=>r.arrayBuffer()) });

const payload='x'.repeat(300);
const ref = QR.create(payload,{errorCorrectionLevel:'M'});
const n = ref.modules.size;
const inFinder=(r,c)=>(r<7&&c<7)||(r<7&&c>=n-7)||(r>=n-7&&c<7);
function alignCells(n){ // alignment pattern centers
  const step = n>=14? Math.ceil((n-13)/ (n>=14?2:1)) : 0; const res=[];
  if(n<25) return [[n-7,n-7]];
  return [[n-7,n-7]];
}
function build(style, roundTimingToo){
  const cell=10, margin=24, S=n*cell+margin*2; let body='';
  const mods=[];
  // collect alignment pattern centers per spec: pattern at (6, 4*version+10 ... ) -> use generator's own module grid, find 5x5 islands; simpler: skip and rely on diff
  for(let r=0;r<n;r++)for(let c=0;c<n;c++){
    if(!ref.modules.get(r,c)) continue;
    if(inFinder(r,c)) continue;
    const x=margin+c*cell, y=margin+r*cell;
    const timing = r===6||c===6;
    const s = (timing && !roundTimingToo) ? 'square' : style;
    if(s==='square') body+=`<rect x="${x}" y="${y}" width="${cell}" height="${cell}"/>`;
    else if(s==='round') body+=`<circle cx="${x+cell/2}" cy="${y+cell/2}" r="${cell/2}"/>`;
    else if(s==='rounded') body+=`<rect x="${x}" y="${y}" width="${cell}" height="${cell}" rx="${cell*0.32}"/>`;
    else body+=`<path d="M${x+5} ${y}L${x+10} ${y+5}L${x+5} ${y+10}L${x} ${y+5}Z"/>`;
  }
  // finder as fill-rule path (the fix)
  let f='';
  for(const [fr,fc] of [[0,0],[0,n-7],[n-7,0]]){
    const x=margin+fc*cell, y=margin+fr*cell, S7=7*cell;
    f+=`<path fill-rule="evenodd" d="M${x} ${y}h${S7}v${S7}h-${S7}zM${x+cell} ${y+cell}h${S7-2*cell}v${S7-2*cell}h-${S7-2*cell}z"/><rect x="${x+2*cell}" y="${y+2*cell}" width="${3*cell}" height="${3*cell}"/>`;
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${S}" height="${S}" viewBox="0 0 ${S} ${S}"><rect width="${S}" height="${S}" fill="#ffffff"/><g fill="#171a26">${body}${f}</g></svg>`;
}
async function dec(svg,label){
  const {data,info}=await sharp(Buffer.from(svg),{density:200}).flatten({background:'#fff'}).raw().ensureAlpha().toBuffer({resolveWithObject:true});
  const r=await readBarcodesFromImageData({data:new Uint8ClampedArray(data),width:info.width,height:info.height},{tryHarder:true});
  console.log(label.padEnd(48), r.length?`DECODABLE (${r[0].text.length})`:'>>> NOT DECODABLE <<<',`${info.width}px, modules=${n}`);
}
await dec(build('square',false),'square style (version '+n+' has alignment):');
await dec(build('round',false),'round style (timing square):');
await dec(build('round',true),'round style (timing also round):');
await dec(build('diamond',false),'diamond style:');
await dec(build('rounded',false),'rounded style:');
