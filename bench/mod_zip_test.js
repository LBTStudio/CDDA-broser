// Exercise the actual MOD ZIP handler from shell/index.html with a POSIX-like MEMFS adapter.
const assert = require('node:assert/strict');
const fsNode = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fsNode.readFileSync(path.join(__dirname, '../shell/index.html'), 'utf8');
const start = html.indexOf('document.getElementById("mod-zip-input").addEventListener("change", async (ev) => {');
const end = html.indexOf('/* ---- Japanese (IME) input bridge', start);
assert(start >= 0 && end > start, 'find actual MOD installer handler');
const source = html.slice(start, end);

const root = '/home/audit/.cataclysm-dda';
const mods = root + '/mods';
function makeZip(entries) {
  return {
    forEach(callback) {
      for (const item of entries) {
        callback(item.path, {
          dir: !!item.dir,
          async: async () => {
            if (item.error) throw new Error('injected decompression failure');
            return new TextEncoder().encode(item.content || '').buffer;
          },
        });
      }
    },
  };
}

function makeScenario({ entries, fileName = 'bundle.zip', initialFiles = [], failInstallRename = '' }) {
  const dirs = new Set();
  const files = new Map();
  const operations = [];
  let failRename = failInstallRename;
  function normalize(value) {
    const parts = [];
    for (const part of value.split('/')) {
      if (!part || part === '.') continue;
      if (part === '..') parts.pop();
      else parts.push(part);
    }
    return '/' + parts.join('/');
  }
  function ensureDir(target) {
    const normalized = normalize(target);
    const parts = normalized.split('/').filter(Boolean);
    let current = '';
    for (const part of parts) {
      current += '/' + part;
      if (files.has(current)) throw new Error('file blocks directory creation: ' + current);
      dirs.add(current);
    }
  }
  ensureDir(root);
  ensureDir(mods);
  for (const [name, content] of initialFiles) {
    const target = normalize(name);
    ensureDir(target.slice(0, target.lastIndexOf('/')));
    files.set(target, content);
  }
  const fakeFs = {
    analyzePath(target) {
      const normalized = normalize(target);
      return { exists: dirs.has(normalized) || files.has(normalized) };
    },
    mkdirTree(target) {
      const normalized = normalize(target);
      ensureDir(normalized);
      operations.push(['mkdirTree', normalized]);
    },
    writeFile(target, value) {
      const normalized = normalize(target);
      if (dirs.has(normalized)) throw new Error('cannot overwrite directory: ' + normalized);
      const parent = normalized.slice(0, normalized.lastIndexOf('/'));
      if (!dirs.has(parent)) throw new Error('missing parent: ' + parent);
      files.set(normalized, new TextDecoder().decode(value));
      operations.push(['writeFile', normalized]);
    },
    readFile(target, options = {}) {
      const normalized = normalize(target);
      if (!files.has(normalized)) throw new Error('missing file: ' + normalized);
      if (options.encoding === 'utf8' || options.encoding === 'utf-8') return files.get(normalized);
      return new TextEncoder().encode(files.get(normalized));
    },
    readdir(target) {
      const normalized = normalize(target);
      if (!dirs.has(normalized)) throw new Error('not a directory: ' + normalized);
      const prefix = normalized === '/' ? '/' : normalized + '/';
      const children = new Set(['.', '..']);
      for (const candidate of dirs) {
        if (candidate === normalized || !candidate.startsWith(prefix)) continue;
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
      const normalized = normalize(target);
      if (dirs.has(normalized)) return { mode: 0x4000 };
      if (files.has(normalized)) return { mode: 0x8000 };
      throw new Error('missing path: ' + normalized);
    },
    isDir(mode) { return mode === 0x4000; },
    isFile(mode) { return mode === 0x8000; },
    unlink(target) {
      const normalized = normalize(target);
      if (!files.delete(normalized)) throw new Error('missing file: ' + normalized);
      operations.push(['unlink', normalized]);
    },
    rmdir(target) {
      const normalized = normalize(target);
      if (this.readdir(normalized).some(name => name !== '.' && name !== '..')) {
        throw new Error('directory not empty: ' + normalized);
      }
      if (!dirs.delete(normalized)) throw new Error('missing directory: ' + normalized);
      operations.push(['rmdir', normalized]);
    },
    rename(from, to) {
      const sourcePath = normalize(from), targetPath = normalize(to);
      if (failRename && targetPath === normalize(failRename) &&
          sourcePath.includes('/.mod-installer-staging/')) {
        failRename = '';
        throw new Error('injected directory swap failure');
      }
      if (!dirs.has(sourcePath) && !files.has(sourcePath)) throw new Error('missing rename source: ' + sourcePath);
      if (dirs.has(targetPath) || files.has(targetPath)) throw new Error('rename target exists: ' + targetPath);
      const dirMoves = [...dirs].filter(p => p === sourcePath || p.startsWith(sourcePath + '/'));
      const fileMoves = [...files.entries()].filter(([p]) => p === sourcePath || p.startsWith(sourcePath + '/'));
      for (const p of dirMoves) dirs.delete(p);
      for (const [p] of fileMoves) files.delete(p);
      for (const p of dirMoves) dirs.add(targetPath + p.slice(sourcePath.length));
      for (const [p, content] of fileMoves) files.set(targetPath + p.slice(sourcePath.length), content);
      operations.push(['rename', sourcePath, targetPath]);
    },
  };

  const input = { addEventListener(name, callback) { assert.equal(name, 'change'); input.handler = callback; } };
  const notices = [];
  let persistCalls = 0;
  const gameModule = { FS: fakeFs };
  const context = {
    document: { getElementById(id) { assert.equal(id, 'mod-zip-input'); return input; } },
    window: { CDDA_PROFILE: 'audit', Module: gameModule },
    Module: gameModule,
    JSZip: { loadAsync: async () => makeZip(entries) },
    booted: true,
    fsReady: () => true,
    modRoot: () => mods,
    requestPersist: () => { persistCalls++; },
    noticeMessage: message => notices.push(message),
    removeDirRecursive(fs, target) {
      for (const name of fs.readdir(target)) {
        if (name === '.' || name === '..') continue;
        const child = target + '/' + name;
        if (fs.isDir(fs.stat(child).mode)) context.removeDirRecursive(fs, child);
        else fs.unlink(child);
      }
      fs.rmdir(target);
    },
    confirm: () => true,
    console: { warn() {}, error() {} },
  };
  vm.runInNewContext(source, context);
  return {
    input, dirs, files, operations, notices, root, mods,
    persistCalls: () => persistCalls,
    async install() {
      const file = { name: fileName, arrayBuffer: async () => new ArrayBuffer(0) };
      await input.handler({ target: { files: [file], value: fileName } });
    },
  };
}

(async () => {
  const entries = [
    { path: 'pack/main/modinfo.json', content: '[{"type":"MOD_INFO","id":"main"}]' },
    { path: 'pack/main/data.json', content: '{"main":true}' },
    { path: 'pack/main/assets/icon.json', content: '{"icon":true}' },
    { path: 'pack/main/sub/modinfo.json', content: '[{"type":"MOD_INFO","id":"sub"}]' },
    { path: 'pack/main/sub/child.json', content: '{"child":true}' },
    { path: 'pack/main/sub/deep/asset.json', content: '{"deep":true}' },
  ];
  {
    const e = makeScenario({ entries });
    await e.install();
    const parent = e.mods + '/main/';
    const child = e.mods + '/sub/';
    assert.equal(e.files.get(parent + 'modinfo.json'), entries[0].content);
    assert.equal(e.files.get(parent + 'data.json'), entries[1].content);
    assert.equal(e.files.get(parent + 'assets/icon.json'), entries[2].content);
    assert.equal(e.files.has(parent + 'sub/modinfo.json'), false, 'parent must not duplicate nested MOD_INFO root');
    assert.equal(e.files.has(parent + 'sub/child.json'), false, 'parent must not duplicate nested MOD payload');
    assert.equal(e.files.get(child + 'modinfo.json'), entries[3].content);
    assert.equal(e.files.get(child + 'child.json'), entries[4].content);
    assert.equal(e.files.get(child + 'deep/asset.json'), entries[5].content);
    assert.equal(e.persistCalls(), 1);
    assert(e.notices.some(message => message.includes('Installed')));
    assert(![...e.files.keys()].some(p => p.includes('/.mod-installer-staging/')));
    console.log('PASS MOD ZIP: nested MOD roots install independently without duplicate payload');
  }

  for (const testCase of [
    { name: 'parent traversal', fileName: 'evil.zip', entries: [{ path: '../modinfo.json', content: '[{"type":"MOD_INFO","id":"evil"}]' }] },
    { name: 'dot target from archive name', fileName: '..zip', entries: [{ path: 'modinfo.json', content: '[{"type":"MOD_INFO","id":"evil"}]' }] },
    { name: 'duplicate canonical paths', fileName: 'evil.zip', entries: [
      { path: './modinfo.json', content: '[{"type":"MOD_INFO","id":"evil"}]' },
      { path: 'modinfo.json', content: '[{"type":"MOD_INFO","id":"other"}]' },
    ] },
  ]) {
    const sentinel = e => [[e.root + '/world.sav', 'preserve-me']];
    const e = makeScenario({ ...testCase, initialFiles: sentinel({ root }) });
    const before = [...e.files.entries()].sort();
    await e.install();
    assert.equal(e.files.get(root + '/world.sav'), 'preserve-me', testCase.name + ': save must remain untouched');
    assert.deepEqual([...e.files.entries()].sort(), before, testCase.name + ': unsafe ZIP must not mutate FS');
    assert.equal(e.operations.length, 0, testCase.name + ': no filesystem mutation before rejection');
    assert.equal(e.persistCalls(), 0);
    assert(e.notices.some(message => /unsafe|安全|重複|duplicate|directory name/i.test(message)), testCase.name + ': show a safe rejection message');
    console.log('PASS MOD ZIP safety: ' + testCase.name);
  }

  {
    const oldInfo = '[{"type":"MOD_INFO","id":"main"}]';
    const e = makeScenario({
      entries: [
        { path: 'main/modinfo.json', content: '[{"type":"MOD_INFO","id":"main-new"}]' },
        { path: 'main/data.json', error: true },
      ],
      initialFiles: [[mods + '/main/modinfo.json', oldInfo], [mods + '/main/old.txt', 'old save-compatible mod file']],
    });
    await e.install();
    assert.equal(e.files.get(mods + '/main/modinfo.json'), oldInfo, 'failed extraction must preserve the old MOD');
    assert.equal(e.files.get(mods + '/main/old.txt'), 'old save-compatible mod file');
    assert(![...e.files.keys()].some(p => p.includes('/.mod-installer-staging/')), 'failed extraction staging must be cleaned');
    assert(e.persistCalls() === 1, 'cleanup of staged files must be persisted');
    assert(e.notices.some(message => /failed|失敗/i.test(message)));
    console.log('PASS MOD ZIP atomic overwrite: extraction failure preserves previous MOD');
  }

  {
    const oldInfo = '[{"type":"MOD_INFO","id":"main"}]';
    const target = mods + '/main';
    const e = makeScenario({
      entries: [
        { path: 'main/modinfo.json', content: '[{"type":"MOD_INFO","id":"main-new"}]' },
        { path: 'main/data.json', content: 'new' },
      ],
      fileName: 'single.zip',
      failInstallRename: target,
      initialFiles: [[target + '/modinfo.json', oldInfo], [target + '/old.txt', 'old version']],
    });
    await e.install();
    assert.equal(e.files.get(target + '/modinfo.json'), oldInfo, 'failed atomic swap must restore old MOD');
    assert.equal(e.files.get(target + '/old.txt'), 'old version');
    assert(![...e.files.keys()].some(p => p.includes('/.mod-installer-staging/previous')), 'successful rollback must remove backup');
    assert(![...e.files.keys()].some(p => p.includes('/.mod-installer-staging/new')), 'failed staging must be cleaned');
    assert.equal(e.persistCalls(), 1);
    assert(e.notices.some(message => /failed|失敗/i.test(message)));
    console.log('PASS MOD ZIP atomic overwrite: failed directory swap restores prior MOD');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
