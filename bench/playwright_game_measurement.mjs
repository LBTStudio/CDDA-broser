#!/usr/bin/env node
// Run a real deployed CDDA WebAssembly bundle in an isolated Playwright profile.
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { chromium } from 'playwright';

const bundle = path.resolve(process.argv[2] || '.');
const scenario = process.argv[3] || 'read4';
const renderer = process.argv[4] || 'game';
const baseUrl = process.argv[5] || 'https://lbtstudio.github.io/CDDA-broser/';
const profile = scenario + '_z0'; // The workflow passes population=0 to set the renderer.
if (!fs.existsSync(path.join(bundle, 'index.html'))) throw new Error('Missing bundle index.html');
if (!['read4', 'read', 'sleep'].includes(scenario)) throw new Error('Unsupported scenario: ' + scenario);
if (!['game', 'software', 'opengles2'].includes(renderer)) throw new Error('Unsupported renderer: ' + renderer);

const outputRoot = path.resolve('bench/out/browser-measurement');
const out = path.join(outputRoot, scenario + '-' + renderer);
fs.mkdirSync(out, { recursive: true });
const captured = { screens: [], result: null, other: [] };
const consoleLog = [], pageErrors = [], requestFailures = [];
const mime = { '.html':'text/html; charset=utf-8', '.js':'text/javascript; charset=utf-8', '.wasm':'application/wasm', '.data':'application/octet-stream', '.json':'application/json; charset=utf-8', '.ttf':'font/ttf', '.ico':'image/x-icon', '.png':'image/png' };
const now = () => Number(process.hrtime.bigint()) / 1e6;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, context, page, tracing = false, newGameResolve, newGameReject, newGameTimer;
const newGameSeen = new Promise((resolve, reject) => { newGameResolve = resolve; newGameReject = reject; });

const server = http.createServer((req, res) => {
  const url = new URL(req.url || '/', 'http://127.0.0.1');
  if (req.method === 'POST' && url.pathname.startsWith('/capture-')) {
    const chunks = []; let length = 0;
    req.on('data', chunk => {
      length += chunk.length;
      if (length > 16 * 1024 * 1024) { res.writeHead(413).end(); req.destroy(); }
      else chunks.push(chunk);
    });
    req.on('end', () => {
      const body = Buffer.concat(chunks);
      if (url.pathname === '/capture-screen') {
        const match = /^data:image\/png;base64,([A-Za-z0-9+/=]+)$/.exec(body.toString('utf8'));
        if (!match) captured.other.push({ endpoint: url.pathname, error: 'invalid PNG data URL' });
        else {
          const file = path.join(out, 'game-canvas-' + (captured.screens.length + 1) + '.png');
          fs.writeFileSync(file, Buffer.from(match[1], 'base64'));
          captured.screens.push({ file: path.basename(file), bytes: fs.statSync(file).size });
        }
      } else if (url.pathname === '/capture-result') {
        fs.writeFileSync(path.join(out, 'fixture-result-full.json'), body);
        try { captured.result = JSON.parse(body.toString('utf8')); }
        catch (error) { captured.other.push({ endpoint: url.pathname, error: String(error) }); }
      } else captured.other.push({ endpoint: url.pathname, bytes: body.length });
      res.writeHead(204, { 'cache-control': 'no-store' }).end();
    });
    return;
  }
  let pathname;
  try { pathname = decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname); }
  catch { res.writeHead(400).end('bad path'); return; }
  const file = path.resolve(bundle, '.' + pathname);
  if (!file.startsWith(bundle + path.sep) || pathname.includes('..')) { res.writeHead(403).end('forbidden'); return; }
  fs.stat(file, (error, stat) => {
    if (error || !stat.isFile()) { res.writeHead(404).end('not found'); return; }
    res.writeHead(200, { 'content-type': mime[path.extname(file).toLowerCase()] || 'application/octet-stream',
      'content-length': stat.size, 'cache-control': 'no-store' });
    fs.createReadStream(file).pipe(res);
  });
});

