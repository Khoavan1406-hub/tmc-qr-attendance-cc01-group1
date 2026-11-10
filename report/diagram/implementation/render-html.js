const {chromium}=require('playwright');(async()=>{const b=await chromium.launch();const p=await b.newPage({deviceScaleFactor:2,viewport:{width:1220,height:900}});
await p.goto('file://'+process.argv[2]);await p.waitForTimeout(300);await (await p.$('#w')).screenshot({path:process.argv[3]});await b.close();})();
