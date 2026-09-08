// Execute the actual shell bridge, not a reimplementation. No game-speed claim.
// node bench/ime_bridge_test.js
// Optional real Chromium IME protocol check:
// CDDA_PLAYWRIGHT_MODULE=/absolute/path/to/playwright-core node bench/ime_bridge_test.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../shell/index.html'), 'utf8');
const code = html.slice(html.indexOf('      window.cddaImeState ='),
  html.indexOf('      var Module ='));
assert(code.includes('function imeSwallowKey'));

function environment() {
  const timers = [];
  let document;
  function target() {
    const handlers = new Map();
    return {
      value: '',
      addEventListener(name, fn) {
        if (!handlers.has(name)) handlers.set(name, []);
        handlers.get(name).push(fn);
      },
      fire(name, props = {}) {
        const event = { target: this, stopped: false,
          stopImmediatePropagation() { this.stopped = true; }, ...props };
        for (const fn of handlers.get(name) || []) fn(event);
        return event;
      },
      focus() { document.activeElement = this; },
      blur() {
        document.activeElement = document.body;
        this.fire('blur');
        // Some browsers finalize the in-flight composition during blur.
        this.fire('compositionend', { data: '遅延確定' });
      },
    };
  }
  const proxy = target(), canvas = target(), other = target(), window = target();
  document = Object.assign(target(), { body: target(), getElementById: () => proxy });
  document.activeElement = canvas;
  const context = vm.createContext({ document, window, canvas, errorShown: false,
    setTimeout(fn) { timers.push(fn); } });
  vm.runInContext(code, context);
  return { proxy, canvas, other, window, document, context,
    state: window.cddaImeState, flush() { while (timers.length) timers.shift()(); } };
}

function checks() {
  const e = environment();
  const { proxy, state, window, other, document, canvas } = e;
  const set = window.CDDA_SET_TEXT_INPUT;
  const commits = () => Array.from(state.commits);
  proxy.fire('compositionstart');
  proxy.fire('compositionupdate', { data: '禁止' });
  proxy.fire('compositionend', { data: '禁止' });
  proxy.value = '禁止'; proxy.fire('input');
  assert.deepEqual(commits(), []);
  assert.equal(state.preview, null);
  set(1);
  assert.equal(document.activeElement, proxy);
  proxy.fire('compositionstart');
  proxy.fire('compositionupdate', { data: 'にほん' });
  proxy.value = 'にほん'; proxy.fire('input', { isComposing: true });
  assert.equal(state.preview, 'にほん');
  assert.deepEqual(commits(), []);
  for (const key of ['Enter', 'Escape', 'ArrowLeft', 'ArrowRight']) {
    for (const type of ['keydown', 'keyup']) {
      assert(window.fire(type, { target: proxy, key }).stopped);
      assert(!window.fire(type, { target: other, key, isComposing: true }).stopped);
    }
  }
  proxy.fire('compositionend', { data: '日本' });
  proxy.fire('input'); // Chromium has already delivered the composing input.
  assert.deepEqual(commits(), ['日本']);
  assert.equal(state.preview, '');
  assert(!window.fire('keydown', { target: proxy, key: 'Enter' }).stopped);
  assert(!window.fire('keydown', { target: proxy, key: 'a' }).stopped);
  assert(window.fire('keydown', { target: proxy, keyCode: 229 }).stopped);
  assert(!window.fire('keydown', { target: other, keyCode: 229 }).stopped);
  proxy.value = '包帯'; proxy.fire('input', { inputType: 'insertFromPaste' });
  proxy.fire('compositionstart');
  proxy.fire('compositionend', { data: '' }); // cancelled conversion
  assert.deepEqual(commits(), ['日本', '包帯']);
  set(0); // includes synchronous late compositionend from blur()
  assert.deepEqual(commits(), []);
  assert.equal(state.preview, null);
  proxy.fire('compositionend', { data: 'さらに遅延' });
  assert.deepEqual(commits(), []);
  assert(!window.fire('keydown', { target: proxy, keyCode: 229 }).stopped);
  set(1);
  proxy.fire('compositionstart'); proxy.fire('compositionend', { data: '再入力' });
  assert.deepEqual(commits(), ['再入力']);
  document.activeElement = canvas;
  document.fire('mousedown'); e.flush();
  assert.equal(document.activeElement, proxy);
  document.activeElement = other;
  document.fire('mousedown'); e.flush(); set(1);
  assert.equal(document.activeElement, other); // real UI keeps focus
  e.context.errorShown = true;
  proxy.fire('compositionend', { data: 'エラー後' });
  assert.deepEqual(commits(), ['再入力']);
  console.log('PASS IME: composition/commit/cancel/paste, key isolation, closed-field leakage, refocus');
}

async function chromiumCheck() {
  const { chromium } = require(process.env.CDDA_PLAYWRIGHT_MODULE);
  const tmp = path.join(__dirname, 'out/ime-browser');
  fs.mkdirSync(tmp, { recursive: true });
  process.env.TMPDIR = tmp;
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const page = await browser.newPage();
    await page.setContent('<canvas id="canvas" tabindex="0"></canvas><input id="ime-proxy"><input id="other">');
    await page.addScriptTag({ content:
      'const canvas=document.getElementById("canvas"); let errorShown=false;\n' + code });
    await page.evaluate(() => window.CDDA_SET_TEXT_INPUT(1));
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Input.imeSetComposition', { text: 'にほん', selectionStart: 3, selectionEnd: 3 });
    assert.equal(await page.evaluate(() => cddaImeState.preview), 'にほん');
    await cdp.send('Input.insertText', { text: '日本' });
    assert.deepEqual(await page.evaluate(() => cddaImeState.commits), ['日本']);
    await page.evaluate(() => {
      CDDA_SET_TEXT_INPUT(0);
      document.getElementById('ime-proxy').dispatchEvent(new CompositionEvent('compositionend', { data: '漏れ' }));
      CDDA_SET_TEXT_INPUT(1);
    });
    await cdp.send('Input.insertText', { text: '包帯' });
    assert.deepEqual(await page.evaluate(() => cddaImeState.commits), ['包帯']);
    console.log('PASS real Chromium CDP composition + Japanese commit + reopen (shell only, not ChromeOS IME)');
  } finally {
    await browser.close();
  }
}
checks();
if (process.env.CDDA_PLAYWRIGHT_MODULE) chromiumCheck().catch(err => { console.error(err); process.exitCode = 1; });
