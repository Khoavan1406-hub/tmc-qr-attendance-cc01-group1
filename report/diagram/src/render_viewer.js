const {chromium}=require('playwright');const fs=require('fs');
(async()=>{const [,,inp,out]=process.argv;const xml=fs.readFileSync(inp,'utf8');
const b=await chromium.launch();const p=await b.newPage({deviceScaleFactor:Number(process.argv[4]||2),viewport:{width:2400,height:2400}});
p.on('pageerror',e=>console.log('E:',e.message));
await p.setContent('<html><body style="margin:0;background:#fff"><div id="g"></div></body></html>');
await p.addScriptTag({path:__dirname+'/../dio/src/main/webapp/js/viewer-static.min.js'});
await p.evaluate((xml)=>{const el=document.getElementById('g');new GraphViewer(el,mxUtils.parseXml(xml).documentElement,{toolbar:null,nav:false,border:10,'check-visible-state':false,'auto-fit':false});},xml);
await p.waitForSelector('#g svg',{timeout:10000,state:'attached'});await p.waitForTimeout(1500);
const el=await p.$('#g svg');await el.screenshot({path:out});await b.close();})();
