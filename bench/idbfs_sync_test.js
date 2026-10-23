// Test the real EM_ASYNC_JS body from an applied CDDA source tree.
// Usage: node bench/idbfs_sync_test.js /path/to/patched/cdda
// FS and timers are controlled adapters: this is not an IndexedDB/device benchmark.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
if (!process.argv[2]) throw new Error('Expected patched CDDA source directory');
const source = fs.readFileSync(path.join(process.argv[2], 'src/main.cpp'), 'utf8');
const start = source.indexOf('EM_ASYNC_JS( void, mount_idbfs,');
assert(start >= 0);
const end = source.indexOf('\n} );', start);
assert(end > start);
const code = source.slice(source.indexOf('{', start) + 1, end);

function environment(options = {}) {
  const timers = new Map(), events = new Map(), requests = [], notices = [], errors = [];
  const mountPoint = '/home/test_player/.cataclysm-dda';
  let nextTimer = 0, active = 0, maxActive = 0, throwNext = false;
  let restore, write;
  const window = {
    addEventListener(name, fn) { events.set(name, fn); },
    CDDA_ON_IDBFS_ERROR(err) { errors.push(err); if (options.hookThrows) throw new Error('UI'); },
    CDDA_ON_IDBFS_SYNC_STATE(failed, detail) {
      notices.push({ failed, detail });
      if (options.hookThrows) throw new Error('UI');
    },
    CDDA_ON_IDBFS_MOUNTED(root) {
      assert.equal(root, mountPoint);
      if (options.migrationThrows) throw new Error('migration');
      return !!options.migration;
    },
  };
  if (options.noHooks) {
    delete window.CDDA_ON_IDBFS_SYNC_STATE;
    delete window.CDDA_ON_IDBFS_ERROR;
  }
  const document = { visibilityState: 'visible', addEventListener(name, fn) { events.set(name, fn); } };
  const FS = {
    mkdirTree(root) { assert.equal(root, mountPoint); },
    mount(type, opts, root) { assert.equal(root, mountPoint); if (options.mountThrows) throw new Error('mount'); },
    syncfs(populate, callback) {
      requests.push(populate);
      if (throwNext || (populate && options.restoreThrows)) {
        throwNext = false;
        throw new Error('sync exception');
      }
      ++active; maxActive = Math.max(maxActive, active);
      assert.equal(active, 1, 'restore/write transactions must never overlap');
      const finish = err => { --active; callback(err); };
      if (populate) restore = finish;
      else write = finish;
    },
  };
  const context = vm.createContext({ window, document, FS, IDBFS: {},
    user_dir: mountPoint, UTF8ToString: s => s,
    console: { log() {}, error() {} },
    setTimeout(fn, ms) { timers.set(++nextTimer, { fn, ms }); return nextTimer; },
    clearTimeout(id) { timers.delete(id); },
    requestAnimationFrame() { throw new Error('Persistence must not depend on rendering'); },
  });
  const mounted = vm.runInContext('(async function() {\n' + code + '\n})()', context);
  return {
    window, document, timers, notices, errors, requests, mounted,
    dirty() {
      window.setFsNeedsSync();
      assert.equal(window.cdda_persistence_pending, true);
    },
    async restore(err = null) { const f = restore; restore = null; f(err); await mounted; },
    finish(err = null) {
      assert(write); const f = write; write = null; f(err);
      if (err) assert.equal(window.cdda_persistence_pending, true);
    },
    tick(ms) {
      assert.equal(timers.size, 1, 'at most one scheduled synchronization');
      const [id, timer] = timers.entries().next().value;
      assert.equal(timer.ms, ms);
      timers.delete(id); timer.fn();
    },
    event(name, state = 'hidden') { document.visibilityState = state; events.get(name)(); },
    throwOnWrite() { throwNext = true; },
    writes() { return requests.filter(p => !p).length; },
    maxActive() { return maxActive; },
  };
}

