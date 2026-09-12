// Compile the actual cata_web_yield implementation and test real browser wakeups.
// CDDA_EMXX=/path/to/em++ CDDA_PLAYWRIGHT_MODULE=/path/to/playwright-core \
// CDDA_RUNTIME_BACKEND=jspi node bench/scheduler_browser_test.js /path/to/patched/cdda
// Not a full SDL/game latency or physical Chromebook test.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require(process.env.CDDA_PLAYWRIGHT_MODULE || 'playwright-core');
const source = path.resolve(process.argv[2]);
const backend = process.env.CDDA_RUNTIME_BACKEND || 'asyncify';
assert(['asyncify', 'jspi'].includes(backend));
assert(process.env.CDDA_EMXX, 'Set CDDA_EMXX');
const out = path.join(__dirname, 'out/scheduler-' + backend);
fs.mkdirSync(out, { recursive: true });
process.env.TMPDIR = out;
fs.writeFileSync(path.join(out, 'main.cpp'), `
#include "cata_web_yield.h"
#include <emscripten.h>
int main() {
    for (int phase = 1; phase <= 4; ++phase) {
        EM_ASM({ window.phase = $0; }, phase);
        cata_web::wait_for_input(2000);
        EM_ASM({
            if (window.delivered.length < $0) throw new Error("resumed before input handler");
            window.resumed = $0;
        }, phase);
    }
    EM_ASM({ window.phase = 99; window.fallbackStart = performance.now(); });
    for (int i = 0; i < 20; ++i) cata_web::wait_for_input(16);
    EM_ASM({
        window.fallbackMs = performance.now() - window.fallbackStart;
        window.idleUsedMessageChannel = !!Module._cataWebYieldChannel;
    });
    // Forced yield remains available after idle waits and cannot deadlock.
    cata_web::yield_now();
    EM_ASM({
        window.paintOrder = [];
        window.realRaf = window.requestAnimationFrame;
        window.realCancel = window.cancelAnimationFrame;
        window.requestAnimationFrame = function(cb) {
            return realRaf(function(t) {
                cb(t);
                queueMicrotask(function() { paintOrder.push("raf-microtask"); });
            });
        };
    });
    cata_web::yield_paint();
    EM_ASM({
        paintOrder.push("resumed");
        window.requestAnimationFrame = function() { throw new Error("hidden rAF requested"); };
        Object.defineProperty(document, "hidden", { configurable: true, get: function() { return true; } });
        window.hiddenStart = performance.now();
    });
    cata_web::yield_paint();
    EM_ASM({
        window.hiddenMs = performance.now() - hiddenStart;
        delete document.hidden;
        window.cancelledFrames = 0;
        window.requestAnimationFrame = function() { return 999; };
        window.cancelAnimationFrame = function(id) { if(id === 999) ++cancelledFrames; };
    });
    cata_web::yield_paint();
    EM_ASM({
        window.requestAnimationFrame = realRaf;
        window.cancelAnimationFrame = realCancel;
        window.done = true;
    });
    return 0;
}
`);
execFileSync(process.env.CDDA_EMXX, [path.join(out, 'main.cpp'),
  path.join(source, 'src/cata_web_yield.cpp'), '-I' + path.join(source, 'src'),
  '-std=c++17', '-O2', '-DEMSCRIPTEN',
  ...(backend === 'jspi' ? ['-sJSPI', '-fwasm-exceptions'] : ['-sASYNCIFY', '-fexceptions']),
  '-sASSERTIONS=1', '-sENVIRONMENT=web', '-sINITIAL_MEMORY=16777216',
  '-o', path.join(out, 'test.js')], { stdio: 'inherit' });

async function run() {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const page = await browser.newPage(), errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('https://scheduler.test/**', async route => {
      const name = new URL(route.request().url()).pathname.slice(1);
      if (name) {
        assert(['test.js', 'test.wasm'].includes(name));
        return route.fulfill({ path: path.join(out, name),
          contentType: name.endsWith('.wasm') ? 'application/wasm' : 'application/javascript' });
      }
      return route.fulfill({ contentType: 'text/html', body: `<!doctype html>
<input id="text"><script>
window.delivered=[]; window.phase=0; window.resumed=0;
// Stand-in for existing SDL/shell consumers: installed BEFORE runtime waiters.
for(const name of ['keydown','keyup','input','compositionend']) {
  window.addEventListener(name, event => delivered.push({name,data:event.data||event.key||''}));
}
var Module={};</script><script src="test.js"></script>` });
    });
    await page.goto('https://scheduler.test/');
    const latencies = [];
    for (let phase = 1; phase <= 4; ++phase) {
      await page.waitForFunction(n => window.phase === n, phase);
      const start = Date.now();
      if (phase === 1) await page.keyboard.down('ArrowRight');
      if (phase === 2) await page.keyboard.up('ArrowRight');
      if (phase === 3) await page.evaluate(() => document.getElementById('text').dispatchEvent(
        new InputEvent('input', { data: '日本', bubbles: true })));
      if (phase === 4) await page.evaluate(() => document.getElementById('text').dispatchEvent(
        new CompositionEvent('compositionend', { data: '包帯', bubbles: true })));
      await page.waitForFunction(n => window.resumed >= n, phase, { timeout: 1500 });
      latencies.push(Date.now() - start); // includes Playwright roundtrips, not game latency
    }
    await page.waitForFunction(() => window.done, undefined, { timeout: 10000 });
    const result = await page.evaluate(() => ({ delivered, fallbackMs, idleUsedMessageChannel, paintOrder, hiddenMs, cancelledFrames }));
    assert.deepEqual(errors, []);
    assert.deepEqual(result.delivered.map(e => e.name), ['keydown', 'keyup', 'input', 'compositionend']);
    assert.deepEqual(result.delivered.slice(2).map(e => e.data), ['日本', '包帯']);
    assert(result.fallbackMs >= 250, 'idle wait must not hot-spin');
    assert.equal(result.idleUsedMessageChannel, false);
    assert.deepEqual(result.paintOrder, ['raf-microtask', 'resumed']);
    assert(result.hiddenMs >= 80 && result.hiddenMs < 1500);
    assert.equal(result.cancelledFrames, 1);
    console.log('PASS real paint: post-rAF task, hidden fallback, cancelled throttled frame');
    console.log('PASS actual scheduler ' + backend + ': input order, key/IME wake, 20 bounded idle waits, forced yield');
    console.log(JSON.stringify({ ...result, testRoundtripMs: latencies }));
  } finally { await browser.close(); }
}
run().catch(e => { console.error(e); process.exitCode = 1; });
