// Exercise the actual MOD ZIP handler from shell/index.html with a POSIX-like MEMFS adapter.
const assert = require('node:assert/strict');
const fsNode = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const RealJSZip = require(path.join(__dirname, '../shell/vendor/jszip-3.10.1.min.js'));

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
          _data: {
            uncompressedSize: Number.isSafeInteger(item.uncompressedSize)
              ? item.uncompressedSize
              : new TextEncoder().encode(item.content || '').byteLength,
          },
          internalStream(type) {
            assert.equal(type, 'uint8array');
            const handlers = {};
            let paused = false;
            const stream = {
              on(name, callback) { handlers[name] = callback; return stream; },
              pause() { paused = true; return stream; },
              resume() {
                if (item.error) {
                  if (handlers.error) handlers.error(new Error('injected decompression failure'));
                  return stream;
                }
                const encoded = new TextEncoder().encode(item.content || '');
                const length = Number.isSafeInteger(item.actualLength) ? item.actualLength : encoded.byteLength;
                for (let offset = 0; offset < length && !paused; offset += 1024 * 1024) {
                  const chunkSize = Math.min(1024 * 1024, length - offset);
                  const chunk = Number.isSafeInteger(item.actualLength)
                    ? new Uint8Array(chunkSize)
                    : encoded.subarray(offset, offset + chunkSize);
                  if (handlers.data) handlers.data(chunk);
                }
                if (!paused && handlers.end) handlers.end();
                return stream;
              },
            };
            return stream;
          },
          async: async () => {
            if (item.error) throw new Error('injected decompression failure');
            return new TextEncoder().encode(item.content || '').buffer;
          },
        });
      }
    },
  };
}

