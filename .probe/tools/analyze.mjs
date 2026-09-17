import fs from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire('/home/user/.probe/');
const raw = fs.readFileSync('/tmp/g/h.png');
// decode PNG via sharp
const sharp = require('sharp');
const {data,info}=await sharp('/tmp/g/h.png').raw().toBuffer({resolveWithObject:true});
console.log('px', info.width, info.height, 'ch', info.channels);
const S = info.width, margin = 40*(S/290), cell = 10*(S/290), n = 21;
function at(r,c){const x=Math.round(margin+c*cell+cell/2), y=Math.round(margin+r*cell+cell/2); const i=(y*info.width+x)*info.channels; return data[i]>128?'.':'#';}
for(let r=0;r<n;r++){ let row=''; for(let c=0;c<n;c++) row+=at(r,c); console.log(row); }
console.log('\nbg outside quiet zone (top-left 5px):', at(0,0), 'sample corners:', data[0], data[info.channels*(info.width*2)+0]);
