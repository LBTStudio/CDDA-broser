// Regression tests execute the actual shell loader with real streams/wasm,
// and an in-memory Cache Storage/network adapter. No game-speed claims.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../shell/index.html'), 'utf8');
const code = html.slice(html.indexOf('      const ASSET_CACHE ='),
  html.indexOf('      window.addEventListener("menuready"'));
assert(code.includes('async function instantiateCachedWasm'));
const wasmBytes = Uint8Array.from([0, 97, 115, 109, 1, 0, 0, 0]);
const dataBytes = Uint8Array.from([10, 20, 30, 40]);

// Exercise device defaults and explicit recovery choices using actual shell code.
const selectionStart = html.indexOf('      function prefersWebGL(');
const selectionCode = html.slice(selectionStart, html.indexOf('      // Activity speed changes scheduling only;', selectionStart));
assert(selectionStart >= 0);
for (const [saved, available, expected] of [
  [null, true, 'opengles2'], [null, false, 'game'], [null, 'throw', 'game'],
  ['software', true, 'software'], ['game', true, 'game'],
  ['opengles2', false, 'opengles2'], ['invalid', true, 'opengles2'],
]) {
  let probes = 0, released = 0, onChange;
  const writes = [];
  const select = { value: '', addEventListener(name, callback) { assert.equal(name, 'change'); onChange = callback; } };
  vm.runInNewContext(selectionCode, {
    localStorage: { getItem: () => saved, setItem: (...args) => writes.push(args) },
    document: {
      getElementById: () => select,
      createElement(name) {
        assert.equal(name, 'canvas'); ++probes;
        return { getContext(type, attributes) {
          assert.equal(type, 'webgl'); assert.equal(attributes.failIfMajorPerformanceCaveat, true);
          if (available === 'throw') throw new Error('GPU unavailable');
          return available ? { getExtension: () => ({ loseContext() { ++released; } }) } : null;
        } };
      },
    },
  });
  assert.equal(select.value, expected);
  const explicit = ['software', 'game', 'opengles2'].includes(saved);
  assert.equal(probes, explicit ? 0 : 1);
  assert.equal(released, !explicit && available === true ? 1 : 0);
  select.value = 'software'; onChange();
  assert.deepEqual(writes, [['cdda_web_renderer_v1', 'software']]);
}
console.log('PASS graphics default: accelerated context only, probe released, explicit choices preserved');

// Run the actual live preference controls, including unavailable storage.
const activityStart = html.indexOf('      // Activity speed changes scheduling only;');
const activityCode = html.slice(activityStart, html.indexOf('      /* Per-player save isolation:', activityStart));
for (const saved of [null, 'fast', 'normal', 'invalid', 'throw']) {
  const events = new Map(), writes = [], window = {};
  const select = { value: '', addEventListener: (name, fn) => events.set(name, fn) };
  vm.runInNewContext(activityCode, {
    window, document: { getElementById: () => select },
    localStorage: {
      getItem() { if (saved === 'throw') throw new Error('blocked'); return saved; },
      setItem(...args) { if (saved === 'throw') throw new Error('blocked'); writes.push(args); },
    },
  });
  assert.equal(window.CDDA_ACTIVITY_FAST, saved !== 'normal');
  for (const value of ['normal', 'fast']) {
    select.value = value; events.get('change')();
    assert.equal(window.CDDA_ACTIVITY_FAST, value === 'fast');
  }
  for (const name of ['keydown', 'keyup', 'keypress']) {
    let stopped = false;
    events.get(name)({ stopPropagation() { stopped = true; } });
    assert(stopped, name + ' must not leak from the preference control to SDL');
  }
  if (saved !== 'throw') assert.equal(writes.length, 2);
}
const unloadLine = html.split('\n').find(line => line.includes('window.onbeforeunload ='));
for (const [dirty, pending] of [[false, false], [true, false], [false, true], [true, true]]) {
  const window = { game_unsaved: dirty, cdda_persistence_pending: pending };
  vm.runInNewContext(unloadLine, { window });
  let warned = false;
  window.onbeforeunload({ preventDefault() { warned = true; } });
  assert.equal(warned, dirty || pending);
}
console.log('PASS live activity preference: default, persistence, unavailable storage, key isolation, pending-save exit warning');