function makeScenario({ entries, fileName = 'bundle.zip', fileSize = 1024,
                      initialFiles = [], failInstallRename = '', zipBytes = null }) {
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
  let arrayBufferReads = 0;
  let loadAsyncCalls = 0;
  const gameModule = { FS: fakeFs };
  const context = {
    document: { getElementById(id) { assert.equal(id, 'mod-zip-input'); return input; } },
    window: { CDDA_PROFILE: 'audit', Module: gameModule },
    Module: gameModule,
    JSZip: { loadAsync: async buffer => {
      loadAsyncCalls++;
      return zipBytes ? RealJSZip.loadAsync(buffer) : makeZip(entries);
    } },
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
    arrayBufferReads: () => arrayBufferReads,
    loadAsyncCalls: () => loadAsyncCalls,
    async install() {
      const emptyZip = new Uint8Array(22);
      emptyZip.set([0x50, 0x4b, 0x05, 0x06]);
      const file = {
        name: fileName,
        size: fileSize,
        arrayBuffer: async () => {
          arrayBufferReads++;
          return zipBytes
            ? zipBytes.buffer.slice(zipBytes.byteOffset, zipBytes.byteOffset + zipBytes.byteLength)
            : emptyZip.buffer;
        },
      };
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

  // Archive and expansion limits are checked before any MOD directory is written.
  {
    const e = makeScenario({
      fileName: 'too-large.zip',
      fileSize: 128 * 1024 * 1024 + 1,
      entries: [{ path: 'main/modinfo.json', content: '[{"type":"MOD_INFO","id":"main"}]' }],
    });
    await e.install();
    assert.equal(e.arrayBufferReads(), 0, 'oversized archive must be rejected before allocating its bytes');
    assert.equal(e.operations.length, 0);
    assert.equal(e.persistCalls(), 0);
    assert(e.notices.some(message => message.includes('128 MiB')));
    console.log('PASS MOD ZIP resource limits: oversized archive rejected before reading bytes');
  }

  {
    const entries = [
      { path: 'main/modinfo.json', content: '[{"type":"MOD_INFO","id":"main"}]' },
      { path: 'main/huge.bin', content: 'small fixture', uncompressedSize: 64 * 1024 * 1024 + 1 },
    ];
    const e = makeScenario({ entries });
    await e.install();
    assert.equal(e.operations.length, 0, 'oversized entry must be rejected before writing staging files');
    assert.equal(e.persistCalls(), 0);
    assert(e.notices.some(message => message.includes('64 MiB')));
    console.log('PASS MOD ZIP resource limits: oversized expanded entry rejected before extraction');
  }

  {
    const entries = [
      { path: 'main/modinfo.json', content: '[{"type":"MOD_INFO","id":"main"}]' },
      ...Array.from({ length: 5 }, (_, i) => ({
        path: 'main/large-' + i + '.bin',
        content: 'small fixture',
        uncompressedSize: 60 * 1024 * 1024,
      })),
    ];
    const e = makeScenario({ entries });
    await e.install();
    assert.equal(e.operations.length, 0, 'aggregate declared expansion over limit must be rejected before filesystem changes');
    assert.equal(e.persistCalls(), 0);
    assert(e.notices.some(message => message.includes('256 MiB')));
    console.log('PASS MOD ZIP resource limits: aggregate declared expansion capped before extraction');
  }

  // Central-directory sizes can be forged. The live streaming path must stop
  // once real output crosses the per-entry limit, even when metadata says 1 B.
  {
    const oldInfo = '[{"type":"MOD_INFO","id":"main-old"}]';
    const target = mods + '/main';
    const e = makeScenario({
      entries: [
        { path: 'main/modinfo.json', content: '[{"type":"MOD_INFO","id":"main-new"}]' },
        { path: 'main/huge.bin', content: 'x', uncompressedSize: 1, actualLength: 64 * 1024 * 1024 + 1 },
      ],
      initialFiles: [[target + '/modinfo.json', oldInfo], [target + '/old.txt', 'preserve']],
    });
    await e.install();
    assert.equal(e.files.get(target + '/modinfo.json'), oldInfo,
      'metadata under-reporting must not replace a pre-existing MOD');
    assert.equal(e.files.get(target + '/old.txt'), 'preserve');
    assert(![...e.files.keys()].some(p => p.includes('/.mod-installer-staging/')),
      'failed bounded decompression must clean the staging tree');
    assert.equal(e.files.has(target + '/huge.bin'), false);
    assert.equal(e.persistCalls(), 1, 'staging cleanup is marked for persistence');
    assert(e.notices.some(message => message.includes('64 MiB')),
      'actual streamed size must trigger a user-visible resource-limit error');
    console.log('PASS MOD ZIP safety: forged declared size cannot bypass actual stream expansion cap');
  }

  {
    const entries = Array.from({ length: 20001 }, (_, i) => ({
      path: 'many/file-' + i + '.json',
      content: '{}',
    }));
    const e = makeScenario({ entries });
    await e.install();
    assert.equal(e.operations.length, 0, 'too many ZIP entries must be rejected without filesystem mutation');
    assert.equal(e.persistCalls(), 0);
    assert(e.notices.some(message => message.includes('20000')));
    console.log('PASS MOD ZIP resource limits: excessive entry count rejected');
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

  // A central directory with more than the allowed number of tiny entries must
  // be rejected before JSZip constructs an object for every entry.
  {
    const tooManyZip = await (async () => {
      const zip = new RealJSZip();
      for (let i = 0; i < 20001; ++i) {
        zip.file('entry-' + String(i).padStart(5, '0') + '.txt', '');
      }
      return zip.generateAsync({ type: 'uint8array', compression: 'STORE' });
    })();
    const e = makeScenario({
      entries: [],
      fileName: 'too-many-entries.zip',
      fileSize: tooManyZip.byteLength,
      zipBytes: tooManyZip,
    });
    await e.install();
    assert.equal(e.arrayBufferReads(), 1, 'read the bounded archive bytes for the preflight scan');
    assert.equal(e.loadAsyncCalls(), 0, 'reject entry floods before JSZip parsing');
    assert.equal(e.persistCalls(), 0, 'rejected archive must not touch persistent storage');
    assert(e.notices.some(message => /20000/.test(message) && /entry|エントリ/i.test(message)),
      'the player must see the entry-count limit');
    console.log('PASS real ZIP preflight: excessive central-directory entries rejected before JSZip parsing');

    // Under-report the EOCD count while retaining all 20,001 central-directory
    // records. The scanner must count the records rather than trust this field.
    const forgedCountZip = new Uint8Array(tooManyZip);
    const forgedView = new DataView(forgedCountZip.buffer);
    let eocd = -1;
    for (let offset = forgedCountZip.length - 22;
         offset >= Math.max(0, forgedCountZip.length - 22 - 0xffff); --offset) {
      if (forgedView.getUint32(offset, true) === 0x06054b50 &&
          offset + 22 + forgedView.getUint16(offset + 20, true) === forgedCountZip.length) {
        eocd = offset;
        break;
      }
    }
    assert(eocd >= 0, 'find EOCD to forge the declared entry count');
    forgedView.setUint16(eocd + 8, 1, true);
    forgedView.setUint16(eocd + 10, 1, true);
    const forged = makeScenario({
      entries: [],
      fileName: 'underreported-entry-count.zip',
      fileSize: forgedCountZip.byteLength,
      zipBytes: forgedCountZip,
    });
    await forged.install();
    assert.equal(forged.loadAsyncCalls(), 0,
      'actual central-directory records must be counted when the EOCD count is forged');
    assert.equal(forged.persistCalls(), 0);
    assert(forged.notices.some(message => /20000/.test(message)),
      'under-reported ZIP entry count must hit the same entry limit');
    console.log('PASS real ZIP preflight: forged EOCD count cannot hide excessive central-directory entries');
  }

  // Exercise the actual bundled JSZip parser and its streaming API. Forge both
  // ZIP uncompressed-size fields to 1 byte while leaving a valid 64 MiB+1
  // DEFLATE payload. This verifies the defense is not merely a fake-adapter test.
  {
    const forgedZip = await (async () => {
      const zip = new RealJSZip();
      const payloadSize = 64 * 1024 * 1024 + 1;
      zip.file('main/modinfo.json', '[{"type":"MOD_INFO","id":"main-new"}]');
      zip.file('main/huge.bin', new Uint8Array(payloadSize), {
        binary: true, compression: 'DEFLATE', compressionOptions: { level: 1 },
      });
      const bytes = await zip.generateAsync({
        type: 'uint8array', compression: 'DEFLATE', compressionOptions: { level: 1 },
      });
      const name = new TextEncoder().encode('main/huge.bin');
      const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
      let localName = -1, centralName = -1;
      const matchesAt = (at, signature) =>
        at >= 0 && bytes[at] === signature[0] && bytes[at + 1] === signature[1] &&
        bytes[at + 2] === signature[2] && bytes[at + 3] === signature[3];
      for (let i = 0; i <= bytes.length - name.length; i++) {
        let match = true;
        for (let j = 0; j < name.length; j++) {
          if (bytes[i + j] !== name[j]) { match = false; break; }
        }
        if (!match) continue;
        if (matchesAt(i - 30, [0x50, 0x4b, 0x03, 0x04])) localName = i;
        if (matchesAt(i - 46, [0x50, 0x4b, 0x01, 0x02])) centralName = i;
      }
      assert(localName >= 30, 'find huge entry local header');
      assert(centralName >= 46, 'find huge entry central-directory header');
      view.setUint32(localName - 30 + 22, 1, true);
      view.setUint32(centralName - 46 + 24, 1, true);
      assert(bytes.byteLength < 128 * 1024 * 1024, 'compressed test archive remains under the installer cap');
      return bytes;
    })();
    const oldInfo = '[{"type":"MOD_INFO","id":"main-old"}]';
    const target = mods + '/main';
    const e = makeScenario({
      entries: [],
      fileName: 'forged-size.zip',
      fileSize: forgedZip.byteLength,
      zipBytes: forgedZip,
      initialFiles: [[target + '/modinfo.json', oldInfo], [target + '/old.txt', 'preserve']],
    });
    await e.install();
    assert.equal(e.files.get(target + '/modinfo.json'), oldInfo,
      'real JSZip stream overflow must leave the previous MOD intact');
    assert.equal(e.files.get(target + '/old.txt'), 'preserve');
    assert(![...e.files.keys()].some(p => p.includes('/.mod-installer-staging/')),
      'real ZIP overflow must clean staging');
    assert.equal(e.files.has(target + '/huge.bin'), false);
    assert.equal(e.persistCalls(), 1);
    assert(e.notices.some(message => message.includes('64 MiB')),
      'actual output size, not forged ZIP metadata, must stop extraction');
    console.log('PASS real JSZip: forged central/local size fields cannot bypass stream expansion limit');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
