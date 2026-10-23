#!/usr/bin/env node
// Ensure save export and MOD ZIP installation remain usable with no network
// after their small utility scripts have been fetched and persisted once.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const shell = fs.readFileSync(path.join(root, 'shell/index.html'), 'utf8');
const jszipPath = path.join(root, 'shell/vendor/jszip-3.10.1.min.js');
const saverPath = path.join(root, 'shell/vendor/FileSaver-2.0.0.min.js');
const licensesPath = path.join(root, 'shell/vendor/THIRD-PARTY-LICENSES.md');
const workflow = fs.readFileSync(path.join(root, '.github/workflows/build-and-release.yml'), 'utf8');
const manual = fs.readFileSync(path.join(root, 'manual/build-and-release.yml'), 'utf8');
const localBuild = fs.readFileSync(path.join(root, 'scripts/build-local.sh'), 'utf8');

assert(!/<script[^>]+src="[^"]*jszip/i.test(shell),
  'JSZip must load through persistent Cache Storage, not an unhandled script URL');
assert(!/<script[^>]+src="[^"]*FileSaver/i.test(shell),
  'FileSaver must load through persistent Cache Storage, not an unhandled script URL');
assert(shell.includes('ensureUtilityLibrary("vendor/jszip-3.10.1.min.js", "JSZip")'));
assert(shell.includes('ensureUtilityLibrary("vendor/FileSaver-2.0.0.min.js", "saveAs")'));
assert(shell.includes('precacheUtilityScripts().catch'));
for (const file of [jszipPath, saverPath, licensesPath]) {
  assert(fs.statSync(file).size > 0, 'vendored dependency must be present: ' + path.relative(root, file));
}
assert(fs.readFileSync(jszipPath, 'utf8').includes('JSZip v3.10.1'));
assert(fs.readFileSync(licensesPath, 'utf8').includes('JSZip 3.10.1'));
assert(fs.readFileSync(licensesPath, 'utf8').includes('FileSaver.js 2.0.0'));
assert(fs.readFileSync(licensesPath, 'utf8').includes('pako'));

assert.equal(workflow, manual, 'manual and live workflows must stay in sync');
for (const required of [
  'cp shell/vendor/jszip-3.10.1.min.js cdda/build/vendor/',
  'cp shell/vendor/FileSaver-2.0.0.min.js cdda/build/vendor/',
  'cp shell/vendor/THIRD-PARTY-LICENSES.md cdda/build/vendor/',
]) {
  assert(workflow.includes(required), 'Pages artifact must include: ' + required);
}
for (const required of [
  'cp "$WRAPPER_ROOT/shell/index.html" build/index.html',
  'cp "$WRAPPER_ROOT/shell/vendor/jszip-3.10.1.min.js" build/vendor/',
  'cp "$WRAPPER_ROOT/shell/vendor/FileSaver-2.0.0.min.js" build/vendor/',
  'cp "$WRAPPER_ROOT/shell/vendor/THIRD-PARTY-LICENSES.md" build/vendor/',
]) {
  assert(localBuild.includes(required), 'local build must include: ' + required);
}

const helperStart = shell.indexOf('      /* JSZip/FileSaver power user-initiated');
const helperEnd = shell.indexOf('      async function boot()', helperStart);
assert(helperStart >= 0 && helperEnd > helperStart, 'locate the real persistent utility loader');
const helperSource = shell.slice(helperStart, helperEnd);
assert(helperSource.includes('caches.open(UTILITY_SCRIPT_CACHE)'));
assert(helperSource.includes('cache.put(url'));
assert(helperSource.includes('new Blob([source]'));
assert(helperSource.includes('URL.revokeObjectURL(objectUrl)'));

function smokeTestActualVendoredLibraries() {
  const libraryContext = vm.createContext({
    console, Blob, URL, Response, TextEncoder, TextDecoder,
    setTimeout, clearTimeout, setInterval, clearInterval,
    setImmediate, clearImmediate,
    navigator: { userAgent: 'Mozilla/5.0 Chrome/120.0' },
    document: {
      title: 'CDDA',
      body: { innerText: 'CDDA', appendChild() {} },
      createElement() { return { style: {}, click() {}, setAttribute() {} }; },
    },
  });
  libraryContext.window = libraryContext;
  libraryContext.self = libraryContext;
  libraryContext.global = libraryContext;
  vm.runInContext(fs.readFileSync(jszipPath, 'utf8'), libraryContext, { timeout: 5000 });
  assert.equal(typeof libraryContext.JSZip, 'function', 'vendored JSZip must export the expected global');
  vm.runInContext(fs.readFileSync(saverPath, 'utf8'), libraryContext, { timeout: 5000 });
  assert.equal(typeof libraryContext.saveAs, 'function', 'vendored FileSaver must export saveAs');
}
smokeTestActualVendoredLibraries();

