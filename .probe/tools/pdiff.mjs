import sharp from 'sharp';
import fs from 'node:fs';
const load = async f => (await sharp(f).resize(290,290,{fit:'fill',background:'#fff'}).flatten({background:'#fff'}).greyscale().raw().toBuffer({resolveWithObject:true}));
const a = await load('/tmp/D.svg');
const b = await load('/tmp/E.svg');
const W=a.info.width,H=a.info.height;
let diffs=[];
for(let y=0;y<H;y++)for(let x=0;x<W;x++){const va=a.data[y*W+x]<128, vb=b.data[y*W+x]<128; if(va!==vb) diffs.push({x,y,va,vb});}
console.log('D vs E pixel diffs:', diffs.length);
const boxes = {};
for(const d of diffs){const k=`module r${Math.floor((d.y-40)/10)}c${Math.floor((d.x-40)/10)}@${d.va?'white→black':'black→white'}`; boxes[k]=(boxes[k]||0)+1;}
console.log(Object.entries(boxes).slice(0,30));
fs.writeFileSync('/tmp/D.png', await sharp('/tmp/D.svg').resize(600,600,{fit:'fill'}).flatten({background:'#fff'}).png().toBuffer());
fs.writeFileSync('/tmp/E.png', await sharp('/tmp/E.svg').resize(600,600,{fit:'fill'}).flatten({background:'#fff'}).png().toBuffer());
