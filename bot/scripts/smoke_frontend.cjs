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
   localStorage.setItem('xynmacro-changelog-seen','1.8.0-beta.1');
   localStorage.setItem('xynmacro-auto-update','0');
   localStorage.setItem('dbog-bg','none');
   window.qaCommands=[];
   window.qaState={version:'1.8.0-beta.1',running:false,available_stats:['Health','Agility','Ki Control','Physical Damage','Ki Damage'],config:{training_order:['Health','Agility','Ki Control','Physical Damage','Ki Damage'],agility_mode:'v2',health_mode:'v2_track',ki_v8_mode:'v2_ring',mouse_click_button:'left',respawn_settle_sec:1,scan_rate_limit_hz:0,startup_window_mode:'unchanged',restart_after_hit_delay_sec:.2},game_window:{found:true,minimized:false,width:1920,height:1080},screen:{width:1920,height:1080,device:'mock',scale:1},telemetry:{},button_calibration:{current:{},overrides:{}},region_calibration:{current:{},overrides:{}}};
   window.__TAURI__={core:{invoke:async(command,args)=>{
     if(command==='proxy_get') return args.path.startsWith('/logs')?[]:structuredClone(window.qaState);
     if(command==='send_to_python') {window.qaCommands.push(args); if(args.action==='set')window.qaState.config[args.value.key]=args.value.value;return {ok:true,value:args.value?.value};}
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
 await page.screenshot({path:path.join(output,'compact-800.png')});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({ok:true,screenshots:output,errors}));
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
