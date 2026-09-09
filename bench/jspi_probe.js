// Autonomous mechanism comparison. Requires Emscripten 4.0.15 and Chromium.
// CDDA_EMXX=/path/to/em++ CDDA_PLAYWRIGHT_MODULE=/path/to/playwright-core \
//   node bench/jspi_probe.js
// This is NOT the full game, a Chromebook emulator, or a peak-RAM measurement.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require(process.env.CDDA_PLAYWRIGHT_MODULE || 'playwright-core');
const emxx = process.env.CDDA_EMXX;
assert(emxx, 'Set CDDA_EMXX to the isolated Emscripten 4.0.15 em++');
const out = path.join(__dirname, 'out/jspi-probe');
fs.mkdirSync(out, { recursive: true });
process.env.TMPDIR = out;
const compiler = execFileSync(emxx, ['--version'], { encoding: 'utf8' }).split('\n')[0];
assert(compiler.includes('4.0.15'), 'Pin compiler for reproducible comparisons');
const variants = [
  { name: 'sync-js-eh', flags: ['-DPROBE_SYNC', '-fexceptions'] },
  { name: 'asyncify-js-eh', flags: ['-sASYNCIFY', '-fexceptions'] },
  { name: 'sync-wasm-eh', flags: ['-DPROBE_SYNC', '-fwasm-exceptions'] },
  { name: 'jspi-wasm-eh', flags: ['-sJSPI', '-fwasm-exceptions'] },
  { name: 'jspi-js-eh-negative', flags: ['-sJSPI', '-fexceptions'], negative: true },
];
for (const variant of variants) {
  execFileSync(emxx, [path.join(__dirname, 'asyncify_overhead.cpp'), '-std=c++17',
    '-O2', ...variant.flags, `-DPROBE_BACKEND="${variant.name}"`, '-sASSERTIONS=1',
    '-sENVIRONMENT=web', '-sINITIAL_MEMORY=67108864', '-o', path.join(out, variant.name + '.js')],
  { stdio: 'inherit', env: { ...process.env, EMCC_CORES: '1' } });
}

async function run() {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const report = { compiler, browser: browser.version(),
    note: 'Mechanism-only. Exception ABI changes as well as suspension backend. CPU throttle is not Chromebook emulation.',
    runs: [] };
  try {
    // Sequential fresh contexts avoid simultaneously allocating several Wasm heaps.
    for (const rate of [1, 4]) {
      // Alternate order to reduce order/warm-machine bias.
      const ordered = rate === 1 ? variants : [...variants].reverse();
      for (const variant of ordered) {
        const context = await browser.newContext();
        try {
          const page = await context.newPage(), logs = [], errors = [];
          const cdp = await context.newCDPSession(page);
          await cdp.send('Emulation.setCPUThrottlingRate', { rate });
          page.on('console', message => {
            if (/^(PASS|RESULT|DONE)/.test(message.text())) logs.push(message.text());
          });
          page.on('pageerror', error => errors.push(error.message));
          await context.route('https://runtime-probe.test/**', async route => {
            const name = new URL(route.request().url()).pathname.slice(1);
            if (!name) {
              return route.fulfill({ contentType: 'text/html', body:
                `<!doctype html><script>var Module={print:s=>console.log(s)};</script><script src="${variant.name}.js"></script>` });
            }
            assert([variant.name + '.js', variant.name + '.wasm'].includes(name));
            return route.fulfill({ path: path.join(out, name),
              contentType: name.endsWith('.wasm') ? 'application/wasm' : 'application/javascript' });
          });
          await page.goto('https://runtime-probe.test/');
          for (let i = 0; i < 600 && !errors.length && !logs.some(s => s.startsWith('DONE')); ++i) {
            await page.waitForTimeout(100);
          }
          if (variant.negative) {
            assert(errors.some(s => /trying to suspend JS frames/.test(s)), 'JS exception incompatibility must be exposed');
          } else {
            assert.deepEqual(errors, []);
            assert(logs.some(s => s.startsWith('PASS suspension followed by exception')));
            assert(logs.some(s => s.startsWith('DONE')), 'probe timed out');
            assert.equal(logs.filter(s => s.startsWith('RESULT')).length, 3);
            for (const line of logs.filter(s => s.startsWith('RESULT'))) {
              assert(line.includes('checksum=98763608'), 'simulation fixture checksum changed');
            }
          }
          const entry = { backend: variant.name, cpuThrottle: rate,
            wasmBytes: fs.statSync(path.join(out, variant.name + '.wasm')).size, logs, errors };
          report.runs.push(entry);
          console.log(JSON.stringify(entry));
        } finally { await context.close(); }
      }
    }
    fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify(report, null, 2));
    console.log('PASS 10 browser runs, checksums, exceptions/RAII, negative controls; see out/jspi-probe/results.json');
  } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
