import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { createRequire } from 'node:module';
import sharp from 'sharp';
const require = createRequire('/home/user/.probe/');
const { readBarcodesFromImageData, prepareZXingModule } = require('zxing-wasm/reader');

const wasmPath = path.resolve('node_modules/zxing-wasm/reader/zxing_reader.wasm');
await prepareZXingModule({ overrideURL: wasmPath, fallback: () => fetch('file://' + wasmPath).then(r => r.arrayBuffer()) });

/* Robust: build an SVG from a known grid with a chosen quiet zone, rasterize, auto-detect grid, sample. */
function buildSvg(n, cell, margin, get, finder) {
  const S = n * cell + margin * 2;
  let b = '';
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
    if (!get(r, c)) continue;
    const inF = (r < 7 && c < 7) || (r < 7 && c >= n - 7) || (r >= n - 7 && c < 7);
    if (inF) continue;
    b += `<rect x="${margin + c * cell}" y="${margin + r * cell}" width="${cell}" height="${cell}"/>`;
  }
  if (finder === 'stroke') {
    for (const [fr, fc] of [[0, 0], [0, n - 7], [n - 7, 0]]) {
      const x = margin + fc * cell, y = margin + fr * cell, s = 7 * cell;
      b += `<rect x="${x}" y="${y}" width="${s}" height="${s}" fill="none" stroke="#000" stroke-width="${cell}"/>`;
      b += `<rect x="${x + 2 * cell}" y="${y + 2 * cell}" width="${3 * cell}" height="${3 * cell}" fill="#000"/>`;
    }
  } else {
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (get(r, c)) b += `<rect x="${margin + c * cell}" y="${margin + r * cell}" width="${cell}" height="${cell}"/>`;
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${S}" height="${S}" viewBox="0 0 ${S} ${S}"><rect width="${S}" height="${S}" fill="#fff"/><g fill="#000">${b}</g></svg>`;
}

async function sample(svgStr, n) {
  const { data, info } = await sharp(Buffer.from(svgStr)).greyscale().flatten({ background: '#ffffff' }).resize(1000, 1000, { fit: 'fill' }).raw().toBuffer({ resolveWithObject: true });
  // ink profile
  const px = (x, y) => 255 - data[y * info.width + x];
  const col = new Array(info.width).fill(0), row = new Array(info.height).fill(0);
  for (let y = 0; y < info.height; y += 2) for (let x = 0; x < info.width; x += 2) { const v = px(x, y); col[x] += v; row[y] += v; }
  const first = row.findIndex(v => v > 0), last = row.length - 1 - [...row].reverse().findIndex(v => v > 0);
  const firstC = col.findIndex(v => v > 0), lastC = col.length - 1 - [...col].reverse().findIndex(v => v > 0);
  const cell = (last - first + 1) / n, cellC = (lastC - firstC + 1) / n;
  const g = [];
  for (let r = 0; r < n; r++) { let s = ''; for (let c = 0; c < n; c++) { const x = Math.round(firstC + (c + .5) * cellC), y = Math.round(first + (r + .5) * cell); s += px(x, y) > 128 ? '#' : '.'; } g.push(s); }
  return { grid: g, inkBox: [firstC, first, lastC - firstC + 1, last - first + 1], cellPx: cell.toFixed(1) };
}

const QR = require('qrcode/lib/core/qrcode.js');
const ref = QR.create('HELLO', { errorCorrectionLevel: 'M' });
const n = ref.modules.size;
const get = (r, c) => !!ref.modules.get(r, c);

for (const [label, finder] of [['plain-squares', 'plain'], ['stroke-finders', 'stroke']]) {
  for (const [mm, margin] of [['10-cell', 10 * (1000 / (n * 10 + 20))], ['40px', 40]]) {
    const svg = buildSvg(n, 10, Math.round(margin), get, finder);
    const { grid, inkBox, cellPx } = await sample(svg, n);
    let d = 0; for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if ((grid[r][c] === '#') !== get(r, c)) d++;
    console.log(`${label} margin=${Math.round(margin)} (${mm}) cellPx=${cellPx} inkBox=${inkBox} -> grid diffs vs true matrix: ${d}`);
    if (d) { grid.slice(0, 8).forEach(r => console.log('   ', r)); }
  }
}
