// Verify storage-protection denial remains visible without masking actual sync errors.
const assert = require('node:assert/strict');
const fsNode = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fsNode.readFileSync(path.join(__dirname, '../shell/index.html'), 'utf8');
const start = html.indexOf('      let cddaFilesystemReady = false;');
const end = html.indexOf('      /* ---- Mod installer:', start);
assert(start >= 0 && end > start, 'find the actual shell persistence warning logic');
const source = html.slice(start, end);

function environment(persist = () => Promise.resolve(true)) {
  const warning = { hidden: true, textContent: '' };
  const notices = [];
  const window = { Module: { FS: {} } };
  const context = vm.createContext({
    window, Module: window.Module, FS: {},
    navigator: { storage: { persist } },
    document: { getElementById(id) {
      assert.equal(id, 'save-warning');
      return warning;
    } },
    rendererMode: { value: 'game' },
    migrateJapaneseDefault: () => false,
    applyWebRenderer: () => false,
    noticeMessage(message) { notices.push(message); },
    console: { warn() {}, error() {} },
  });
  vm.runInContext(source, context);
  return { context, window, warning, notices };
}

(async () => {
  {
    const e = environment(() => Promise.resolve(false));
    await e.context.requestStorageProtection();
    assert.equal(e.warning.hidden, false, 'a denied persistence request needs a persistent warning');
    assert(e.warning.textContent.includes('Persistent-storage protection was not granted'));
    assert(e.warning.textContent.includes('Export Saves'));

    e.window.CDDA_ON_IDBFS_SYNC_STATE(false, '');
    assert.equal(e.warning.hidden, false,
      'a successful sync must not hide the independent eviction-risk warning');
    assert(e.warning.textContent.includes('Persistent-storage protection was not granted'));

    e.window.CDDA_ON_IDBFS_SYNC_STATE(true, 'quota');
    assert(e.warning.textContent.includes('Save sync failed'));
    assert(e.warning.textContent.includes('Persistent-storage protection was not granted'),
      'both independent durability problems must remain visible');

    e.window.CDDA_ON_STORAGE_PERSIST(true);
    assert.equal(e.warning.hidden, false, 'a sync failure remains visible after protection is granted');
    assert(e.warning.textContent.includes('Save sync failed'));
    assert(!e.warning.textContent.includes('Persistent-storage protection was not granted'));

    e.window.CDDA_ON_IDBFS_SYNC_STATE(false, '');
    assert.equal(e.warning.hidden, true, 'clear the warning after protection is granted and sync recovers');
    console.log('PASS storage warning: denial, sync failure/recovery, and independent warning lifetimes');
  }

  {
    const e = environment(() => Promise.reject(new Error('permission request rejected')));
    await e.context.requestStorageProtection();
    assert.equal(e.warning.hidden, false);
    assert(e.warning.textContent.includes('Persistent-storage protection was not granted'));
    console.log('PASS storage warning: rejected persistence request is handled and visible');
  }

  {
    const e = environment(() => { throw new Error('permission request threw'); });
    await e.context.requestStorageProtection();
    assert.equal(e.warning.hidden, false);
    assert(e.warning.textContent.includes('Persistent-storage protection was not granted'));
    console.log('PASS storage warning: synchronous persistence-request exception is handled');
  }

  {
    const e = environment();
    e.window.CDDA_ON_IDBFS_ERROR('indexeddb unavailable');
    assert.equal(e.warning.hidden, false);
    assert(e.warning.textContent.includes('Browser persistence unavailable'));
    assert(e.notices.some(message => /cannot be persisted|セーブ/.test(message)));
    e.window.CDDA_ON_STORAGE_PERSIST(true);
    e.window.CDDA_ON_IDBFS_SYNC_STATE(false, '');
    assert.equal(e.warning.hidden, false,
      'a successful protection request must not mask a failed IndexedDB restore');
    assert(e.warning.textContent.includes('Browser persistence unavailable'));
    console.log('PASS storage warning: volatile fallback warning cannot be hidden by later success callbacks');
  }

  {
    const e = environment(() => Promise.resolve(true));
    await e.context.requestStorageProtection();
    assert.equal(e.warning.hidden, true, 'granted protection with healthy storage shows no warning');
    console.log('PASS storage warning: no false positive when browser grants protection');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
