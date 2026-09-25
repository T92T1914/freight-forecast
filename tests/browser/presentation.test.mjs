import assert from 'node:assert/strict';
import {before, after, test} from 'node:test';
import {createServer} from 'node:http';
import {readFile, mkdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';

// Only an owned loopback server and fresh headless contexts. No saved profiles,
// visible fallback, desktop input or connection to an existing browser.
const root = fileURLToPath(new URL('../../_site/', import.meta.url));
const mime = {'.html':'text/html', '.css':'text/css', '.js':'text/javascript',
  '.mjs':'text/javascript', '.json':'application/json', '.svg':'image/svg+xml'};
let browser, server, base;
before(async () => {
  server = createServer(async (request, response) => {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    if (pathname === '/favicon.ico') { response.writeHead(204).end(); return; }
    const name = pathname === '/' ? 'index.html' : pathname.slice(1);
    if (!/^[a-z0-9.-]+$/i.test(name)) { response.writeHead(404).end(); return; }
    try {
      const bytes = await readFile(path.join(root, name));
      response.writeHead(200, {'Content-Type': mime[path.extname(name)] ?? 'text/plain'});
      response.end(bytes);
    } catch (_) { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  const channel = process.env.FREIGHT_BROWSER_CHANNEL;
  if (channel && !['chrome','chromium'].includes(channel)) throw Error('Unsupported channel');
  browser = await chromium.launch({headless:true, chromiumSandbox:true, ...(channel ? {channel} : {})});
  console.log(`Isolated headless browser ${browser.version()}; sandbox requested; fresh contexts`);
});
after(async () => {
  if (browser) await browser.close();
  if (server) await new Promise(resolve => server.close(resolve));
});
async function fixture(t, options = {}, blockedStorage = false) {
  const context = await browser.newContext({viewport:{width:1280,height:900}, ...options});
  const failures = [], external = [];
  await context.route('**/*', route => {
    if (new URL(route.request().url()).origin !== base) {
      external.push(route.request().url()); return route.abort();
    }
    return route.continue();
  });
  if (blockedStorage) await context.addInitScript(() => {
    Object.defineProperty(window, 'localStorage', {get() { throw new DOMException('Blocked','SecurityError'); }});
  });
  const page = await context.newPage();
  page.on('pageerror', error => failures.push(error.message));
  page.on('response', response => { if (response.status() >= 400) failures.push(`${response.status()} ${response.url()}`); });
  t.after(async () => {
    await context.close();
    assert.deepEqual(failures, []);
    assert.deepEqual(external, [], 'No font, analytics or other external page requests');
  });
  return page;
}
const background = page => page.locator('body').evaluate(e => getComputedStyle(e).backgroundColor);
async function ready(page, location = '/') {
  await page.goto(base + location);
  await page.locator('#appearance:not([disabled])').waitFor();
}
async function capture(page, name, locator = 'body') {
  if (!process.env.FREIGHT_SCREENSHOT_DIR) return;
  await mkdir(process.env.FREIGHT_SCREENSHOT_DIR, {recursive:true});
  await page.locator(locator).screenshot({path:path.join(process.env.FREIGHT_SCREENSHOT_DIR, name+'.png')});
}

test('Auto follows system, override survives reload, and theme keeps evidence selection', async t => {
  const page = await fixture(t, {colorScheme:'dark'});
  await ready(page);
  assert.equal(await background(page), 'rgb(9, 9, 9)');
  await page.locator('#choice option').last().waitFor({state:'attached'});
  await page.locator('#backtest-slice option').last().waitFor({state:'attached'});
  await page.locator('#choice').selectOption('9');
  await page.locator('#backtest-slice').selectOption('2');
  const before = await page.locator('#metrics').textContent();
  const beforeBacktest = await page.locator('#backtest-rows').textContent();
  const selectedURL = page.url();
  await page.locator('#appearance').selectOption('clair');
  assert.equal(await background(page), 'rgb(248, 247, 243)');
  assert.equal(await page.locator('#metrics').textContent(), before);
  assert.equal(await page.locator('#backtest-rows').textContent(), beforeBacktest);
  assert.equal(page.url(), selectedURL);
  await page.reload();
  await page.locator('#interactive:visible').waitFor();
  assert.equal(await page.locator('#choice').inputValue(), '9');
  assert.equal(await page.locator('#appearance').inputValue(), 'clair');
  await page.emulateMedia({colorScheme:'dark'});
  assert.equal(await background(page), 'rgb(248, 247, 243)');
  await page.locator('#appearance').selectOption('auto');
  assert.equal(await background(page), 'rgb(9, 9, 9)');
  await page.emulateMedia({colorScheme:'light'});
  assert.equal(await background(page), 'rgb(248, 247, 243)');
  await page.locator('#choice').selectOption('4');
  await page.goBack();
  assert.equal(await page.locator('#choice').inputValue(), '9');
});

test('blocked storage leaves usable appearance controls', async t => {
  const page = await fixture(t, {colorScheme:'light'}, true);
  await ready(page);
  await page.locator('#appearance').selectOption('obscur');
  assert.equal(await background(page), 'rgb(9, 9, 9)');
  await page.reload();
  assert.equal(await background(page), 'rgb(248, 247, 243)');
});

test('without JavaScript the report data, chart and Auto still work', async t => {
  const page = await fixture(t, {javaScriptEnabled:false, colorScheme:'dark'});
  await page.goto(base+'/real-data.html');
  assert.equal(await background(page), 'rgb(9, 9, 9)');
  assert.equal(await page.locator('#appearance').isDisabled(), true);
  assert.equal(await page.locator('td[data-series="actual"]').count(), 72);
  assert.equal(await page.locator('svg g[data-series]').count(), 4);
  await page.emulateMedia({colorScheme:'light'});
  assert.equal(await background(page), 'rgb(248, 247, 243)');
});

for (const mode of ['clair','obscur']) test(`${mode} keeps chart meaning at wide and narrow sizes`, async t => {
  const page = await fixture(t);
  await ready(page, '/real-data.html');
  const data = await page.locator('td[data-value]').evaluateAll(cells => cells.map(e => [e.dataset.series,e.dataset.value]));
  const paths = await page.locator('svg g[data-series]>path:first-child').evaluateAll(nodes => nodes.map(e => e.getAttribute('d')));
  await page.locator('#appearance').selectOption(mode);
  assert.equal(await page.locator('td[data-value]').count(), 288);
  assert.deepEqual(await page.locator('td[data-value]').evaluateAll(cells => cells.map(e => [e.dataset.series,e.dataset.value])), data);
  assert.deepEqual(await page.locator('svg g[data-series]>path:first-child').evaluateAll(nodes => nodes.map(e => e.getAttribute('d'))), paths);
  await capture(page, `report-${mode}-wide`);
  await capture(page, `chart-${mode}`, '.chart-wrap');
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.locator('.chart-wrap').focus();
  assert.equal(await page.evaluate(() => document.activeElement.className), 'chart-wrap');
  assert.equal(await page.locator('.chart-wrap').evaluate(e => getComputedStyle(e).outlineStyle), 'solid');
  await page.keyboard.press('ArrowRight');
  await capture(page, `report-${mode}-mobile`);
  // Enlarged text is independent of OS display settings or a real browser profile.
  await page.addStyleTag({content:'body { font-size: 34px !important; }'});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true,
    JSON.stringify(await page.locator('body *').evaluateAll(nodes => nodes.filter(e => e.getBoundingClientRect().right > innerWidth && !e.closest('.table-wrap,.chart-wrap')).map(e => [e.tagName,e.className,e.getBoundingClientRect().right]))));
  await page.goto(base+'/');
  await page.locator('#interactive:visible').waitFor();
  assert.equal(await page.locator('#appearance').inputValue(), mode);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await capture(page, `site-${mode}-mobile`);
  await page.addStyleTag({content:'body { font-size: 34px !important; }'});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.locator('#choice').focus();
  assert.equal(await page.evaluate(() => document.activeElement.id), 'choice');
});

test('print uses Clair without changing an explicit Obscur screen preference', async t => {
  const page = await fixture(t);
  await ready(page, '/real-data.html');
  await page.locator('#appearance').selectOption('obscur');
  await page.emulateMedia({media:'print'});
  assert.equal(await background(page), 'rgb(248, 247, 243)');
  assert.equal(await page.locator('svg>rect').evaluate(e => getComputedStyle(e).fill), 'rgb(253, 252, 248)');
  assert.equal(await page.evaluate(() => localStorage.getItem('freight-forecast.appearance.v1')), 'obscur');
  await page.emulateMedia({media:'screen', forcedColors:'active'});
  assert.equal(await page.evaluate(() => matchMedia('(forced-colors: active)').matches), true);
  await page.emulateMedia({forcedColors:'none'});
  assert.equal(await background(page), 'rgb(9, 9, 9)');
});

async function fonts(page, selector) {
  const session = await page.context().newCDPSession(page);
  try {
    await session.send('DOM.enable'); await session.send('CSS.enable');
    const {root} = await session.send('DOM.getDocument');
    const {nodeId} = await session.send('DOM.querySelector', {nodeId:root.nodeId, selector});
    return (await session.send('CSS.getPlatformFontsForNode', {nodeId})).fonts.filter(f => f.glyphCount > 0);
  } finally { await session.detach(); }
}
test('a deliberately missing font supplies a readable non-Inter fallback', async t => {
  const page = await fixture(t);
  await ready(page, '/real-data.html');
  await page.evaluate(() => {
    const sample = document.createElement('p'); sample.id='missing-face';
    sample.style.fontFamily='"Freight deliberately missing face", Arial, sans-serif';
    sample.textContent='Readable fallback 123'; document.body.append(sample);
  });
  const providers = await fonts(page, '#missing-face');
  assert.ok(providers.length > 0);
  assert.ok(providers.every(f => !f.postScriptName.startsWith('Inter')));
  console.log('Separate missing-font fallback:', JSON.stringify(providers));
});
test('installed Inter supplies all six intended faces in both appearances',
  {skip:process.env.FREIGHT_REQUIRE_INTER !== '1'}, async t => {
  const page = await fixture(t);
  await ready(page, '/real-data.html');
  const faces = [[400,'normal','Inter-Regular'],[600,'normal','Inter-SemiBold'],[700,'normal','Inter-Bold'],
    [400,'italic','Inter-Italic'],[600,'italic','Inter-SemiBoldItalic'],[700,'italic','Inter-BoldItalic']];
  await page.evaluate(faces => {
    for (const [weight,style,name] of faces) {
      const sample = document.createElement('p'); sample.id=name;
      sample.style.fontWeight=String(weight); sample.style.fontStyle=style;
      sample.textContent='Freight typography sample 123'; document.body.append(sample);
    }
  }, faces);
  await page.evaluate(() => document.fonts.ready);
  for (const mode of ['obscur','clair']) {
    await page.locator('#appearance').selectOption(mode);
    for (const [, , name] of faces) {
      const providers = await fonts(page, '#'+name);
      assert.ok(providers.some(f => f.postScriptName === name && f.glyphCount > 0), JSON.stringify({mode,name,providers}));
      assert.ok(providers.every(f => f.postScriptName === name), 'Latin sample must not use fallback');
      console.log('Controlled local-font specimen:', JSON.stringify({mode,name,providers}));
    }
    for (const [selector, expected] of [['h1','Inter-Bold'],['.lead','Inter-Regular'],['label[for="appearance"]','Inter-SemiBold']]) {
      assert.ok((await fonts(page, selector)).some(f => f.postScriptName === expected));
    }
  }
  for (const mode of ['obscur','clair']) {
    await page.goto(base+`/real-data-${mode}.svg`);
    await page.evaluate(() => document.fonts.ready);
    assert.ok((await fonts(page, 'svg>g>text:first-child')).some(f => f.postScriptName === 'Inter-SemiBold'));
    assert.ok((await fonts(page, 'svg>g>text:nth-child(2)')).some(f => f.postScriptName === 'Inter-Regular'));
    console.log(`Standalone ${mode} SVG uses actual Inter SemiBold and Regular glyphs`);
  }
});