async function makeEnvironment({ offline = false, cacheAvailable = true, bucket = new Map() } = {}) {
  let fetchCalls = 0;
  let objectId = 0;
  const objectUrls = new Map();
  const revoked = [];
  const errors = [];
  let context;
  class FakeURL extends URL {}
  FakeURL.createObjectURL = blob => {
    const url = 'blob:utility/' + (++objectId);
    objectUrls.set(url, blob);
    return url;
  };
  FakeURL.revokeObjectURL = url => {
    revoked.push(url);
    objectUrls.delete(url);
  };
  const cache = {
    async match(url) {
      const saved = bucket.get(url);
      return saved ? new Response(saved.text, { headers: saved.headers }) : undefined;
    },
    async put(url, response) {
      bucket.set(url, { text: await response.text(), headers: [...response.headers] });
    },
  };
  const document = {
    baseURI: 'https://example.test/CDDA-broser/',
    createElement(tag) {
      assert.equal(tag, 'script');
      return { src: '', onload: null, onerror: null };
    },
    body: {
      appendChild(script) {
        objectUrls.get(script.src).text().then(source => {
          vm.runInContext(source, context);
          script.onload();
        }).catch(error => {
          errors.push(String(error));
          script.onerror();
        });
      },
    },
  };
  context = vm.createContext({
    Blob, Response, URL: FakeURL, document,
    console: { warn() { }, error() { } },
    caches: {
      async open(name) {
        assert.equal(name, 'cdda-utility-scripts-v1');
        if (!cacheAvailable) throw new Error('Cache Storage unavailable');
        return cache;
      },
    },
    async fetch(url) {
      ++fetchCalls;
      if (offline) throw new Error('offline');
      const code = url.endsWith('jszip-3.10.1.min.js')
        ? 'window.JSZip = function JSZip() {};'
        : 'window.saveAs = function saveAs() {};';
      return new Response(code, { status: 200, headers: { 'Content-Type': 'text/javascript' } });
    },
  });
  context.window = context;
  vm.runInContext(helperSource + '\nglobalThis.testApi = { ensureUtilityLibrary, precacheUtilityScripts, getUtilityScriptText };', context);
  return {
    context, bucket, objectUrls, revoked, errors,
    fetchCalls: () => fetchCalls,
  };
}

(async () => {
  const online = await makeEnvironment();
  await online.context.testApi.precacheUtilityScripts();
  assert.equal(online.fetchCalls(), 2);
  assert.equal(online.bucket.size, 2, 'boot should persist both utilities in Cache Storage');
  assert.equal(typeof online.context.JSZip, 'undefined', 'pre-cache must not eagerly execute JSZip');
  await online.context.testApi.ensureUtilityLibrary('vendor/jszip-3.10.1.min.js', 'JSZip');
  await online.context.testApi.ensureUtilityLibrary('vendor/FileSaver-2.0.0.min.js', 'saveAs');
  assert.equal(typeof online.context.JSZip, 'function');
  assert.equal(typeof online.context.saveAs, 'function');
  assert.equal(online.revoked.length, 2, 'temporary Blob URLs must be released');

  const offline = await makeEnvironment({ offline: true, bucket: online.bucket });
  await offline.context.testApi.ensureUtilityLibrary('vendor/jszip-3.10.1.min.js', 'JSZip');
  await offline.context.testApi.ensureUtilityLibrary('vendor/FileSaver-2.0.0.min.js', 'saveAs');
  assert.equal(offline.fetchCalls(), 0, 'persisted scripts must not attempt the network offline');
  assert.equal(typeof offline.context.JSZip, 'function');
  assert.equal(typeof offline.context.saveAs, 'function');
  assert.equal(offline.revoked.length, 2);
  console.log('PASS offline dependencies: cache warmup, lazy execution, offline load, and Blob cleanup');

  const unavailable = await makeEnvironment({ offline: true });
  await assert.rejects(
    unavailable.context.testApi.ensureUtilityLibrary('vendor/jszip-3.10.1.min.js', 'JSZip'),
    /unavailable offline/
  );
  assert.equal(unavailable.context.JSZip, undefined);
  console.log('PASS offline dependency miss: user-action loader fails explicitly without a cached copy');
})().catch(error => { console.error(error); process.exitCode = 1; });
