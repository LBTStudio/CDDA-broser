// Exercise the actual Save Export handler and verify ZIP entry paths are portable.
const assert = require('node:assert/strict');
const fsNode = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fsNode.readFileSync(path.join(__dirname, '../shell/index.html'), 'utf8');
const start = html.indexOf('document.getElementById("export-saves").onclick = async () => {');
const end = html.indexOf('/* Migrate the prior English default to Japanese exactly once per', start);
assert(start >= 0 && end > start, 'find the actual save export handler');
const source = html.slice(start, end);

const root = '/home/test-player/.cataclysm-dda';
const dirs = new Set([
  root,
  root + '/config',
  root + '/worlds',
  root + '/worlds/demo-world',
]);
const files = new Map([
  [root + '/config/options.json', '[{"name":"USE_LANG","value":"ja"}]'],
  [root + '/worlds/demo-world/master.gsav', 'game-save'],
]);
const fakeFs = {
  readdir(target) {
    assert(dirs.has(target), 'readdir existing directory: ' + target);
    const prefix = target === '/' ? '/' : target + '/';
    const children = new Set(['.', '..']);
    for (const candidate of dirs) {
      if (candidate === target || !candidate.startsWith(prefix)) continue;
      const rest = candidate.slice(prefix.length);
      if (rest && !rest.includes('/')) children.add(rest);
    }
    for (const candidate of files.keys()) {
      if (!candidate.startsWith(prefix)) continue;
      const rest = candidate.slice(prefix.length);
      if (rest && !rest.includes('/')) children.add(rest);
    }
    return [...children];
  },
  stat(target) {
    if (dirs.has(target)) return { mode: 0x4000 };
    if (files.has(target)) return { mode: 0x8000 };
    throw new Error('missing path: ' + target);
  },
  isDir(mode) { return mode === 0x4000; },
  isFile(mode) { return mode === 0x8000; },
  readFile(target) {
    if (!files.has(target)) throw new Error('missing file: ' + target);
    return new TextEncoder().encode(files.get(target));
  },
};

const zipPaths = [];
class FakeZip {
  file(name, _contents) {
    zipPaths.push(name);
    return this;
  }
  async generateAsync(options) {
    assert.equal(options.type, 'blob');
    return { entries: zipPaths.slice() };
  }
}
const button = {};
let savedAs = null;
const notices = [];
const context = {
  document: { getElementById(id) { assert.equal(id, 'export-saves'); return button; } },
  window: { CDDA_PROFILE: 'test-player' },
  Module: { FS: fakeFs },
  FS: fakeFs,
  JSZip: FakeZip,
  saveAs(blob, filename) { savedAs = { blob, filename }; },
  fsReady: () => true,
  ensureUtilityLibrary: async () => {},
  noticeMessage(message) { notices.push(message); },
  console: { warn() {}, error() {} },
};
vm.runInNewContext(source, context);

(async () => {
  await button.onclick();
  assert(savedAs, 'save export should call saveAs');
  assert.equal(savedAs.filename, 'saves-test-player.zip');
  assert.deepEqual(zipPaths.sort(), [
    'config/options.json',
    'worlds/demo-world/master.gsav',
  ]);
  assert(zipPaths.every(name => !name.startsWith('/')), 'ZIP entry paths must be relative');
  assert(zipPaths.every(name => !name.split('/').includes('..')), 'ZIP entries must not contain traversal components');
  assert.equal(notices.length, 0, 'successful export should not show an error');
  console.log('PASS save export: relative paths preserved for config and world files');
})().catch(error => { console.error(error); process.exitCode = 1; });
