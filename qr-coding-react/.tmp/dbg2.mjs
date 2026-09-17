import { createMatrix } from '/home/user/qr-coding-react/src/lib/qrcode.js';
import { renderQrSvg } from '/home/user/qr-coding-react/src/lib/render.js';
const payload='https://a.fr/quelque-chose-d-un-peu-plus-long-pour-monter-en-version';
const m = createMatrix(payload, {});
console.log('size', m.size, 'version', m.version);
for (const ecc of ['M','H']) for (const pct of [22,45]) {
  const r = renderQrSvg(m, { logo:'x', logoPercent:pct, margin:32, cell:8, ecc });
  console.log(`ecc=${ecc} pct=${pct} -> applied=${r.logoPercent}% cov=${(r.logoCoverage*100).toFixed(1)} warn=${r.warnings.length} :: ${r.warnings.join(' | ')}`);
}
