import fs from 'node:fs'; import path from 'node:path';
import sharp from 'sharp';
import { readBarcodesFromImageData, prepareZXingModule, defaultReaderOptions } from 'zxing-wasm/reader';
const wasmPath = path.resolve('node_modules/zxing-wasm/reader/zxing_reader.wasm');
await prepareZXingModule({ overrideURL: wasmPath, fallback: () => fetch('file://'+wasmPath).then(r=>r.arrayBuffer()) });
for (const file of ['/tmp/ref2.svg', '/home/user/orig/out-square.svg']) {
  let svg = fs.readFileSync(file,'utf8');
  if (svg.startsWith('data:')) svg = Buffer.from(svg.split('base64,')[1],'base64').toString('utf8');
  console.log('\nFILE', file, 'starts:', JSON.stringify(svg.slice(0,80)));
  const img = sharp(Buffer.from(svg), {density:150});
  const meta = await img.metadata(); console.log('meta', meta.width, meta.height, meta.channels, meta.space);
  const {data, info} = await img.raw().ensureAlpha().toBuffer({resolveWithObject:true});
  const id = {data: new Uint8ClampedArray(data), width: info.width, height: info.height};
  for (const opts of [{}, {tryHarder:true}, {tryHarder:true,inversionAttempts:'attemptBoth'}]) {
    try { const r = await readBarcodesFromImageData(id, opts); console.log('opts', JSON.stringify(opts), '->', JSON.stringify(r).slice(0,200)); }
    catch(e){ console.log('opts', JSON.stringify(opts), 'ERR', e.message); }
  }
}
