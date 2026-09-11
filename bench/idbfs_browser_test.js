// Actual Emscripten IDBFS + Chromium integration, using CDDA's mount function.
// Not the full game and not a ChromeOS memory/performance benchmark.
// CDDA_EMCC=/path/to/emcc CDDA_PLAYWRIGHT_MODULE=/path/to/playwright-core \
//   node bench/idbfs_browser_test.js /path/to/patched/cdda
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require(process.env.CDDA_PLAYWRIGHT_MODULE || 'playwright-core');
if (!process.argv[2] || !process.env.CDDA_EMCC) throw new Error('Set CDDA_EMCC and supply patched source directory');
const backend = process.env.CDDA_RUNTIME_BACKEND || 'asyncify';
assert(['asyncify', 'jspi'].includes(backend), 'Unknown suspension backend');
const source = fs.readFileSync(path.join(process.argv[2], 'src/main.cpp'), 'utf8');
const start = source.indexOf('EM_ASYNC_JS( void, mount_idbfs,');
const end = source.indexOf('\n} );', start);
assert(start >= 0 && end > start);
const out = path.join(__dirname, 'out/idbfs-browser-' + backend);
fs.mkdirSync(out, { recursive: true });
process.env.TMPDIR = out;
const harness = '#include <emscripten.h>\n' + source.slice(start, end + 5) + `
int main(int argc, char **argv) {
    if (argc != 2) return 1;
    mount_idbfs(argv[1]);
    EM_ASM({ window.testReady = true; });
    return 0;
}
`;
fs.writeFileSync(path.join(out, 'harness.c'), harness);
execFileSync(process.env.CDDA_EMCC, [path.join(out, 'harness.c'), '-o', path.join(out, 'harness.js'),
  '-O1', ...(backend === 'jspi' ? ['-sJSPI', '-fwasm-exceptions'] : ['-sASYNCIFY']),
  '-sFORCE_FILESYSTEM=1', '-sEXPORTED_RUNTIME_METHODS=["FS"]',
  '-sEXIT_RUNTIME=0', '-sINITIAL_MEMORY=16777216', '-sASSERTIONS=1', '-lidbfs.js'], { stdio: 'inherit' });
const html = `<!doctype html><meta charset="utf-8"><script>
window.testReady = false;
window.profile = new URL(location.href).searchParams.get('profile') || 'alpha';
window.root = '/home/' + profile + '/.cataclysm-dda';
window.syncStates = [];
window.CDDA_ON_IDBFS_SYNC_STATE = (failed, detail) => syncStates.push({failed, detail});
window.CDDA_ON_IDBFS_ERROR = detail => { window.mountError = String(detail); };
var Module = { arguments: [root], onAbort: detail => { window.abortError = String(detail); } };
</script><script src="harness.js"></script>`;