// Execute the real reconcile replacement against a deterministic transaction
// adapter. The browser companion tests the same code with actual IndexedDB.
function reconcileFixture(fault = '') {
  const tasks = [], written = [], disk = new Map([['/old', 'old'], ['/same', 'same']]);
  const before = [...disk], stamp = n => ({ timestamp: new Date(n) });
  const src = { type: 'local', entries: { '/same': stamp(1) } };
  for (let i = 0; i < 1000; ++i) src.entries['/new/' + String(i).padStart(4, '0')] = stamp(2);
  const dst = { type: 'remote', entries: { '/old': stamp(1), '/same': stamp(1) } };
  let pending = 0, peak = 0, callbacks = 0, abortDelivered = false, aborting = false;
  let finalError, tx, delegated = 0;
  const staged = new Map(disk);
  function request(kind, key, entry) {
    if (fault === 'put-throw' && written.length === 3) throw new Error(fault);
    const req = { error: new Error('request error') };
    ++pending; peak = Math.max(peak, pending); written.push([kind, key]);
    tasks.push(() => {
      --pending;
      if (aborting) return;
      if (fault === 'request-error' && written.length === 3 || fault === 'delete-error' && kind === 'delete') {
        const event = { target: req, preventDefault() {} };
        req.onerror(event); tx.onerror(event); // bubbles; must not abort/complete twice
      } else {
        if (kind === 'put') staged.set(key, entry);
        else staged.delete(key);
        req.onsuccess();
        if (!pending && !aborting) tasks.push(() => {
          disk.clear(); for (const [k, v] of staged) disk.set(k, v);
          tx.oncomplete();
        });
      }
    });
    return req;
  }
  dst.db = { transaction() {
    if (fault === 'transaction-throw') throw new Error(fault);
    tx = {
      objectStore() {
        if (fault === 'store-throw') throw new Error(fault);
        return { put: (entry, key) => request('put', key, entry), delete: key => request('delete', key) };
      },
      abort() {
        if (aborting) throw new Error('already aborting');
        aborting = true;
        tasks.push(() => { abortDelivered = true; tx.onabort(); });
      },
    };
    return tx;
  } };
  const IDBFS = {
    DB_STORE_NAME: 'FILE_DATA',
    reconcile() { ++delegated; },
    loadLocalEntry(key, cb) {
      if (fault === 'load-throw' && written.length === 3) throw new Error(fault);
      if (fault === 'load-error' && written.length === 3) return cb(new Error(fault));
      cb(null, key);
    },
  };
  const replacement = code.slice(code.indexOf('    const originalReconcile'), code.indexOf('    let fsNeedsSync'));
  assert(replacement.includes('IDBFS.reconcile = function'));
  vm.runInNewContext(replacement, { IDBFS });
  IDBFS.reconcile({ type: 'remote' }, { type: 'local' }, () => {});
  assert.equal(delegated, 1, 'restore must retain the original implementation');
  IDBFS.reconcile(src, dst, err => {
    ++callbacks; finalError = err;
    if (aborting) assert(abortDelivered, 'must wait for transaction abort before retry');
  });
  assert.equal(pending, fault === 'transaction-throw' || fault === 'store-throw' ? 0 : 1);
  let steps = 0;
  while (tasks.length) { assert(++steps < 2000); tasks.shift()(); }
  assert.equal(callbacks, 1); assert(peak <= 1); assert.equal(pending, 0);
  if (fault) {
    assert(finalError); assert.deepEqual([...disk], before, 'failed transaction must not commit partial files');
  } else {
    assert.equal(finalError, null); assert.equal(disk.size, 1001);
    assert(!disk.has('/old')); assert.equal(disk.get('/same'), 'same');
    assert.equal(written.length, 1001);
    assert.deepEqual(written.at(-1), ['delete', '/old']);
    assert.deepEqual(written.slice(0, 1000).map(w => w[1]), Object.keys(src.entries).filter(k => k !== '/same').sort());
    // A second identical reconcile must not open an empty transaction.
    let emptyCalls = 0;
    IDBFS.reconcile(src, { type: 'remote', entries: src.entries }, err => { assert.equal(err, null); ++emptyCalls; });
    assert.equal(emptyCalls, 1);
  }
}