async function snapshotSave() {
  return page.evaluate(variant => {
    try {
      const fs = window.Module?.FS || window.FS;
      const base = '/home/perf_' + variant + '/.cataclysm-dda/save';
      if (!fs || !fs.analyzePath(base).exists) return { available: false, reason: 'save directory unavailable' };
      const candidates = [];
      for (const world of fs.readdir(base).filter(name => name !== '.' && name !== '..')) {
        const dir = base + '/' + world;
        for (const name of fs.readdir(dir).filter(name => name.endsWith('.sav'))) {
          const raw = fs.readFile(dir + '/' + name, { encoding: 'utf8' });
          const start = raw.indexOf('{');
          if (start < 0) continue;
          const data = JSON.parse(raw.slice(start));
          const player = data.player || {};
          const position = Object.fromEntries(Object.entries(player).filter(([key]) => /pos|location|moves/i.test(key)).slice(0, 40));
          const fullPath = dir + '/' + name;
          const stat = fs.stat(fullPath);
          candidates.push({ world, file: name, turn: data.turn ?? null, activity: player.activity?.type ?? null,
            position, bytes: stat.size, mtimeMs: stat.mtime?.getTime?.() ?? null });
        }
      }
      candidates.sort((a, b) => (b.turn ?? -1) - (a.turn ?? -1));
      return candidates.length ? { available: true, ...candidates[0] } : { available: false, reason: 'no parseable character save' };
    } catch (error) { return { available: false, reason: String(error) }; }
  }, profile);
}

async function quickSave(tag, previous = null) {
  const beforeCommand = previous || await snapshotSave();
  const syncStartCount = await page.evaluate(() => window.__cddaMeasure?.persistenceSyncs?.length || 0);
  const marks = await page.evaluate(() => ({ at: performance.now(), task: window.__cddaMeasure.tasks.length }));
  const started = now();
  await page.keyboard.press('F5').catch(error => consoleLog.push({ type: 'save-key-error', text: String(error) }));
  let value = await snapshotSave();
  const changed = snapshot => {
    if (!snapshot?.available) return false;
    if (!beforeCommand?.available) return true;
    return snapshot.world !== beforeCommand.world || snapshot.file !== beforeCommand.file ||
      snapshot.turn !== beforeCommand.turn || snapshot.activity !== beforeCommand.activity ||
      snapshot.bytes !== beforeCommand.bytes || snapshot.mtimeMs !== beforeCommand.mtimeMs ||
      JSON.stringify(snapshot.position) !== JSON.stringify(beforeCommand.position);
  };
  while (!changed(value) && now() - started < 7000) { await pause(250); value = await snapshotSave(); }
  const elapsedMs = Number((now() - started).toFixed(2));
  const fileUpdated = changed(value);
  const syncWaitStarted = now();
  let syncWaitTimedOut = false;
  if (fileUpdated && persistenceInstrumentation.installed) {
    try {
      await page.waitForFunction(startCount =>
        (window.__cddaMeasure?.persistenceSyncs || []).slice(startCount).some(entry => !entry.populate),
        syncStartCount, { timeout: 5000 });
    } catch (_) { syncWaitTimedOut = true; }
  }
  const persistentSyncs = await page.evaluate(startCount =>
    (window.__cddaMeasure?.persistenceSyncs || []).slice(startCount).filter(entry => !entry.populate),
    syncStartCount);
  const syncWaitElapsedMs = Number((now() - syncWaitStarted).toFixed(2));
  const frameEffects = await page.evaluate(m => {
    const state = window.__cddaMeasure, tasks = state.tasks.slice(m.task), intervals = [];
    for (let i = 1; i < state.frameTimes.length; i++) {
      const from = state.frameTimes[i - 1], to = state.frameTimes[i];
      if (from >= m.at && to <= performance.now()) intervals.push(to - from);
    }
    const percentile = (values, q) => {
      const sorted = [...values].sort((a,b) => a-b);
      return sorted.length ? Number(sorted[Math.min(sorted.length - 1, Math.ceil(q * sorted.length) - 1)].toFixed(2)) : null;
    };
    return {
      rafIntervalsMeasured: intervals.length, rafIntervalP95Ms: percentile(intervals, .95),
      rafIntervalMaxMs: intervals.length ? Number(Math.max(...intervals).toFixed(2)) : null,
      rafIntervalsOver50Ms: intervals.filter(v => v > 50).length,
      rafIntervalsOver100Ms: intervals.filter(v => v > 100).length,
      longTaskCount: tasks.length,
      longTaskTotalMs: Number(tasks.reduce((sum, entry) => sum + entry.duration, 0).toFixed(2)),
      longTaskMaxMs: tasks.length ? Number(Math.max(...tasks.map(entry => entry.duration)).toFixed(2)) : null
    };
  }, marks);
  if (!value || typeof value !== 'object') value = { available: false, reason: 'no save snapshot' };
  value.saveTiming = {
    f5ToFileUpdateMs: fileUpdated ? elapsedMs : null,
    f5NoFileUpdateObservedWithinMs: fileUpdated ? null : elapsedMs,
    fileUpdated,
    persistenceInstrumentationInstalled: persistenceInstrumentation.installed,
    idbfsSyncsAfterCommand: persistentSyncs,
    idbfsWaitAfterFileUpdateMs: fileUpdated ? syncWaitElapsedMs : null,
    idbfsWaitTimedOut: fileUpdated && persistenceInstrumentation.installed ? syncWaitTimedOut : null,
    frameEffects
  };
  fs.writeFileSync(path.join(out, tag + '-save-snapshot.json'), JSON.stringify(value, null, 2));
  return value;
}