async function run() {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    // A fresh browser context owns a disposable origin; no real user's save is accessed.
    const context = await browser.newContext();
    await context.route('https://cdda-save.test/**', async route => {
      const name = new URL(route.request().url()).pathname.slice(1);
      if (name === 'harness.js' || name === 'harness.wasm') {
        await route.fulfill({ path: path.join(out, name),
          contentType: name.endsWith('.wasm') ? 'application/wasm' : 'application/javascript' });
      } else {
        await route.fulfill({ body: html, contentType: 'text/html' });
      }
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    async function open(profile = 'alpha') {
      await page.goto('https://cdda-save.test/?profile=' + profile);
      await page.waitForFunction(() => window.testReady || window.abortError);
      assert.equal(await page.evaluate(() => window.abortError || window.mountError || ''), '');
      await page.evaluate(() => {
        window.successWrites = 0; window.activeWrites = 0; window.maxActiveWrites = 0;
        window.failNext = false; window.addDuringSync = false;
        const sync = FS.syncfs.bind(FS);
        FS.syncfs = (populate, cb) => {
          if (populate) return sync(populate, cb);
          ++activeWrites; maxActiveWrites = Math.max(maxActiveWrites, activeWrites);
          if (failNext) {
            failNext = false;
            setTimeout(() => { --activeWrites; cb(new Error('injected temporary storage failure')); }, 0);
            return;
          }
          sync(false, err => { --activeWrites; if (!err) ++successWrites; cb(err); });
          if (addDuringSync) {
            addDuringSync = false;
            FS.writeFile(root + '/save/追加.sav', '同期中の追加変更');
            window.setFsNeedsSync();
          }
        };
        // Saving must succeed even if the page never receives a paint callback.
        window.requestAnimationFrame = () => { throw new Error('Unexpected paint dependency'); };
      });
    }
    const content = JSON.stringify({ world: '日本語の世界', items: ['包帯', '水'], turn: 12345 });
    await open();
    await page.evaluate(text => {
      FS.mkdirTree(root + '/save');
      FS.writeFile(root + '/save/世界.sav', text);
      for (let i = 0; i < 1000; ++i) window.setFsNeedsSync();
    }, content);
    await page.waitForFunction(() => successWrites === 1 && activeWrites === 0);
    await open();
    assert.equal(await page.evaluate(() => FS.readFile(root + '/save/世界.sav', { encoding: 'utf8' })), content);
    console.log('PASS real IDBFS: 1000 dirty notifications, one sync, exact Japanese bytes restored on reload');

    await page.evaluate(() => {
      failNext = true;
      FS.writeFile(root + '/save/retry.sav', '再試行後に復元');
      window.setFsNeedsSync();
    });
    await page.waitForFunction(() => syncStates.some(s => s.failed));
    await page.waitForFunction(() => successWrites === 1 && syncStates.at(-1).failed === false);
    await open();
    assert.equal(await page.evaluate(() => FS.readFile(root + '/save/retry.sav', { encoding: 'utf8' })), '再試行後に復元');
    console.log('PASS real IDBFS: injected failed attempt retried without new mutation; bytes survive reload');

    await page.evaluate(() => {
      FS.writeFile(root + '/save/first.sav', '先行の変更');
      addDuringSync = true;
      window.setFsNeedsSync();
    });
    await page.waitForFunction(() => successWrites === 2 && activeWrites === 0);
    assert.equal(await page.evaluate(() => maxActiveWrites), 1);
    await open();
    assert.equal(await page.evaluate(() => FS.readFile(root + '/save/追加.sav', { encoding: 'utf8' })), '同期中の追加変更');
    assert.equal(await page.evaluate(() => FS.readFile(root + '/save/first.sav', { encoding: 'utf8' })), '先行の変更');
    console.log('PASS real IDBFS: writes made during sync are persisted in serialized subsequent pass');

    // A save spanning many event-loop turns must not start intermediate scans.
    // Exercise real IDBFS renames/deletes and a failed final sync, then reload.
    await page.evaluate(async () => {
      FS.writeFile(root + '/save/delete.sav', 'old');
      window.setFsNeedsSync();
      window.beginFsSyncBatch();
      window.beginFsSyncBatch();
      for (let i = 0; i < 4; ++i) {
        FS.writeFile(root + '/save/batch.tmp', '保存の最終世代 ' + i);
        window.setFsNeedsSync();
        document.dispatchEvent(new Event('visibilitychange'));
        window.dispatchEvent(new Event('pagehide'));
        await new Promise(resolve => setTimeout(resolve, 300));
        if (successWrites !== 0 || activeWrites !== 0) throw new Error('mid-save sync');
      }
      FS.rename(root + '/save/batch.tmp', root + '/save/batch.sav');
      FS.unlink(root + '/save/delete.sav');
      window.setFsNeedsSync();
      window.endFsSyncBatch();
      await new Promise(resolve => setTimeout(resolve, 350));
      if (successWrites !== 0) throw new Error('nested batch escaped');
      failNext = true;
      window.endFsSyncBatch();
    });
    await page.waitForFunction(() => syncStates.some(s => s.failed));
    await page.waitForFunction(() => successWrites === 1 && activeWrites === 0 && !syncStates.at(-1).failed);
    assert.equal(await page.evaluate(() => maxActiveWrites), 1);
    await open();
    assert.equal(await page.evaluate(() => FS.readFile(root + '/save/batch.sav', {encoding: 'utf8'})), '保存の最終世代 3');
    assert.equal(await page.evaluate(() => FS.analyzePath(root + '/save/batch.tmp').exists), false);
    assert.equal(await page.evaluate(() => FS.analyzePath(root + '/save/delete.sav').exists), false);
    console.log('PASS real IDBFS: long/nested save, lifecycle flushes, final retry, rename/delete reload');

    await open('beta');
    assert.equal(await page.evaluate(() => FS.analyzePath(root + '/save/世界.sav').exists), false);
    await open('alpha');
    assert.equal(await page.evaluate(() => FS.readFile(root + '/save/世界.sav', { encoding: 'utf8' })), content);
    assert.deepEqual(errors, []);
    console.log('PASS real IDBFS: player profile isolation; no browser runtime errors');
    console.log('PASS backend=' + backend + ': autonomous real-browser persistence checks completed');
    console.log('NOTE: injected error is not real disk exhaustion; this does not establish full-game/4GB/eviction guarantees');
  } finally {
    await browser.close();
  }
}
run().catch(err => { console.error(err); process.exitCode = 1; });
