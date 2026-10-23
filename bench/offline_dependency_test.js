#!/usr/bin/env node
// The core game may be started entirely from persistent Cache Storage.
// These smaller utilities are still required for Export Saves and Install
// Mod, so their deployed copies must not depend on a live CDN.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const shell = fs.readFileSync(path.join(root, 'shell/index.html'), 'utf8');
const jszipPath = path.join(root, 'shell/vendor/jszip-3.10.1.min.js');
const saverPath = path.join(root, 'shell/vendor/FileSaver-2.0.0.min.js');
const licensesPath = path.join(root, 'shell/vendor/THIRD-PARTY-LICENSES.md');
const workflow = fs.readFileSync(path.join(root, '.github/workflows/build-and-release.yml'), 'utf8');
const manual = fs.readFileSync(path.join(root, 'manual/build-and-release.yml'), 'utf8');
const localBuild = fs.readFileSync(path.join(root, 'scripts/build-local.sh'), 'utf8');

assert(shell.includes('src="vendor/jszip-3.10.1.min.js"'));
assert(shell.includes('src="vendor/FileSaver-2.0.0.min.js"'));
assert(!/<script[^>]+src="https:\/\/cdnjs\.cloudflare\.com\/ajax\/libs\/jszip\//i.test(shell),
  'JSZip must not require the CDN for MOD installation or save export');
assert(!/<script[^>]+src="https:\/\/cdnjs\.cloudflare\.com\/ajax\/libs\/FileSaver\.js\//i.test(shell),
  'FileSaver must not require the CDN for save export');

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
console.log('PASS offline critical dependencies: same-origin JSZip/FileSaver, vendor licenses, Pages/local bundle parity');
