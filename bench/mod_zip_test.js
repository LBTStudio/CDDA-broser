// Exercise the actual MOD ZIP handler from shell/index.html.
const assert = require('node:assert/strict');
const fsNode = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fsNode.readFileSync(path.join(__dirname, '../shell/index.html'), 'utf8');
const start = html.indexOf('document.getElementById("mod-zip-input").addEventListener("change", async (ev) => {');
const end = html.indexOf('/* ---- Japanese (IME) input bridge', start);
assert(start >= 0 && end > start, 'find actual MOD installer handler');
const source = html.slice(start, end);
const entries = new Map([
  ['pack/main/modinfo.json', '{"type":"MOD_INFO","id":"main"}'],
  ['pack/main/data.json', '{"main":true}'],
  ['pack/main/assets/icon.json', '{"icon":true}'],
  ['pack/main/sub/modinfo.json', '{"type":"MOD_INFO","id":"sub"}'],
  ['pack/main/sub/child.json', '{"child":true}'],
  ['pack/main/sub/deep/asset.json', '{"deep":true}'],
]);
const zip = {
  forEach(callback) {
    for (const [name, content] of entries) {
      callback(name, { dir: false, async: async () => new TextEncoder().encode(content).buffer });
    }
  },
};
const input = { addEventListener(name, callback) { assert.equal(name, 'change'); input.handler = callback; } };
const dirs = new Set();
const files = new Map();
function mkdirTree(target) {
  let current = '';
  for (const part of target.split('/').filter(Boolean)) {
    current += '/' + part;
    dirs.add(current);
  }
}
const fakeFs = {
  analyzePath(target) { return { exists: dirs.has(target) || files.has(target) }; },
  mkdirTree,
  writeFile(target, value) { files.set(target, new TextDecoder().decode(value)); },
};
const notices = [];
let persistCalls = 0;
const module = { FS: fakeFs };
const context = {
  document: { getElementById(id) { assert.equal(id, 'mod-zip-input'); return input; } },
  window: { CDDA_PROFILE: 'audit', Module: module },
  Module: module,
  JSZip: { loadAsync: async () => zip },
  booted: true,
  fsReady: () => true,
  modRoot: () => '/home/audit/.cataclysm-dda/mods',
  requestPersist: () => { persistCalls++; },
  noticeMessage: message => notices.push(message),
  confirm: () => true,
  console,
};
vm.runInNewContext(source, context);
(async () => {
  const file = { name: 'bundle.zip', arrayBuffer: async () => new ArrayBuffer(0) };
  await input.handler({ target: { files: [file], value: 'bundle.zip' } });
  const parent = '/home/audit/.cataclysm-dda/mods/main/';
  const child = '/home/audit/.cataclysm-dda/mods/sub/';
  assert.equal(files.get(parent + 'modinfo.json'), entries.get('pack/main/modinfo.json'));
  assert.equal(files.get(parent + 'data.json'), entries.get('pack/main/data.json'));
  assert.equal(files.get(parent + 'assets/icon.json'), entries.get('pack/main/assets/icon.json'));
  assert.equal(files.has(parent + 'sub/modinfo.json'), false, 'parent must not duplicate nested MOD_INFO root');
  assert.equal(files.has(parent + 'sub/child.json'), false, 'parent must not duplicate nested MOD payload');
  assert.equal(files.get(child + 'modinfo.json'), entries.get('pack/main/sub/modinfo.json'));
  assert.equal(files.get(child + 'child.json'), entries.get('pack/main/sub/child.json'));
  assert.equal(files.get(child + 'deep/asset.json'), entries.get('pack/main/sub/deep/asset.json'));
  assert.equal(persistCalls, 1);
  assert(notices.some(message => message.includes('Installed')));
  console.log('PASS MOD ZIP: parent and nested MOD roots install independently without duplicate payload');
})().catch(error => { console.error(error); process.exitCode = 1; });
