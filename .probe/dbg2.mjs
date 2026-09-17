import fs from 'node:fs'; import path from 'node:path';
import sharp from 'sharp';
import { readBarcodesFromImageData, prepareZXingModule } from 'zxing-wasm/reader';
const wasmPath = path.resolve('node_modules/zxing-wasm/reader/zxing_reader.wasm');
await prepareZXingModule({ overrideURL: wasmPath, fallback: () => fetch('file://'+wasmPath).then(r=>r.arrayBuffer()) });

let svg = fs.readFileSync('/home/user/orig/out-square.svg','utf8');
console.log('len', svg.length, '\nhead:', svg.slice(0,220));
for (const d of [96, 300]) {
  const {data, info} = await sharp(Buffer.from(svg), {density:d}).raw().ensureAlpha().toBuffer({resolveWithObject:true});
  const r = await readBarcodesFromImageData({data:new Uint8ClampedArray(data),width:info.width,height:info.height},{tryHarder:true});
  console.log('density', d, 'px', info.width, '->', r.length ? 'DECOK '+JSON.stringify(r[0].text).slice(0,60) : 'fail');
}
fs.writeFileSync('/tmp/orig-square.png', await sharp(Buffer.from(svg),{density:300}).png().toBuffer());
