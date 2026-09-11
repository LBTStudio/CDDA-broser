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
    dirty() { window.setFsNeedsSync(); },
    async restore(err = null) { const f = restore; restore = null; f(err); await mounted; },
    finish(err = null) { assert(write); const f = write; write = null; f(err); },
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

const cases = [
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