// Execute the real post-mount renderer helper, not a duplicate implementation.
const rendererStart = html.indexOf('      function applyWebRenderer(');
assert(rendererStart >= 0);
const rendererSource = html.slice(rendererStart, html.indexOf('      /* The runtime mounts IDBFS', rendererStart));
const rendererContext = vm.createContext({ console: { warn() {} } });
vm.runInContext(rendererSource, rendererContext);
function rendererFixture(initial) {
  const files = new Map(initial);
  const writes = [];
  const fs = {
    analyzePath: p => ({ exists: files.has(p) }),
    readFile: p => files.get(p),
    mkdirTree() {},
    writeFile(p, value) { writes.push(p); files.set(p, value); },
  };
  return { files, writes, fs };
}
{
  const config = '/home/player/.cataclysm-dda/config';
  const file = config + '/options.json';
  const initial = [{ name: 'RENDERER', value: 'software' },
    { name: 'USE_LANG', value: 'ja' }, { name: 'AUTOSAVE', value: 'true' },
    { name: 'TILES', value: 'UltimateCataclysm', custom: 123 }];
  const e = rendererFixture([[file, JSON.stringify(initial)], ['/home/other/options.json', 'untouched']]);
  const apply = mode => rendererContext.applyWebRenderer(e.fs, config, mode);
  assert.equal(apply('opengles2'), true);
  assert.deepEqual(JSON.parse(e.files.get(file)), initial.map(o => o.name === 'RENDERER' ? { ...o, value: 'opengles2' } : o));
  assert.equal(apply('opengles2'), false, 'unchanged choice does not dirty IDBFS');
  assert.equal(apply('software'), true, 'compatibility recovery must work');
  assert.equal(apply('game'), false);
  assert.equal(apply('invalid'), false);
  assert.deepEqual(JSON.parse(e.files.get(file)), initial);
  assert.equal(e.files.get('/home/other/options.json'), 'untouched');
  assert.deepEqual(e.writes, [file, file]);
  for (const malformed of ['{broken', '{}', '[null]', '[1]']) {
    const bad = rendererFixture([[file, malformed]]);
    assert.equal(rendererContext.applyWebRenderer(bad.fs, config, 'opengles2'), false);
    assert.equal(bad.files.get(file), malformed);
    assert.deepEqual(bad.writes, []);
  }
  const fresh = rendererFixture([]);
  assert.equal(rendererContext.applyWebRenderer(fresh.fs, config, 'opengles2'), true);
  assert.deepEqual(JSON.parse(fresh.files.get(file)), [{ name: 'RENDERER', value: 'opengles2' }]);
  const failed = rendererFixture([]);
  failed.fs.writeFile = () => { throw new Error('quota'); };
  assert.equal(rendererContext.applyWebRenderer(failed.fs, config, 'opengles2'), false);
  assert(html.includes('const rendererChanged = applyWebRenderer(fs, configDir, rendererMode.value);'));
  assert(html.includes('return languageChanged || rendererChanged;'));
  console.log('PASS renderer: WebGL/software/game selection, no-op saves, profile isolation, malformed options and write failure');
}

function environment(options = {}) {
  const entries = new Map();
  const requests = [];
  let offline = false, cacheFailure = false;
  let streamCalls = 0, bufferCalls = 0, fatal = 0, receives = 0;
  let streamFailure = !!options.streamFailure;
  const module = {};
  const warnings = [];
  const cache = {
    async match(url) {
      const entry = entries.get(url);
      return entry ? new Response(entry.blob, { headers: entry.headers }) : undefined;
    },
    async put(url, response) {
      if (cacheFailure || options.quota) throw new Error('quota');
      entries.set(url, { blob: await response.blob(), headers: [...response.headers] });
    },
  };
  const context = vm.createContext({
    console: { warn: (...args) => warnings.push(args), error: () => {} },
    performance, Response, TransformStream: options.noTransform ? undefined : TransformStream,
    WebAssembly: {
      instantiateStreaming: options.noStreaming ? undefined : async (response, imports) => {
        ++streamCalls;
        if (streamFailure) {
          streamFailure = false;
          await response.arrayBuffer(); // emulate a consumed response on failure
          throw new Error('stream compiler unavailable');
        }
        return WebAssembly.instantiateStreaming(response, imports);
      },
      instantiate: async (bytes, imports) => {
        ++bufferCalls;
        return WebAssembly.instantiate(bytes, imports);
      },
    },
    caches: {
      async open() { if (options.noCache) throw new Error('storage blocked'); return cache; },
      async keys() { return []; },
    },
    async fetch(url, init = {}) {
      requests.push({ url, init });
      if (offline) throw new Error('offline');
      if (options.httpError) return new Response('missing', { status: 404 });
      if (init.headers && init.headers['If-None-Match'] === 'v1') {
        return new Response(null, { status: 304 });
      }
      const isWasm = url.endsWith('.wasm');
      const bytes = options.invalidWasm && isWasm ? dataBytes : isWasm ? wasmBytes : dataBytes;
      let offset = 0;
      const body = new ReadableStream({
        pull(controller) {
          if (options.brokenStream) { controller.error(new Error('disconnected')); return; }
          if (offset === bytes.length) controller.close();
          else controller.enqueue(bytes.slice(offset, ++offset));
        },
      });
      return new Response(body, { headers: {
        ETag: 'v1', 'Content-Length': String(bytes.length),
        'Content-Type': isWasm && !options.badMime ? 'application/wasm' : 'application/octet-stream',
      } });
    },
    loadingMessage: { textContent: '' },
    navigator: { storage: { persist() { return Promise.resolve(true); } } },
    Module: module,
    showFatalError() { ++fatal; },
    async loadScript(name) {
      if (name.endsWith('.data.js')) {
        if (module.getPreloadedPackage) {
          assert.deepEqual(new Uint8Array(module.getPreloadedPackage()), dataBytes);
          assert.equal(module.getPreloadedPackage(), null); // release once consumed
        }
      } else if (module.instantiateWasm) {
        const result = module.instantiateWasm({}, instance => {
          assert(instance instanceof WebAssembly.Instance);
          ++receives;
        });
        assert.equal(Object.keys(result).length, 0);
      }
    },
  });
  vm.runInContext('let booted = false;\n' + code +
    '\nglobalThis.api = {cachedAssetResponse, instantiateCachedWasm, boot, assetTags};', context);
  return {
    api: context.api, entries, requests, module, warnings,
    offline() { offline = true; }, failCache() { cacheFailure = true; },
    counts() { return { streamCalls, bufferCalls, fatal, receives }; },
  };
}

