import { createMatrix } from '/home/user/qr-coding-react/src/lib/qrcode.js';
import { renderQrSvg } from '/home/user/qr-coding-react/src/lib/render.js';
for (const pct of [10,15,20,25,30,35,45]) {
  const m = createMatrix('https://a.fr', {});
  const r = renderQrSvg(m, { logo:'x', logoPercent:pct, margin:32, cell:8, ecc:'M' });
  console.log(`pct=${pct} size=${r.size} coverage=${(r.logoCoverage*100).toFixed(1)}% warnings=${r.warnings.length} :: ${r.warnings.join(' | ').slice(0,150)}`);
}