async function waitForStableSave(timeoutMs = 20000) {
  const started = now();
  let lastSignature = null, stable = 0, value = null, lastFlags = {};
  while (now() - started < timeoutMs) {
    value = await snapshotSave();
    lastFlags = await page.evaluate(() => ({
      pendingSyncs: window.__cddaMeasure?.persistenceSyncPending || 0,
      gameUnsaved: !!window.game_unsaved,
      persistencePending: !!window.cdda_persistence_pending
    }));
    const signature = JSON.stringify(value.available ? {
      world: value.world, file: value.file, turn: value.turn, activity: value.activity,
      bytes: value.bytes, mtimeMs: value.mtimeMs, position: value.position
    } : value);
    if (value.available && signature === lastSignature && lastFlags.pendingSyncs === 0 &&
        !lastFlags.gameUnsaved && !lastFlags.persistencePending) {
      stable++;
      if (stable >= 2) return { settled: true, elapsedMs: Number((now() - started).toFixed(2)), snapshot: value, flags: lastFlags };
    } else stable = 0;
    lastSignature = signature;
    await pause(500);
  }
  return { settled: false, elapsedMs: Number((now() - started).toFixed(2)), snapshot: value, flags: lastFlags };
}

let persistenceInstrumentation = { installed: false, reason: 'not attempted' };

async function installPersistenceInstrumentation() {
  persistenceInstrumentation = await page.evaluate(() => {
    const fs = window.Module?.FS || window.FS;
    const state = window.__cddaMeasure;
    if (!fs || typeof fs.syncfs !== 'function' || !state) {
      return { installed: false, reason: 'Emscripten FS.syncfs or measurement state unavailable' };
    }
    if (state.persistenceHookInstalled) return { installed: true, reason: 'already installed' };
    state.persistenceSyncs = state.persistenceSyncs || [];
    state.persistenceSyncPending = state.persistenceSyncPending || 0;
    const originalSyncfs = fs.syncfs;
    fs.syncfs = function(populate, callback) {
      const startedAt = performance.now();
      let settled = false;
      state.persistenceSyncPending++;
      const finish = error => {
        if (settled) return;
        settled = true;
        state.persistenceSyncPending = Math.max(0, state.persistenceSyncPending - 1);
        state.persistenceSyncs.push({
          populate: !!populate,
          startedAtMs: Number(startedAt.toFixed(2)),
          durationMs: Number((performance.now() - startedAt).toFixed(2)),
          success: !error,
          error: error ? String(error) : null
        });
      };
      const wrapped = typeof callback === 'function' ? function(error) {
        finish(error);
        return callback(error);
      } : callback;
      try {
        return originalSyncfs.call(this, populate, wrapped);
      } catch (error) {
        finish(error);
        throw error;
      }
    };
    state.persistenceHookInstalled = true;
    return { installed: true, reason: 'wrapped real Emscripten FS.syncfs' };
  }).catch(error => ({ installed: false, reason: String(error) }));
}