async function completeBoot(env) {
  await env.api.boot();
  for (let n = 0; n < 100; ++n) {
    if (env.counts().receives || env.counts().fatal) return;
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  throw new Error('boot did not complete');
}

(async () => {
  // Fail if production reintroduces Response.clone/tee for caching or wasm.
  const clone = Response.prototype.clone;
  Response.prototype.clone = () => { throw new Error('Unexpected Response.clone retains fallback bytes'); };
  try {
    const cold = environment();
    let response = await cold.api.cachedAssetResponse('cataclysm-tiles.wasm', 'wasm');
    assert.equal(cold.entries.size, 1);
    assert.equal(cold.api.assetTags['cataclysm-tiles.wasm'], 'v1');
    assert.deepEqual(new Uint8Array(await response.arrayBuffer()), wasmBytes);
    response = await cold.api.cachedAssetResponse('cataclysm-tiles.wasm', 'wasm');
    assert.equal(cold.requests.at(-1).init.headers['If-None-Match'], 'v1');
    assert.deepEqual(new Uint8Array(await response.arrayBuffer()), wasmBytes);
    cold.offline();
    response = await cold.api.cachedAssetResponse('cataclysm-tiles.wasm', 'wasm');
    const instance = await cold.api.instantiateCachedWasm(response, {});
    assert(instance.instance instanceof WebAssembly.Instance);
    assert.equal(cold.counts().streamCalls, 1);
    console.log('PASS cold cache / 304 / offline cache / exact bytes / no cloning');

    for (const options of [{}, { noCache: true }, { quota: true }, { noTransform: true },
                           { badMime: true }, { noStreaming: true }, { streamFailure: true }]) {
      const env = environment(options);
      await completeBoot(env);
      assert.equal(env.counts().receives, 1);
      assert.equal(env.counts().fatal, 0);
      assert.equal(env.module.instantiateWasm, undefined);
      assert.equal(env.module.getPreloadedPackage(), null);
      if (options.streamFailure) {
        assert.equal(env.counts().bufferCalls, 1);
        assert.equal(env.requests.length, 3); // retry only on actual failure
      } else {
        assert.equal(env.requests.length, 2); // one request per large asset
      }
      if (options.badMime || options.noStreaming) assert.equal(env.counts().streamCalls, 0);
      console.log('PASS boot / release hooks / fallback', JSON.stringify(options));
    }
    const offlineBoot = environment();
    await offlineBoot.api.cachedAssetResponse('cataclysm-tiles.data', 'data');
    await offlineBoot.api.cachedAssetResponse('cataclysm-tiles.wasm', 'wasm');
    offlineBoot.offline();
    await completeBoot(offlineBoot);
    assert.equal(offlineBoot.counts().receives, 1);
    console.log('PASS fully cached offline boot');

    for (const options of [{ noCache: true }, { brokenStream: true }, { httpError: true }]) {
      const env = environment(options);
      if (options.noCache) env.offline();
      await assert.rejects(env.api.cachedAssetResponse('cataclysm-tiles.data', 'data'));
    }
    const invalid = environment({ invalidWasm: true });
    await completeBoot(invalid);
    assert.equal(invalid.counts().fatal, 1);
    assert.equal(invalid.counts().receives, 0);
    assert.equal(invalid.module.instantiateWasm, undefined);
    console.log('PASS corrupt wasm / broken stream / missing asset / no offline cache errors');
  } finally {
    Response.prototype.clone = clone;
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
