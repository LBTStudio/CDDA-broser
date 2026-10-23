// Regression tests for the actual browser-shell Japanese settings migration.
const assert = require('node:assert/strict');
const fsNode = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fsNode.readFileSync(path.join(__dirname, '../shell/index.html'), 'utf8');
const start = html.indexOf('      function migrateJapaneseDefault(');
const end = html.indexOf('      // The software SDL backend', start);
assert(start >= 0 && end > start, 'find the actual settings migration');
const source = html.slice(start, end);

function scenario({ optionsContent, marker = false, readFailure = false } = {}) {
  const configDir = '/home/player/.cataclysm-dda/config';
  const optionsPath = configDir + '/options.json';
  const markerPath = configDir + '/.web-ja-default-v3';
  const files = new Map();
  const dirs = new Set(['/home', '/home/player', '/home/player/.cataclysm-dda', configDir]);
  const writes = [];
  if (optionsContent !== undefined) files.set(optionsPath, optionsContent);
  if (marker) files.set(markerPath, 'v3\n');
  const fs = {
    analyzePath(target) { return { exists: files.has(target) || dirs.has(target) }; },
    mkdirTree(target) { dirs.add(target); },
    readFile(target) {
      if (readFailure) throw new Error('injected read failure');
      if (!files.has(target)) throw new Error('missing file: ' + target);
      return files.get(target);
    },
    writeFile(target, value) {
      writes.push([target, value]);
      files.set(target, value);
    },
  };
  const context = vm.createContext({ console: { warn() {} } });
  vm.runInContext(source + '\nglobalThis.runMigration = migrateJapaneseDefault;', context);
  return {
    files, writes, optionsPath, markerPath,
    run() { return context.runMigration(fs, configDir); },
  };
}

{
  const original = [
    { name: 'USE_LANG', value: 'en' },
    { name: 'AUTOSAVE', value: 'true' },
    { name: 'CUSTOM_OPTION', value: 'keep-me' },
  ];
  const e = scenario({ optionsContent: JSON.stringify(original) });
  assert.equal(e.run(), true);
  assert.deepEqual(JSON.parse(e.files.get(e.optionsPath)), [
    { name: 'USE_LANG', value: 'ja' },
    original[1], original[2],
  ]);
  assert.equal(e.files.get(e.markerPath), 'v3\n');
  assert.equal(e.writes.length, 2);
  console.log('PASS config migration: change only the English language default and preserve other settings');
}

for (const optionsContent of [
  '{broken',
  '{"name":"USE_LANG","value":"en"}',
  'null',
  '42',
]) {
  const e = scenario({ optionsContent });
  assert.equal(e.run(), false, 'malformed or non-array settings must abort migration');
  assert.equal(e.files.get(e.optionsPath), optionsContent, 'original options must remain byte-for-byte unchanged');
  assert.equal(e.files.has(e.markerPath), false, 'do not mark an unsuccessful migration complete');
  assert.equal(e.writes.length, 0, 'do not write defaults over unreadable settings');
}
console.log('PASS config migration: malformed and wrong-shaped options are preserved without writes');

{
  const e = scenario({ optionsContent: '[{"name":"CUSTOM_OPTION","value":"kept"}]', readFailure: true });
  assert.equal(e.run(), false);
  assert.equal(e.files.get(e.optionsPath), '[{"name":"CUSTOM_OPTION","value":"kept"}]');
  assert.equal(e.files.has(e.markerPath), false);
  assert.equal(e.writes.length, 0);
  console.log('PASS config migration: read failure preserves the original options');
}

{
  const e = scenario();
  assert.equal(e.run(), true, 'first run without a settings file creates the language default');
  assert.deepEqual(JSON.parse(e.files.get(e.optionsPath)), [{ name: 'USE_LANG', value: 'ja' }]);
  assert.equal(e.files.get(e.markerPath), 'v3\n');
  console.log('PASS config migration: new profiles receive the Japanese default');
}

{
  const original = '[{"name":"USE_LANG","value":"en"}]';
  const e = scenario({ optionsContent: original, marker: true });
  assert.equal(e.run(), false, 'an already-completed migration is a no-op');
  assert.equal(e.files.get(e.optionsPath), original);
  assert.equal(e.writes.length, 0);
  console.log('PASS config migration: existing migration marker is respected');
}