function percentile(values, q) {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted.length ? Number(sorted[Math.min(sorted.length - 1, Math.ceil(q * sorted.length) - 1)].toFixed(2)) : null;
}
function frameStats(values) {
  return { count: values.length, p50: percentile(values, .50), p95: percentile(values, .95), p99: percentile(values, .99),
    max: values.length ? Number(Math.max(...values).toFixed(2)) : null,
    over50: values.filter(v => v > 50).length, over100: values.filter(v => v > 100).length, over250: values.filter(v => v > 250).length };
}

async function main() {
  await new Promise((resolve, reject) => server.listen(0, '127.0.0.1', error => error ? reject(error) : resolve()));
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--enable-webgl', '--use-gl=angle', '--use-angle=swiftshader'] });
  context = await browser.newContext({ viewport: { width: 1365, height: 768 }, deviceScaleFactor: 1 });
  await context.tracing.start({ screenshots: true, snapshots: true, sources: false }); tracing = true;
  page = await context.newPage();
  let fixtureScreenshotSequence = 0;
  const fixtureScreens = [];
  await page.exposeBinding('__cddaCaptureFixtureScreenshot', async (source, label) => {
    const normalized = String(label || 'fixture').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'fixture';
    const filename = normalized === 'before-interruption'
      ? 'before-interruption.png'
      : 'fixture-' + normalized + '-' + (++fixtureScreenshotSequence) + '.png';
    const destination = path.join(out, filename);
    await source.page.screenshot({ path: destination, fullPage: true, animations: 'disabled', timeout: 30000 });
    fixtureScreens.push({ file: filename, bytes: fs.statSync(destination).size, source: 'Playwright page screenshot' });
    return filename;
  });
  await page.exposeBinding('__cddaCaptureFixtureResult', async (_source, payload) => {
    captured.result = payload;
    fs.writeFileSync(path.join(out, 'fixture-result-full.json'), JSON.stringify(payload));
    return true;
  });
  await page.addInitScript(() => {}
    const state = window.__cddaMeasure = { frameTimes: [], tasks: [], keys: [], persistenceSyncs: [], persistenceSyncPending: 0 };
    const original = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = callback => original(time => {
      if (state.frameTimes.length < 30000) state.frameTimes.push(time);
      callback(time);
    });
    window.addEventListener('keydown', event => {
      const item = { key: event.key, repeat: !!event.repeat, at: performance.now(), frameMs: null };
      if (state.keys.length < 5000) state.keys.push(item);
      requestAnimationFrame(() => { item.frameMs = Math.max(0, Number((performance.now() - item.at).toFixed(2))); });
    }, true);
    window.addEventListener('keyup', event => { if (state.keys.length < 5000) state.keys.push({ key: event.key, type: 'keyup', at: performance.now() }); }, true);
    try {
      new PerformanceObserver(list => list.getEntries().forEach(entry => {
        if (state.tasks.length < 2000) state.tasks.push({ start: entry.startTime, duration: entry.duration });
      })).observe({ type: 'longtask', buffered: true });
    } catch (_) { state.longTaskObserverUnavailable = true; }
  });
  page.on('console', message => {
    consoleLog.push({ type: message.type(), text: message.text(), atMs: Number(now().toFixed(2)) });
    if (message.text().startsWith('fixture:newgame')) newGameResolve(message.text());
  });
  page.on('pageerror', error => pageErrors.push(String(error?.stack || error)));
  page.on('requestfailed', request => requestFailures.push({ url: request.url(), failure: request.failure()?.errorText || 'unknown' }));
  page.on('crash', () => newGameReject(new Error('Chromium page crashed')));
  newGameTimer = setTimeout(() => newGameReject(new Error('No fixture:newgame marker in 180 seconds')), 180000);

  const port = server.address().port, fixture = 'fixture-' + profile + '.html', bootStarted = now();
  await page.goto('http://127.0.0.1:' + port + '/' + fixture, { waitUntil: 'domcontentloaded', timeout: 180000 });
  const newGameMessage = await newGameSeen;
  const bootMs = Number((now() - bootStarted).toFixed(2));
  clearTimeout(newGameTimer);
  await installPersistenceInstrumentation();
  const display = await page.evaluate(() => {
    const canvas = document.querySelector('canvas'), rect = canvas?.getBoundingClientRect(), style = canvas ? getComputedStyle(canvas) : null;
    return { language: document.documentElement.lang, viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio },
      canvas: canvas ? { width: canvas.width, height: canvas.height, rect: rect ? { x: rect.x, y: rect.y, width: rect.width, height: rect.height } : null,
        scaleX: rect && canvas.width ? rect.width / canvas.width : null, scaleY: rect && canvas.height ? rect.height / canvas.height : null,
        imageRendering: style?.imageRendering } : null,
      fonts: { status: document.fonts?.status || null, terminusCheck: document.fonts?.check ? document.fonts.check('16px Terminus') : null },
      wasmRuntimePresent: !!(window.Module || window.FS) };
  });
  await page.screenshot({ path: path.join(out, 'game-start-page.png'), fullPage: true, animations: 'disabled' }).catch(error => consoleLog.push({ type: 'screenshot-error', text: String(error) }));

  // Wait until the fixture's save/persistence work is stable; don't inject
  // movement keys while an earlier F5 operation still owns the game loop.
  await page.waitForSelector('#fixture-finished', { state: 'attached', timeout: 260000 });
  await page.waitForTimeout(300);
  const stableBefore = await waitForStableSave(30000);
  const before = await snapshotSave();
  before.saveTiming = { preMovementSaveSettled: stableBefore.settled, preMovementSaveWaitMs: stableBefore.elapsedMs,
    persistenceFlags: stableBefore.flags };
  fs.writeFileSync(path.join(out, 'before-movement-save-snapshot.json'), JSON.stringify(before, null, 2));
  await page.locator('#canvas').focus().catch(() => {});

  // Capture the actual compositor output through Playwright; WebGL
  // canvas.toDataURL() can be black when preserveDrawingBuffer is disabled.
  // Both A/B screenshots use this same static capture, scaled to 75%.
  const canvasSource = path.join(out, 'canvas-scale-source.png');
  await page.locator('#canvas').screenshot({ path: canvasSource, animations: 'disabled', timeout: 30000 });
  const sourceData = fs.readFileSync(canvasSource).toString('base64');
  const comparisonPage = await context.newPage();
  await comparisonPage.setContent('<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:#111;display:grid;place-items:center;width:100%;height:100%;overflow:hidden}img{width:1024px;height:576px;object-fit:fill}</style><img id="comparison" alt="static game canvas snapshot" src="data:image/png;base64,' + sourceData + '">');
  await comparisonPage.locator('#comparison').evaluate(el => el.style.setProperty('image-rendering', 'pixelated', 'important'));
  await comparisonPage.screenshot({ path: path.join(out, 'text-scale-pixelated.png'), animations: 'disabled' });
  await comparisonPage.locator('#comparison').evaluate(el => el.style.setProperty('image-rendering', 'auto', 'important'));
  await comparisonPage.screenshot({ path: path.join(out, 'text-scale-auto.png'), animations: 'disabled' });
  const visualScaleComparison = {
    source: path.basename(canvasSource), captured: ['text-scale-pixelated.png', 'text-scale-auto.png'],
    viewportScale: 1024 / 1365,
    note: 'Both screenshots display the same static canvas screenshot at 75% scale; only CSS interpolation differs.'
  };
  await comparisonPage.close();
  const start = await page.evaluate(() => ({ at: performance.now(), task: window.__cddaMeasure.tasks.length, key: window.__cddaMeasure.keys.length }));
  const movementStarted = now();
  await page.keyboard.down('l');
  for (let i = 0; i < 18; i++) { await pause(80); await page.keyboard.down('l'); }
  await page.keyboard.up('l');
  await pause(500);
  const movementMs = Number((now() - movementStarted).toFixed(2));
  const end = await page.evaluate(() => ({ at: performance.now(), task: window.__cddaMeasure.tasks.length, key: window.__cddaMeasure.keys.length }));
  const after = await quickSave('after-movement', before);
  await page.screenshot({ path: path.join(out, 'after-long-press-page.png'), fullPage: true, animations: 'disabled' }).catch(error => consoleLog.push({ type: 'screenshot-error', text: String(error) }));

  const runtime = await page.evaluate(marks => {
    const state = window.__cddaMeasure, tasks = state.tasks, keys = state.keys;
    const intervalsInRange = (fromAt, toAt) => {
      const intervals = [];
      for (let i = 1; i < state.frameTimes.length; i++) {
        const from = state.frameTimes[i - 1], to = state.frameTimes[i];
        if (from >= fromAt && to <= toAt) intervals.push(to - from);
      }
      return intervals;
    };
    const wholeFrames = intervalsInRange(-Infinity, performance.now());
    const movementFrames = intervalsInRange(marks.start.at, marks.end.at);
    const movementTasks = tasks.filter(task => task.start >= marks.start.at && task.start <= marks.end.at);
    const movementKeys = keys.filter(item => item.at >= marks.start.at && item.at <= marks.end.at && (item.key === 'l' || item.key === 'L'));
    const latency = movementKeys.filter(item => Number.isFinite(item.frameMs)).map(item => item.frameMs);
    return { rafIntervalCount: wholeFrames.length, rafCallbackIntervalMs: frameStatsInPage(wholeFrames),
      longTaskCount: tasks.length, longTaskMs: { total: Number(tasks.reduce((a, b) => a + b.duration, 0).toFixed(2)),
        max: tasks.length ? Number(Math.max(...tasks.map(t => t.duration)).toFixed(2)) : null, over100: tasks.filter(t => t.duration > 100).length },
      movement: { rafIntervalCount: movementFrames.length, rafCallbackIntervalMs: frameStatsInPage(movementFrames), longTaskCount: movementTasks.length,
        longTaskTotalMs: Number(movementTasks.reduce((a, b) => a + b.duration, 0).toFixed(2)),
        keyEvents: movementKeys.length, keydowns: movementKeys.filter(item => item.type !== 'keyup').length,
        keydownToNextFrameMs: { p50: percentileInPage(latency, .50), p95: percentileInPage(latency, .95), max: latency.length ? Number(Math.max(...latency).toFixed(2)) : null } },
      jsHeap: performance.memory ? { usedBytes: performance.memory.usedJSHeapSize, totalBytes: performance.memory.totalJSHeapSize, limitBytes: performance.memory.jsHeapSizeLimit } : null };
    function percentileInPage(values, q) { const sorted = [...values].sort((a, b) => a - b); return sorted.length ? Number(sorted[Math.min(sorted.length - 1, Math.ceil(q * sorted.length) - 1)].toFixed(2)) : null; }
    function frameStatsInPage(values) { return { count: values.length, p50: percentileInPage(values, .50), p95: percentileInPage(values, .95), p99: percentileInPage(values, .99),
      max: values.length ? Number(Math.max(...values).toFixed(2)) : null, over50: values.filter(v => v > 50).length, over100: values.filter(v => v > 100).length, over250: values.filter(v => v > 250).length }; }
  }, { start, end });

  const fixtureResult = captured.result;
  let savedFiles = [];
  if (fixtureResult?.saves) savedFiles = Object.entries(fixtureResult.saves).map(([name, values]) => {
    const bytes = Buffer.from(values);
    return { name, bytes: bytes.length, sha256: crypto.createHash('sha256').update(bytes).digest('hex') };
  });
  let preInterruptScreenshot = null;
  if (typeof fixtureResult?.beforeInterrupt === 'string' && /^[a-z0-9_-]+\.png$/i.test(fixtureResult.beforeInterrupt)) {
    preInterruptScreenshot = fixtureResult.beforeInterrupt;
  } else if (fixtureResult?.beforeInterrupt?.startsWith('data:image/png;base64,')) {
    preInterruptScreenshot = 'before-interruption.png';
    fs.writeFileSync(path.join(out, preInterruptScreenshot), Buffer.from(fixtureResult.beforeInterrupt.slice(22), 'base64'));
  }
  const assets = ['index.html', 'cataclysm-tiles.js', 'cataclysm-tiles.wasm', 'cataclysm-tiles.data', 'cataclysm-tiles.data.js', 'Terminus.ttf', 'favicon.ico'];
  const bundleFiles = assets.map(name => {
    const file = path.join(bundle, name);
    return fs.existsSync(file) ? { name, bytes: fs.statSync(file).size } : { name, missing: true };
  });
  const result = { schemaVersion: 1, measuredAt: new Date().toISOString(), targetUrl: baseUrl, browserVersion: browser.version(),
    nodeVersion: process.version, scenario, renderer, bundleFiles, bootToNewGameMs: bootMs, newGameMessage, display,
    persistenceInstrumentation,
    movement: { elapsedMs: movementMs, beforeSave: before, afterSave: after,
      turnDelta: before.available && after.available && Number.isFinite(before.turn) && Number.isFinite(after.turn) ? after.turn - before.turn : null,
      changedPositionFields: before.available && after.available ? Object.keys(after.position || {}).filter(key => JSON.stringify(before.position?.[key]) !== JSON.stringify(after.position?.[key])) : [],
      note: 'Missing save snapshots are unavailable, not zero movement.' },
    runtime, visualScaleComparison, fixture: fixtureResult ? { mode: fixtureResult.mode, variant: fixtureResult.variant, renderer: fixtureResult.renderer,
      heapBytes: fixtureResult.heap, error: fixtureResult.error || '', reading: fixtureResult.reading || null, savedFiles,
      beforeInterruptScreenshot: preInterruptScreenshot ? path.basename(preInterruptScreenshot) : null } : null,
    capturedScreens: captured.screens, fixtureScreens, captureResultReceived: !!fixtureResult, captureOther: captured.other, pageErrors, requestFailures, console: consoleLog };
  fs.writeFileSync(path.join(out, 'measurement.json'), JSON.stringify(result, null, 2));
  const movementText = result.movement.turnDelta === null ? 'unavailable (no parseable save delta)' :
    'turn delta ' + result.movement.turnDelta + '; position fields changed: ' + (result.movement.changedPositionFields.join(', ') || 'none observed');
  const reading = result.fixture?.reading ? 'turns ' + result.fixture.reading.gameSecondsBetweenSaves + '; observation ' +
    Math.round(result.fixture.reading.observationMs) + ' ms; reached level 5: ' + result.fixture.reading.reachedLevel5 : 'not applicable';
  const m = runtime.movement, f = m.rafCallbackIntervalMs;
  const summary = ['# CDDA Playwright browser measurement', '', '- Target: ' + baseUrl, '- Captured: ' + result.measuredAt,
    '- Scenario/renderer: ' + scenario + ' / ' + renderer, '- Browser version: ' + result.browserVersion,
    '- Boot to new-game log: ' + bootMs + ' ms', '- Canvas: ' + JSON.stringify(display.canvas),
    '- HTML Terminus font check: ' + display.fonts.terminusCheck, '- Movement: ' + movementText,
    '- Pre-movement save stability: ' + JSON.stringify(result.movement.beforeSave?.saveTiming || {}),
    '- F5 to observed save-file update ms: ' + result.movement.afterSave?.saveTiming?.f5ToFileUpdateMs + (result.movement.afterSave?.saveTiming?.fileUpdated ? '' : ' (no file update observed; censored at timeout)'),
    '- IDBFS sync hook: ' + JSON.stringify(persistenceInstrumentation) + '; post-movement syncs: ' + JSON.stringify(result.movement.afterSave?.saveTiming?.idbfsSyncsAfterCommand || []),
    '- Save RAF intervals/Long Tasks: ' + JSON.stringify(result.movement.afterSave?.saveTiming?.frameEffects || {}),
    '- Reading result: ' + reading,
    '- Movement RAF callback intervals p50/p95/p99/max ms: ' + [f.p50, f.p95, f.p99, f.max].join(' / '),
    '- Movement RAF callback intervals >50/>100/>250 ms: ' + [f.over50, f.over100, f.over250].join(' / '),
    '- Input-to-next-frame p50/p95/max ms: ' + [m.keydownToNextFrameMs.p50, m.keydownToNextFrameMs.p95, m.keydownToNextFrameMs.max].join(' / '),
    '- Whole-run Long Tasks: ' + runtime.longTaskCount + ', total ' + runtime.longTaskMs.total + ' ms, max ' + runtime.longTaskMs.max + ' ms',
    '- Canvas captures: ' + captured.screens.length + '; page errors: ' + pageErrors.length,
    '- Text scaling A/B: same static canvas image at 75% CSS size, pixelated vs auto interpolation',
    '- Playwright fixture screenshots: ' + fixtureScreens.length,
    '', '## Interpretation limits', '', '- GitHub Actions values are not a physical 4GB Chromebook benchmark.',
    '- RAF intervals are callback-to-callback timing, not a guaranteed display-FPS metric; idle waits and activity modals can create long gaps.',
    '- Long Task time includes browser runtime, game processing, drawing, and measurement overhead; it is not pure turn CPU time.',
    '- JS heap is not Wasm heap or total browser RAM; RSS/PSS is sampled separately.',
    '- Movement is confirmed only if before/after save data is available.', '- Canvas images require visual review; UTF-8/font resource success does not prove glyph correctness.',
    '', 'Full JSON: measurement.json', ''].join('\n');
  fs.writeFileSync(path.join(out, 'summary.md'), summary);
  console.log(summary);
  if (bundleFiles.some(file => file.missing)) throw new Error('Missing one or more deployed bundle assets');
  if (!fixtureResult) throw new Error('No game fixture result payload');
  if (pageErrors.length) throw new Error('Browser page errors: ' + pageErrors.length);
}

