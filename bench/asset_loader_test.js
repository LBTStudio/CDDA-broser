// Regression tests execute the actual shell loader with real streams/wasm,
// and an in-memory Cache Storage/network adapter. No game-speed claims.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../shell/index.html'), 'utf8');
const code = html.slice(html.indexOf('      const LEGACY_ASSET_CACHE ='),
  html.indexOf('      window.addEventListener("menuready"'));
assert(code.includes('async function instantiateCachedWasm'));

const loaderStart = html.indexOf('      function loadScriptDirect(src) {');
const loaderEnd = html.indexOf('      /* ---- Persistent asset cache', loaderStart);
assert(loaderStart >= 0 && loaderEnd > loaderStart, 'find persistent generated-script loader');
const loaderSource = html.slice(loaderStart, loaderEnd);
assert(loaderSource.includes('cachedAssetResponse(src, label)'));
assert(loaderSource.includes('URL.createObjectURL(new Blob([source]'));
assert(loaderSource.includes('URL.revokeObjectURL(objectUrl)'));

async function testPersistentScriptLoader() {
  const cacheCalls = [], appended = [], revoked = [], blobSources = new Map();
  let nextBlob = 0, forceCacheFailure = false, forceBlobExecutionFailure = false, context;
  const fakeURL = {
    createObjectURL(blob) {
      const uri = 'blob:test/' + (++nextBlob);
      blobSources.set(uri, blob);
      return uri;
    },
    revokeObjectURL(uri) { revoked.push(uri); blobSources.delete(uri); },
  };
  const document = {
    createElement(tag) { assert.equal(tag, 'script'); return { src: '', onload: null, onerror: null }; },
    body: { appendChild(script) {
      appended.push(script.src);
      if (script.src.startsWith('blob:')) {
        if (forceBlobExecutionFailure) {
          script.onerror();
          return;
        }
        blobSources.get(script.src).text().then(source => {
          vm.runInContext(source, context);
          script.onload();
        }).catch(() => script.onerror());
      } else {
        script.onload(); // emulate a successful regular HTTP script request
      }
    } },
  };
  context = vm.createContext({
    Blob, URL: fakeURL, document,
    Module: { getPreloadedPackage() {}, instantiateWasm() {} },
    console: { warn() {} },
    async cachedAssetResponse(url, label) {
      cacheCalls.push({ url, label });
      if (forceCacheFailure) throw new Error('offline cache miss');
      return { text: async () => 'globalThis.loadedScripts = (globalThis.loadedScripts || []).concat(' + JSON.stringify(url) + ');' };
    },
  });
  vm.runInContext(loaderSource, context);
  await context.loadScript('cataclysm-tiles.data.js');
  await context.loadScript('cataclysm-tiles.js');
  assert.deepEqual(Array.from(context.loadedScripts), ['cataclysm-tiles.data.js', 'cataclysm-tiles.js']);
  assert.deepEqual(cacheCalls.map(x => x.url), ['cataclysm-tiles.data.js', 'cataclysm-tiles.js']);
  assert.deepEqual(cacheCalls.map(x => x.label), ['データローダー', 'JavaScript本体']);
  assert.equal(appended.length, 2);
  assert(appended.every(src => src.startsWith('blob:')), 'cached scripts must not require a network script URL');
  assert.equal(revoked.length, 2, 'temporary Blob URLs must be released after load');

  // If execution of a pinned bundle script fails, do not load the mutable URL:
  // it could have been deployed from a newer release than the selected WASM.
  forceBlobExecutionFailure = true;
  const beforePinnedFallback = appended.length;
  await assert.rejects(
    context.loadScript('cataclysm-tiles.js', {
      text: async () => 'globalThis.loadedScripts = (globalThis.loadedScripts || []).concat("pinned");',
    }, false),
    /Could not execute cached script cataclysm-tiles.js/
  );
  assert.equal(appended.length, beforePinnedFallback + 1);
  assert(appended.at(-1).startsWith('blob:'), 'pinned script failure must not request the mutable URL');
  assert.equal(appended.includes('cataclysm-tiles.js'), false);
  assert.equal(revoked.length, 3, 'failed pinned Blob URL must also be released');
  forceBlobExecutionFailure = false;

  forceCacheFailure = true;
  await context.loadScript('cataclysm-tiles.js');
  assert.equal(appended.at(-1), 'cataclysm-tiles.js', 'retain the normal URL fallback if cache retrieval fails');
  context.Module.instantiateWasm = undefined;
  forceCacheFailure = false;
  const cacheCallsBeforeStockFallback = cacheCalls.length;
  await context.loadScript('cataclysm-tiles.js');
  assert.equal(appended.at(-1), 'cataclysm-tiles.js', 'stock Emscripten loader must retain the original sibling-asset URL');
  assert.equal(cacheCalls.length, cacheCallsBeforeStockFallback, 'stock loader path must not fetch a Blob script');
  console.log('PASS persistent script loader: offline cache, direct fallback, stock sibling URLs, and Blob cleanup');
}
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
  const event = { returnValue: '', preventDefault() { warned = true; } };
  window.onbeforeunload(event);
  assert.equal(warned, dirty || pending);
  assert.equal(event.returnValue, dirty || pending ? true : '');
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
  const cacheBuckets = new Map();
  for (const [name, entries] of (options.cacheBuckets || [])) {
    cacheBuckets.set(name, new Map(entries));
  }
  if (!cacheBuckets.has('cdda-assets-v2')) {
    cacheBuckets.set('cdda-assets-v2', new Map(options.cacheEntries || []));
  }
  const entries = cacheBuckets.get('cdda-assets-v2');
  const requests = [];
  const failFirstUrls = new Set(options.failFirstUrls || []);
  let offline = false, cacheFailure = false, putCalls = 0;
  let streamCalls = 0, bufferCalls = 0, fatal = 0, receives = 0;
  let streamFailure = !!options.streamFailure;
  const module = {};
  const warnings = [];
  const errors = [];
  const loadingMessage = { textContent: '' };

  function cacheFor(name) {
    if (!cacheBuckets.has(name)) cacheBuckets.set(name, new Map());
    const bucket = cacheBuckets.get(name);
    return {
      async match(url) {
        const entry = bucket.get(url);
        return entry ? new Response(entry.blob, { headers: entry.headers }) : undefined;
      },
      async put(url, response) {
        ++putCalls;
        if (cacheFailure || options.quota || options.failPutAt === putCalls) throw new Error('quota / injected cache-write failure');
        bucket.set(url, { blob: await response.blob(), headers: [...response.headers] });
      },
      async delete(url) {
        if (url === undefined) return cacheBuckets.delete(name);
        return bucket.delete(url);
      },
    };
  }

  const context = vm.createContext({
    console: { warn: (...args) => warnings.push(args), error: (...args) => errors.push(args.map(String).join(' ')) },

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
      async open(name) { if (options.noCache) throw new Error('storage blocked'); return cacheFor(name); },
      async keys() { return [...cacheBuckets.keys()]; },
      async delete(name) { return cacheBuckets.delete(name); },
    },
    async fetch(url, init = {}) {
      requests.push({ url, init });
      if (offline || (options.failUrls || []).includes(url)) throw new Error('offline/network failure');
      if (failFirstUrls.has(url)) {
        failFirstUrls.delete(url);
        throw new Error('injected one-time network failure');
      }
      if (options.httpError) return new Response('missing', { status: 404 });
      const etag = options.etag || 'v1';
      if (init.headers && init.headers['If-None-Match'] === etag) {
        return new Response(null, { status: 304 });
      }
      const isWasm = url.endsWith('.wasm');
      const bytes = options.invalidWasm && isWasm ? dataBytes :
        isWasm ? (options.wasmBytes || wasmBytes) : (options.dataBytes || dataBytes);
      let offset = 0;
      const body = new ReadableStream({
        pull(controller) {
          if (options.brokenStream) { controller.error(new Error('disconnected')); return; }
          if (offset === bytes.length) controller.close();
          else controller.enqueue(bytes.slice(offset, ++offset));
        },
      });
      return new Response(body, { headers: {
        ETag: etag, 'Content-Length': String(bytes.length),
        'Content-Type': isWasm && !options.badMime ? 'application/wasm' :
          url.endsWith('.js') ? 'application/javascript' : 'application/octet-stream',
      } });
    },
    loadingMessage,
    navigator: { storage: { persist() {
      if (options.persistThrow) throw new Error('storage shelf unavailable');
      if (options.persistReject) return Promise.reject(new Error('persistence denied'));
      return Promise.resolve(true);
    } } },
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
    async loadScriptDirect(name) { return context.loadScript(name); },
  });
  vm.runInContext('let booted = false;\n' + code +
    '\nglobalThis.api = {cachedAssetResponse, instantiateCachedWasm, boot, assetTags, readCompleteCachedBundle, commitAssetCacheBatch, get activeAssetCacheName() { return activeAssetCacheName; }};', context);
  return {
    api: context.api, entries, cacheBuckets, requests, module, warnings, errors, loadingMessage, options,
    offline() { offline = true; }, failCache() { cacheFailure = true; },
    snapshotCacheBuckets() { return [...cacheBuckets].map(([name, bucket]) => [name, [...bucket]]); },
    currentBucket() { return cacheBuckets.get(context.api.activeAssetCacheName); },
    counts() { return { streamCalls, bufferCalls, fatal, receives }; },
  };
}

