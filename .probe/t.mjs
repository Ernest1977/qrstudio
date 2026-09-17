import puppeteer from 'puppeteer';
const b = await puppeteer.launch({args:['--no-sandbox','--disable-dev-shm-usage']});
const p = await b.newPage();
await p.goto('data:text/html,<h1 id=x>hello</h1>');
console.log('title:', await p.$eval('#x', e=>e.textContent));
await p.setViewport({width:800,height:400});
await p.screenshot({path:'/home/user/.probe/probe.png'});
await b.close();
