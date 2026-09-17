import fs from 'node:fs';
const a = fs.readFileSync('/tmp/raw-matrix.svg', 'utf8');
const b = fs.readFileSync('/tmp/app-svg.svg', 'utf8');
console.log('A raw   :', a.slice(0, 260), '\n   rects:', (a.match(/<rect/g) || []).length);
console.log('\nB app svg:', b.slice(0, 400), '\n   rects:', (b.match(/<rect/g) || []).length, 'circles:', (b.match(/<circle/g) || []).length);

// count drawn dark modules in B for the top-left 7x7 finder + a few rows
const n = 69, cell = 10, m = 24;
const rects = [...b.matchAll(/<rect x="([\d.]+)" y="([\d.]+)" width="10" height="10"\/>/g)].map(x => [Math.round((+x[1] - m) / cell), Math.round((+x[2] - m) / cell)]);
console.log('\nB module rects count (excluding bg/finder rects):', rects.length);
console.log('B modules in row 0:', rects.filter(r => r[0] === 0).map(r => r[1]).sort((x, y) => x - y).join(','));
// extract matrix from A
let dark = new Set();
for (const x of a.matchAll(/<rect x="(\d+)" y="(\d+)" width="10" height="10"\/>/g)) {
  const c = +x[1] / 10, r = +x[2] / 10; dark.add(r + ':' + c);
}
const bset = new Set(rects.map(r => r[0] + ':' + r[1]));
let missing = [], extra = [];
for (const k of dark) if (!bset.has(k) && !(k.startsWith('0:'))) missing.push(k);
for (const k of bset) if (!dark.has(k)) extra.push(k);
console.log('\ndark modules in raw matrix:', dark.size, '| drawn as squares by app:', bset.size);
console.log('missing in app svg (first 12):', missing.slice(0, 12));
console.log('extra in app svg (first 12):', extra.slice(0, 12));
// find diff in row 8 (a plain data row)
let md = [];
for (let c = 0; c < n; c++) { const k = 8 + ':' + c; if (dark.has(k) !== bset.has(k)) md.push([c, dark.has(k) ? '#' : '.', bset.has(k) ? '#' : '.']); }
console.log('row 8 diffs:', JSON.stringify(md));