try { await main(); }
catch (error) {
  let browserDiagnostics = null;
  if (page) {
    browserDiagnostics = await page.evaluate(() => {
      const canvas = document.querySelector('canvas');
      const loading = document.querySelector('#loading-message');
      const fatal = document.querySelector('#error-overlay');
      const detail = document.querySelector('#error-detail');
      const module = window.Module || null;
      return {
        href: location.href,
        title: document.title,
        readyState: document.readyState,
        bodyText: (document.body?.innerText || '').slice(0, 5000),
        loadingText: loading?.textContent || null,
        loadingDisplay: loading ? getComputedStyle(loading).display : null,
        errorOverlayDisplay: fatal ? getComputedStyle(fatal).display : null,
        fatalDetail: detail?.textContent || null,
        canvas: canvas ? { width: canvas.width, height: canvas.height,
          rect: (() => { const r = canvas.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; })(),
          imageRendering: getComputedStyle(canvas).imageRendering } : null,
        modulePresent: !!module,
        moduleRuntimeInitialized: module?.calledRun ?? null,
        moduleDependencies: module?.totalDependencies ?? null,
        wasmMemoryBytes: module?.HEAPU8?.length ?? window.HEAPU8?.length ?? null,
        fsPresent: !!(module?.FS || window.FS),
        fixtureFinished: !!document.querySelector('#fixture-finished')
      };
    }).catch(diagError => ({ evaluateError: String(diagError) }));
    await page.screenshot({ path: path.join(out, 'failure-page.png'), fullPage: true, timeout: 10000 }).catch(() => {});
    await page.content().then(html => fs.writeFileSync(path.join(out, 'failure-page.html'), html)).catch(() => {});
  }
  const payload = { at: new Date().toISOString(), scenario, renderer, baseUrl,
    error: String(error?.stack || error), browserDiagnostics, console: consoleLog, pageErrors, requestFailures,
    capturedScreens: captured.screens, captureResultReceived: !!captured.result };
  fs.writeFileSync(path.join(out, 'failure.json'), JSON.stringify(payload, null, 2));
  console.error('BROWSER FAILURE DIAGNOSTICS ' + JSON.stringify(payload, null, 2));
  process.exitCode = 1;
} finally {
  if (newGameTimer) clearTimeout(newGameTimer);
  if (context && tracing) await context.tracing.stop({ path: path.join(out, 'playwright-trace.zip') }).catch(() => {});
  if (browser) await browser.close().catch(() => {});
  await new Promise(resolve => server.close(() => resolve())).catch(() => {});
}