const cases = [
  ['bounded reconcile keeps order, unchanged files, one transaction and restore fallback', async () => {
    reconcileFixture();
  }],
  ['reconcile aborts once and rolls back on request, load, put, delete or setup failure', async () => {
    for (const fault of ['put-throw', 'request-error', 'delete-error', 'load-throw', 'load-error', 'transaction-throw', 'store-throw']) {
      reconcileFixture(fault);
    }
  }],
  ['long nested save batches cancel timers and defer lifecycle flushes', async () => {
    const e = environment(); await e.restore(); e.dirty();
    assert.equal(e.timers.size, 1);
    e.window.beginFsSyncBatch(); assert.equal(e.timers.size, 0);
    e.window.beginFsSyncBatch();
    // Each burst would allow another full-tree scan without a save boundary.
    for (let i = 0; i < 100; ++i) {
      e.dirty(); e.event('visibilitychange'); e.event('pagehide');
      assert.equal(e.writes(), 0); assert.equal(e.timers.size, 0);
    }
    e.window.endFsSyncBatch(); assert.equal(e.timers.size, 0);
    e.window.endFsSyncBatch(); e.tick(250); e.finish();
    assert.equal(e.writes(), 1); assert.equal(e.timers.size, 0);
    // Empty or unmatched end calls must not underflow or create idle writes.
    e.window.endFsSyncBatch(); e.window.beginFsSyncBatch(); e.window.endFsSyncBatch();
    assert.equal(e.timers.size, 0);
    e.dirty(); e.tick(250); e.finish(); assert.equal(e.writes(), 2);
  }],
  ['in-flight completion during a batch retains changes and retry backoff', async () => {
    for (const fail of [false, true]) {
      const e = environment(); await e.restore(); e.dirty(); e.tick(250);
      e.window.beginFsSyncBatch(); e.dirty();
      e.finish(fail ? new Error('quota') : null);
      assert.equal(e.timers.size, 0); assert.equal(e.writes(), 1);
      e.event('visibilitychange', 'visible'); assert.equal(e.writes(), 1);
      e.window.endFsSyncBatch(); e.tick(fail ? 1000 : 250); e.finish();
      assert.equal(e.writes(), 2); assert.equal(e.maxActive(), 1);
      assert.equal(e.timers.size, 0);
      if (fail) assert.equal(e.notices.at(-1).failed, false);
    }
  }],
  ['batch exit before in-flight completion still schedules final dirty pass', async () => {
    const e = environment(); await e.restore(); e.dirty(); e.tick(250);
    e.window.beginFsSyncBatch(); e.dirty(); e.window.endFsSyncBatch();
    assert.equal(e.timers.size, 0);
    e.finish(); e.tick(250); e.finish();
    assert.equal(e.writes(), 2); assert.equal(e.maxActive(), 1);
  }],
  ['restore and restore failure remain safe inside a batch', async () => {
    for (const fail of [false, true]) {
      const e = environment(); e.window.beginFsSyncBatch(); e.dirty();
      await e.restore(fail ? new Error('restore') : null);
      assert.equal(e.timers.size, 0); assert.equal(e.writes(), 0);
      e.window.endFsSyncBatch();
      if (fail) {
        e.event('pagehide'); assert.equal(e.writes(), 0); assert.equal(e.timers.size, 0);
      } else {
        e.tick(250); e.finish(); assert.equal(e.writes(), 1);
      }
    }
  }],
  ['profile mount, no idle write, pre-restore mutations', async () => {
    const e = environment();
    assert.equal(typeof e.window.setFsNeedsSync, 'function');
    for (let i = 0; i < 1000; ++i) e.dirty();
    e.event('pagehide'); assert.equal(e.writes(), 0);
    assert.equal(e.timers.size, 0);
    await e.restore(); e.tick(250); e.finish();
    assert.equal(e.writes(), 1); assert.equal(e.timers.size, 0);
    e.event('visibilitychange', 'visible'); assert.equal(e.writes(), 1);
    const idle = environment(); await idle.restore(); assert.equal(idle.timers.size, 0);
  }],
  ['burst coalescing, no rendering dependency', async () => {
    const e = environment(); await e.restore();
    for (let i = 0; i < 1000; ++i) e.dirty();
    e.tick(250); e.finish();
    assert.equal(e.writes(), 1); assert.equal(e.timers.size, 0);
  }],
  ['writes during sync are serialized into one subsequent pass', async () => {
    const e = environment(); await e.restore(); e.dirty(); e.tick(250);
    for (let i = 0; i < 1000; ++i) e.dirty();
    e.event('pagehide'); assert.equal(e.writes(), 1); assert.equal(e.timers.size, 0);
    e.finish(); e.tick(250); e.finish();
    assert.equal(e.writes(), 2); assert.equal(e.maxActive(), 1);
  }],
  ['callback failure retries without another mutation, backoff capped', async () => {
    const e = environment(); await e.restore(); e.dirty(); e.tick(250);
    for (const ms of [1000, 2000, 4000, 8000, 16000, 30000, 30000]) {
      e.finish(new Error('quota')); e.tick(ms);
    }
    assert.equal(e.notices.length, 1, 'do not spam failure notices');
    e.finish(); assert.equal(e.notices.length, 2); assert.equal(e.notices[1].failed, false);
    e.dirty(); e.tick(250); e.finish(); assert.equal(e.timers.size, 0);
  }],
  ['synchronous exception clears in-flight guard and retries', async () => {
    const e = environment(); await e.restore(); e.throwOnWrite(); e.dirty(); e.tick(250);
    assert.equal(e.notices[0].failed, true);
    e.tick(1000); e.finish(); assert.equal(e.notices[1].failed, false);
  }],
  ['error warning remains until concurrent changes are persisted', async () => {
    const e = environment(); await e.restore(); e.dirty(); e.tick(250);
    e.finish(new Error('quota')); e.tick(1000); e.dirty(); e.finish();
    assert.equal(e.notices.length, 1);
    e.tick(250); e.finish(); assert.equal(e.notices[1].failed, false);
  }],
  ['hidden tab flush and subsequent pass never wait for animation', async () => {
    const e = environment(); await e.restore(); e.dirty(); e.event('visibilitychange');
    assert.equal(e.timers.size, 0); assert.equal(e.writes(), 1);
    e.dirty(); e.finish(); e.tick(250); e.finish();
    assert.equal(e.writes(), 2); assert.equal(e.timers.size, 0);
  }],
  ['pagehide and tab return flush one pending pass', async () => {
    const e = environment(); await e.restore(); e.dirty(); e.event('pagehide');
    e.event('visibilitychange', 'visible'); assert.equal(e.writes(), 1);
    e.finish(new Error('temporary'));
    e.event('visibilitychange', 'visible'); assert.equal(e.timers.size, 0);
    assert.equal(e.writes(), 2); e.finish();
  }],
  ['failed restore never writes an empty in-memory tree over saves', async () => {
    const e = environment(); e.dirty(); await e.restore(new Error('restore'));
    e.dirty(); e.event('pagehide'); assert.equal(e.writes(), 0);
    assert.equal(e.errors.length, 1); assert.equal(e.timers.size, 0);
    for (const opts of [{ mountThrows: true }, { restoreThrows: true }]) {
      const other = environment(opts); await other.mounted; other.dirty();
      assert.equal(other.errors.length, 1); assert.equal(other.writes(), 0);
    }
  }],
  ['missing or throwing UI hooks never break retry', async () => {
    for (const opts of [{ noHooks: true }, { hookThrows: true }]) {
      const e = environment(opts); await e.restore(); e.dirty(); e.tick(250);
      e.finish(new Error('quota')); e.tick(1000); e.finish();
      assert.equal(e.timers.size, 0);
      const failed = environment(opts); await failed.restore(new Error('restore'));
      assert.equal(failed.timers.size, 0);
    }
  }],
  ['post-restore migration writes only when requested', async () => {
    const e = environment({ migration: true }); await e.restore(); e.tick(250); e.finish();
    assert.equal(e.writes(), 1);
    const bad = environment({ migrationThrows: true }); await bad.restore();
    bad.dirty(); bad.tick(250); bad.finish(); assert.equal(bad.writes(), 1);
  }],
  ['actual shell keeps failure visible and clears it only on recovery', async () => {
    const html = fs.readFileSync(path.join(__dirname, '../shell/index.html'), 'utf8');
    assert(html.includes('id="save-warning" role="status" aria-live="polite" hidden'));
    const begin = html.indexOf('      const saveWarning =');
    const finish = html.indexOf('      /* ---- Mod installer:', begin);
    assert(begin >= 0 && finish > begin);
    const warning = { hidden: true, textContent: '' }, window = {};
    vm.runInNewContext(html.slice(begin, finish), { window,
      document: { getElementById: () => warning }, console: { warn() {} }, noticeMessage() {} });
    window.CDDA_ON_IDBFS_SYNC_STATE(true, '<script>unsafe</script>');
    assert.equal(warning.hidden, false);
    assert(warning.textContent.includes('Export Saves'));
    assert(!warning.textContent.includes('<script>'));
    window.CDDA_ON_IDBFS_SYNC_STATE(false, '');
    assert.equal(warning.hidden, true); assert.equal(warning.textContent, '');
    window.CDDA_ON_IDBFS_ERROR('mount failure');
    assert.equal(warning.hidden, false); assert(warning.textContent.includes('Export Saves'));
  }],
];
(async () => {
  let failures = 0;
  for (const [name, run] of cases) {
    try { await run(); console.log('PASS IDBFS:', name); }
    catch (err) { ++failures; console.error('FAIL IDBFS:', name, err.message); }
  }
  console.log(`${cases.length - failures}/${cases.length} IDBFS control-flow scenarios passed`);
  if (failures) process.exitCode = 1;
})().catch(err => { console.error(err); process.exitCode = 1; });
