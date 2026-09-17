import fs from 'node:fs';
const src = fs.readFileSync('/home/user/uploads/qr-coding (1).html','utf8');
const js = src.slice(src.indexOf('<script>', src.indexOf('</head>'))+8, src.lastIndexOf('</script>'));
fs.writeFileSync('/home/user/orig/orig.js', js);
const css = src.slice(src.indexOf('<style>'), src.indexOf('</style>')+8);
fs.writeFileSync('/home/user/orig/orig.css', css);
console.log('js bytes', js.length, 'css bytes', css.length);
console.log('--- first lines of extracted js ---');
console.log(js.split('\n').slice(0,3).join('\n').slice(0,200));
