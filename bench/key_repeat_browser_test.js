// Source-derived routing and consumer with REAL SDL + Chromium keyboard events.
// First run key_repeat_test.py SOURCE to generate harness.cpp.
// CDDA_EMXX=... CDDA_PLAYWRIGHT_MODULE=... node bench/key_repeat_browser_test.js SOURCE
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require(process.env.CDDA_PLAYWRIGHT_MODULE || 'playwright-core');
const source = path.resolve(process.argv[2]);
const out = path.join(__dirname, 'out/key-repeat');
assert(process.env.CDDA_EMXX, 'Set CDDA_EMXX');
assert(fs.existsSync(path.join(out, 'harness.cpp')), 'Run key_repeat_test.py first');
process.env.TMPDIR = out;
execFileSync(process.env.CDDA_EMXX, [path.join(out, 'harness.cpp'),
  '-I' + path.join(source, 'src'), '-isystem', path.join(source, 'src/third-party'),
  '-std=c++17', '-O2', '-DEMSCRIPTEN', '-DWEB_BROWSER_TEST', '-sUSE_SDL=2',
  '-sJSPI', '-fwasm-exceptions', '-sALLOW_MEMORY_GROWTH', '-sEXIT_RUNTIME=0',
  '-sENVIRONMENT=web', '-o', path.join(out, 'browser.js')], { stdio: 'inherit' });

async function run() {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const page = await browser.newPage(), errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('https://repeat.test/**', route => {
      const name = new URL(route.request().url()).pathname.slice(1);
      if (name) {
        assert(['browser.js', 'browser.wasm'].includes(name));
        return route.fulfill({ path: path.join(out, name),
          contentType: name.endsWith('.wasm') ? 'application/wasm' : 'application/javascript' });
      }
      return route.fulfill({ contentType: 'text/html', body: `<!doctype html>
<canvas id="canvas" tabindex="0"></canvas><script>
var Module = {canvas: document.getElementById('canvas')};
</script><script src="browser.js"></script>` });
    });
    await page.goto('https://repeat.test/');
    await page.waitForFunction(() => window.ready);
    await page.locator('canvas').focus();
    const reset = () => page.evaluate(() => Module._reset_state());
    const consume = () => page.evaluate(() => Module._consume(0));
    const pump = () => page.evaluate(() => Module._pump());
    const repeat = async (key, n) => { for(let i = 0; i < n; ++i) await page.keyboard.down(key); };
    // CDP repeated keydown sets the real DOM repeat flag; SDL translates it.
    await reset();
    await page.keyboard.down('l'); await repeat('l', 100); await pump();
    assert.deepEqual(await page.evaluate(() => [Module._queued(), Module._repeat_pending()]), [1, 1]);
    await page.keyboard.up('l');
    assert.equal(await consume(), 108); assert.equal(await consume(), 0);
    // No backlog when releasing after the initial tap was already processed.
    await reset(); await page.keyboard.down('l'); assert.equal(await consume(), 108);
    await repeat('l', 100); await pump(); await page.keyboard.up('l');
    assert.equal(await consume(), 0);
    // Distinct taps preserve order even with non-consuming pumps between reads.
    await reset();
    for(const key of ['h', 'l', 'j', 'k', 'h', 'l']) await page.keyboard.press(key);
    const taps = [];
    for(let i=0;i<6;++i) { await pump(); taps.push(await consume()); }
    assert.deepEqual(taps, [104,108,106,107,104,108]); assert.equal(await consume(), 0);
    // Hold A, press/hold B, then release both before the next command.
    await reset(); await page.keyboard.down('h'); await repeat('h', 20);
    await page.keyboard.down('l'); await repeat('l', 20); await pump();
    await page.keyboard.up('h'); await page.keyboard.up('l');
    assert.equal(await consume(), 104); assert.equal(await consume(), 108); assert.equal(await consume(), 0);
    // Browser blur uses SDL's real window callback; it must revoke pending repeat.
    await reset(); await page.keyboard.down('l'); assert.equal(await consume(), 108);
    await repeat('l', 20); await pump();
    await page.evaluate(() => window.dispatchEvent(new Event('blur')));
    assert.equal(await consume(), 0);
    await page.keyboard.up('l');
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.keyboard.down('h'); await repeat('h', 2);
    assert.equal(await consume(), 104); assert.equal(await consume(), 104);
    await page.keyboard.up('h'); assert.equal(await consume(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS real Chromium/SDL: repeat bursts, keyup cancellation, manual FIFO, direction changes, blur/refocus');
  } finally { await browser.close(); }
}
run().catch(e => { console.error(e); process.exitCode = 1; });
