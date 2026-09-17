import fs from 'node:fs';
import path from 'node:path';
import sharp from 'sharp';
import { readBarcodesFromImageData, prepareZXingModule } from 'zxing-wasm/reader';

const wasmPath = path.resolve('node_modules/zxing-wasm/reader/zxing_reader.wasm');
await prepareZXingModule({ overrideURL: wasmPath, fallback: () => fetch(new URL('file://'+wasmPath)).then(r=>r.arrayBuffer()) });

const out = [];
for (const file of process.argv.slice(2)) {
  let svg = fs.readFileSync(file, 'utf8');
  if (svg.startsWith('data:image/svg+xml;base64,')) svg = Buffer.from(svg.split('base64,')[1],'base64').toString('utf8');
  if (!svg.startsWith('<svg')) { out.push({file: path.basename(file), error: 'not an svg: '+svg.slice(0,60)}); continue; }
  const img = sharp(Buffer.from(svg), {density: 150});
  const { data, info } = await img.raw().ensureAlpha().toBuffer({resolveWithObject:true});
  const res = await readBarcodesFromImageData({data: new Uint8ClampedArray(data), width: info.width, height: info.height}, {tryHarder: true, binarizer: 1 /*LocalAverage*/});
  const b = res?.[0];
  out.push({file: path.basename(file), px: `${info.width}x${info.height}`, decoded: !!b, format: b?.format, bytes: b?.bytes?.length,
    text: b ? (b.text||'').slice(0,200).replace(/\r/g,'\\r').replace(/\n/g,'\\n') : null});
}
console.log(JSON.stringify(out, null, 1));
