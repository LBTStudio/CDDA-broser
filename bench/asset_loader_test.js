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