async function completeBoot(env) {
  await env.api.boot();
  for (let n = 0; n < 500; ++n) {
    if (env.counts().receives || env.counts().fatal) return;
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  throw new Error('boot did not complete for ' + JSON.stringify(env.options) +
    '; fatal=' + env.counts().fatal + '; loading=' + env.loadingMessage.textContent +
    '; console=' + env.errors.join(' | '));
}

(async () => {
  await testPersistentScriptLoader();
  // Fail if production reintroduces Response.clone/tee for caching or wasm.
  const clone = Response.prototype.clone;
  Response.prototype.clone = () => { throw new Error('Unexpected Response.clone retains fallback bytes'); };
  try {
    const cold = environment();
    await completeBoot(cold);
    let response = await cold.api.cachedAssetResponse('cataclysm-tiles.wasm', 'wasm');
    assert.equal(cold.api.assetTags['cataclysm-tiles.wasm'], 'v1');
    assert.deepEqual(new Uint8Array(await response.arrayBuffer()), wasmBytes);
    response = await cold.api.cachedAssetResponse('cataclysm-tiles.wasm', 'wasm');
    assert.equal(cold.requests.at(-1).init.headers['If-None-Match'], 'v1');
    assert.deepEqual(new Uint8Array(await response.arrayBuffer()), wasmBytes);
    cold.offline();
    response = await cold.api.cachedAssetResponse('cataclysm-tiles.wasm', 'wasm');
    const instance = await cold.api.instantiateCachedWasm(response, {});
    assert(instance.instance instanceof WebAssembly.Instance);
    assert(cold.currentBucket().has('__cdda_bundle_complete_v3__'));
    console.log('PASS immutable bundle / 304 / offline cache / exact bytes / no cloning');

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
        assert.equal(env.requests.length, 5); // four bundle requests plus one wasm retry
      } else {
        assert.equal(env.requests.length, 4); // data, wasm, and both generated scripts
      }
      if (options.badMime || options.noStreaming) assert.equal(env.counts().streamCalls, 0);
      console.log('PASS boot / release hooks / fallback', JSON.stringify(options));
    }
    const completeBundle = environment();
    await completeBoot(completeBundle);
    const cachedBuckets = completeBundle.snapshotCacheBuckets();
    const offlineBoot = environment({ cacheBuckets: cachedBuckets });
    offlineBoot.offline();
    await completeBoot(offlineBoot);
    assert.equal(offlineBoot.counts().receives, 1);
    assert(offlineBoot.currentBucket().has('__cdda_bundle_complete_v3__'));
    console.log('PASS fully cached four-file offline boot');

    const partialUpdate = environment({
      cacheBuckets: cachedBuckets,
      etag: 'v2',
      failUrls: ['cataclysm-tiles.wasm'],
    });
    await completeBoot(partialUpdate);
    assert.equal(partialUpdate.api.assetTags['cataclysm-tiles.data'], 'v1',
      'one failed asset must select the previous complete bundle, not new data plus old wasm');
    for (const url of ['cataclysm-tiles.data', 'cataclysm-tiles.wasm',
                       'cataclysm-tiles.data.js', 'cataclysm-tiles.js']) {
      const stored = partialUpdate.currentBucket().get(url);
      assert(stored, 'complete old bundle keeps ' + url);
      assert.equal(stored.headers.find(([key]) => key.toLowerCase() === 'x-cdda-etag')?.[1], 'v1',
        'partial network update must not commit a mixture: ' + url);
    }
    console.log('PASS partial deployment failure: use previous coherent bundle and discard staged new files');

    // A partial deployment may recover while streaming a selected older WASM.
    // Retrying must not silently accept the newer ETag and pair it with the
    // previous release's data and generated JS loaders.
    const releaseDrift = environment({
      cacheBuckets: cachedBuckets,
      etag: 'v2',
      failFirstUrls: ['cataclysm-tiles.wasm'],
      streamFailure: true,
    });
    await completeBoot(releaseDrift);
    assert.equal(releaseDrift.counts().receives, 0,
      'never execute a different release’s wasm with the selected cached bundle');
    assert.equal(releaseDrift.counts().fatal, 1,
      'a mismatched release retry must fail closed with a visible error');
    assert.equal(releaseDrift.api.activeAssetCacheName, completeBundle.api.activeAssetCacheName,
      'failed fallback must leave the last known-good cache active');
    assert(releaseDrift.errors.some(line => /different.*release|different.*asset/i.test(line)),
      'mismatched ETag should be reported for diagnosis');
    console.log('PASS WASM fallback release identity: reject a new ETag after partial update fallback');

    const previousName = completeBundle.api.activeAssetCacheName;
    const failedCacheWrite = environment({ cacheBuckets: cachedBuckets, etag: 'v2', failPutAt: 2 });
    await completeBoot(failedCacheWrite);
    assert.equal(failedCacheWrite.counts().receives, 1,
      'the current online boot can use downloaded bytes even when persistent cache update fails');
    assert.equal(failedCacheWrite.api.activeAssetCacheName, previousName,
      'failed cache writes must not switch the active bundle pointer');
    const meta = failedCacheWrite.cacheBuckets.get('cdda-assets-meta-v3');
    const pointer = await new Response(meta.get('__cdda_active_bundle_v3__').blob,
      { headers: meta.get('__cdda_active_bundle_v3__').headers }).text();
    assert.equal(pointer, previousName, 'previous complete bundle remains active');
    assert(failedCacheWrite.currentBucket().has('__cdda_bundle_complete_v3__'));
    for (const url of ['cataclysm-tiles.data', 'cataclysm-tiles.wasm',
                       'cataclysm-tiles.data.js', 'cataclysm-tiles.js']) {
      assert.equal(failedCacheWrite.currentBucket().get(url).headers
        .find(([key]) => key.toLowerCase() === 'x-cdda-etag')?.[1], 'v1',
        'cache write failure must not partially overwrite ' + url);
    }
    assert(![...failedCacheWrite.cacheBuckets.keys()].some(name =>
      name.startsWith('cdda-assets-bundle-v3-') && name !== previousName),
      'incomplete staging bucket must be removed');
    console.log('PASS cache quota/write failure: last complete snapshot and pointer stay intact');

    // If a browser evicts/corrupts one entry in the active cache bucket, the
    // previous immutable snapshot should still boot offline and repair the pointer.
    const firstRelease = environment();
    await completeBoot(firstRelease);
    const firstReleaseName = firstRelease.api.activeAssetCacheName;
    const secondRelease = environment({
      cacheBuckets: firstRelease.snapshotCacheBuckets(),
      etag: 'v2',
    });
    await completeBoot(secondRelease);
    const secondReleaseName = secondRelease.api.activeAssetCacheName;
    assert.notEqual(secondReleaseName, firstReleaseName);
    const damagedBuckets = secondRelease.snapshotCacheBuckets().map(([name, entries]) => [
      name,
      name === secondReleaseName
        ? entries.filter(([key]) => key !== 'cataclysm-tiles.wasm')
        : entries,
    ]);
    const recovered = environment({ cacheBuckets: damagedBuckets });
    recovered.offline();
    await completeBoot(recovered);
    assert.equal(recovered.counts().receives, 1);
    assert.equal(recovered.api.activeAssetCacheName, firstReleaseName,
      'damaged active bundle must roll back to the previous complete snapshot');
    console.log('PASS damaged active bundle: prior immutable snapshot boots offline and active pointer is repaired');

    const unhandledRejections = [];
    const onUnhandledRejection = reason => unhandledRejections.push(reason);
    process.on('unhandledRejection', onUnhandledRejection);
    try {
      for (const options of [{ persistReject: true }, { persistThrow: true }]) {
        const env = environment(options);
        await completeBoot(env);
        await new Promise(resolve => setTimeout(resolve, 0));
        assert.equal(env.counts().fatal, 0, 'optional storage-persistence denial must not abort boot');
      }
      assert.deepEqual(unhandledRejections, [], 'optional storage-persistence failures must be handled');
    } finally {
      process.off('unhandledRejection', onUnhandledRejection);
    }
    console.log('PASS optional storage.persist rejection/throw does not become a fatal or unhandled error');

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
