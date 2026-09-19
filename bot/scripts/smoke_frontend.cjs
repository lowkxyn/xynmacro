const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../src');
const output = path.join(require('node:os').tmpdir(), 'xynmacro-ui-qa');
fs.mkdirSync(output, {recursive:true});
(async () => {
 const browser = await chromium.launch({headless:true, channel:'msedge'});
 try {
 const page = await browser.newPage({viewport:{width:1100,height:720}});
 const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('https://xynmacro.test/**', async route => {
   const name = new URL(route.request().url()).pathname.slice(1) || 'index.html';
   const mime = name.endsWith('.css')?'text/css':name.endsWith('.js')?'text/javascript':name.endsWith('.woff2')?'font/woff2':'text/html';
   await route.fulfill({body:fs.readFileSync(path.join(root,name)),contentType:mime});
 });
 await page.route('https://raw.githubusercontent.com/**', route=>route.fulfill({json:{}}));
 await page.addInitScript(() => {
   for(const key of ['xynmacro-welcome-seen','xynmacro-wspain-seen']) localStorage.setItem(key,'1');
   localStorage.setItem('xynmacro-changelog-seen','1.8.0-beta.2');
   localStorage.setItem('xynmacro-auto-update','0');
   localStorage.setItem('dbog-bg','none');
   window.qaCommands=[];
   window.qaState={version:'1.8.0-beta.2',running:false,available_stats:['Health','Agility','Ki Control','Physical Damage','Ki Damage'],config:{training_order:['Health','Agility','Ki Control','Physical Damage','Ki Damage'],agility_mode:'v2',health_mode:'v2_track',ki_v8_mode:'v2_ring',mouse_click_button:'left',respawn_settle_sec:1,scan_rate_limit_hz:0,startup_window_mode:'unchanged',restart_after_hit_delay_sec:.2},game_window:{found:true,minimized:false,width:1920,height:1080},screen:{width:1920,height:1080,device:'mock',scale:1},telemetry:{},button_calibration:{current:{},overrides:{}},region_calibration:{current:{},overrides:{}}};
   window.__TAURI__={core:{invoke:async(command,args)=>{
     if(command==='proxy_get') return args.path.startsWith('/logs')?[]:structuredClone(window.qaState);
     if(command==='send_to_python') {
       window.qaCommands.push(args);
       if(args.action==='set')window.qaState.config[args.value.key]=args.value.value;
       if(args.action==='shutdown_cancel') {
         window.qaState.shutdown={pending:false,status:'cancelled',seconds_remaining:0};
         return {ok:true,msg:'Shutdown countdown cancelled',shutdown:window.qaState.shutdown};
       }
       if(args.action==='notifications_save') {
         window.qaState.notifications={configured:true,enabled:args.value.enabled,progress_minutes:args.value.progress_minutes,status:'Ready'};
         return {ok:true,notifications:window.qaState.notifications};
       }
       return {ok:true,value:args.value?.value};
     }
     if(command==='get_backend_port')return 12345;
     return null;
   }},event:{listen:async()=>()=>{}},window:{getCurrentWindow:()=>({isMaximized:async()=>false,onResized:async()=>()=>{}})}};
 });
 await page.goto('https://xynmacro.test/');
 await page.waitForTimeout(1200);
 await page.evaluate(()=>{document.querySelector('#splash')?.remove();document.querySelectorAll('.welcome-overlay,.changelog-overlay,.wspain-overlay').forEach(e=>e.classList.remove('open'));});
 await page.locator('[data-view="controls"]').click();
 await page.waitForTimeout(300);
 assert.equal(await page.locator('#statOrderList .stat-item').count(),5);
 for (const style of ['classic','aero']) {
   await page.evaluate(style=>document.documentElement.setAttribute('data-ui',style),style);
   for(const width of [1100,800,640]) {
     await page.setViewportSize({width,height:650});
     await page.waitForTimeout(100);
     await page.screenshot({path:path.join(output,`training-${style}-${width}.png`)});
     const collisions=await page.locator('#statOrderList .stat-item').evaluateAll(rows=>rows.filter(row=>{
       const label=row.querySelector('.stat-item-label').getBoundingClientRect();
       const controls=row.querySelector('.stat-item-controls').getBoundingClientRect();
       return label.right>controls.left+1;
     }).length);
     assert.equal(collisions,0,`${style} ${width} queue overlap`);
   }
 }
 await page.setViewportSize({width:800,height:650});
 await page.locator('[data-view="input"]').click();
 await page.locator('#mouseClickButton').selectOption('right');
 await page.waitForTimeout(100);
 assert.equal(await page.evaluate(()=>qaState.config.mouse_click_button),'right');
 await page.locator('#startupWindowMode').selectOption('windowed');
 await page.waitForTimeout(100);
 assert.equal(await page.evaluate(()=>qaState.config.startup_window_mode),'windowed');
 assert.notEqual(await page.locator('#btnWindowNow').evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(239, 239, 239)');
 await page.screenshot({path:path.join(output,'input-800.png')});
 await page.locator('[data-view="tuning"]').click();
 assert.equal(await page.locator('#kiV8ClickDelay').isDisabled(),true);
 assert.equal(await page.locator('#kiLatencyComp').isDisabled(),false);
 await page.screenshot({path:path.join(output,'minigames-800.png')});
 assert.equal(await page.locator('.tb-tag,.wspain-pop,.confetti-layer').count(),0);
 for(const style of ['classic','aero']) {
   await page.evaluate(style=>document.documentElement.setAttribute('data-ui',style),style);
   for(const zoom of [0.8,1,1.25]) {
     await page.evaluate(zoom=>applyInterfaceZoom(zoom),zoom);
     for(const width of [1100,800,640]) {
       await page.setViewportSize({width,height:650});
       for(const view of ['input','controls']) {
         await page.locator(`[data-view="${view}"]`).click();
         const bounds=await page.locator('.app-shell').boundingBox();
         assert.ok(bounds.x+bounds.width<=width+2,`${style} ${zoom} ${width} workspace width ${JSON.stringify(bounds)}`);
         assert.ok(bounds.y+bounds.height<=652,`${style} ${zoom} ${width} workspace height`);
         const overflow=await page.locator(`#${view}`).evaluate(e=>e.scrollWidth>e.clientWidth+2);
         assert.equal(overflow,false,`${style} ${zoom} ${width} ${view} overflow`);
       }
     }
   }
 }
 await page.locator('[data-view="input"]').click();
 await page.locator('#zoomValue').scrollIntoViewIfNeeded();
 await page.waitForTimeout(350);
 await page.screenshot({path:path.join(output,'zoom125-input-640.png')});
 await page.keyboard.press('Control+0');
 assert.equal(await page.locator('#zoomValue').innerText(),'100%');
 await page.keyboard.press('Control+-');
 assert.equal(await page.locator('#zoomValue').innerText(),'90%');
 await page.locator('#zoomReset').click();
 await page.setViewportSize({width:800,height:650});
 await page.locator('[data-view="controls"]').click();
 await page.evaluate(()=>applyInterfaceZoom(1.25));
 await page.waitForTimeout(350);
 const handles=page.locator('#statOrderList .stat-drag-handle');
 await handles.first().scrollIntoViewIfNeeded();
 const first=await handles.nth(0).boundingBox();
 const third=await handles.nth(2).boundingBox();
 await page.mouse.move(first.x+first.width/2, first.y+first.height/2);
 await page.mouse.down();
 await page.mouse.move(third.x+third.width/2, third.y+third.height, {steps:8});
 await page.mouse.up();
 await page.waitForTimeout(300);
 assert.equal(await page.evaluate(()=>qaState.config.training_order[2]),'Health','drag at 125% moves one item to intended row');
 await page.evaluate(()=>applyInterfaceZoom(1));
 await page.locator('#notificationUrl').fill('https://discord.com/api/webhooks/123456789012345678/'+'x'.repeat(60));
 await page.locator('#notificationEnabled').check();
 await page.locator('#notificationInterval').selectOption('5');
 await page.waitForTimeout(1000);
 assert.equal(await page.locator('#notificationInterval').inputValue(),'5','poll must preserve draft');
 await page.locator('#notificationSave').click();
 await page.waitForTimeout(200);
 assert.equal(await page.locator('#notificationUrl').inputValue(),'','saved secret cleared from field');
 assert.equal(await page.evaluate(()=>qaState.notifications.progress_minutes),5);
 await page.locator('#notificationPanel').scrollIntoViewIfNeeded();
 await page.waitForTimeout(350);
 await page.screenshot({path:path.join(output,'notifications-800.png')});
 await page.evaluate(()=>{qaState.shutdown={pending:true,status:'pending',seconds_remaining:55};});
 await page.waitForTimeout(1000);
 assert.equal(await page.locator('#cancelShutdownButton').isVisible(),true);
 await page.locator('#cancelShutdownButton').click();
 assert.equal(await page.locator('#shutdownBanner').isVisible(),false);
 await page.keyboard.press('Control+k');
 await page.locator('#paletteInput').fill('right click');
 await page.waitForTimeout(100);
 assert.match(await page.locator('#paletteResults').innerText(),/Macro click button/);
 await page.keyboard.press('Escape');
 await page.evaluate(()=>{qaState.running=true;qaState.current_state='Physical Damage';});
 await page.waitForTimeout(1000);
 assert.equal(await page.evaluate(()=>document.documentElement.classList.contains('macro-lightweight')),true);
 await page.evaluate(()=>document.getElementById('reduceRunEffects').click());
 assert.equal(await page.evaluate(()=>document.documentElement.classList.contains('macro-lightweight')),false);
 await page.evaluate(()=>document.querySelector('.window-frame').classList.add('compact'));
 await page.setViewportSize({width:800,height:32});
 await page.evaluate(()=>{qaState.running=false;qaState.shutdown={pending:true,status:'pending',seconds_remaining:50};});
 await page.waitForTimeout(1000);
 assert.equal(await page.locator('#hudCancelShutdown').isVisible(),true);
 await page.screenshot({path:path.join(output,'compact-shutdown-800.png')});
 await page.locator('#hudCancelShutdown').click();
 assert.equal(await page.locator('#hudCancelShutdown').isVisible(),false);
 await page.screenshot({path:path.join(output,'compact-800.png')});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({ok:true,screenshots:output,errors}));
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
