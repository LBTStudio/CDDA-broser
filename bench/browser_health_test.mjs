#!/usr/bin/env node
import assert from 'node:assert/strict';
import {
  ACTIVE_MOVEMENT_STALL_LIMIT_MS,
  assertMovementResponsive,
  createBrowserHealth
} from './browser_health.mjs';

const healthy = createBrowserHealth();
assert.deepEqual(healthy.snapshot(), { pageCrashCount: 0, pageCrashes: [], browserDisconnectCount: 0, browserDisconnects: [] });
assert.doesNotThrow(() => healthy.assertNoBrowserFailure());

// The crash must remain fatal even if the page's startup wait already resolved.
const crashed = createBrowserHealth();
crashed.notePageCrash(123.5);
crashed.notePageCrash(456.25, 'renderer exited');
assert.equal(crashed.snapshot().pageCrashCount, 2);
assert.throws(() => crashed.assertNoBrowserFailure(), /Chromium page crashed 2 time\(s\)/);
assert.deepEqual(crashed.snapshot().pageCrashes, [
  { atMs: 123.5, message: 'Chromium page crashed' },
  { atMs: 456.25, message: 'renderer exited' }
]);
const disconnected = createBrowserHealth();
disconnected.noteBrowserDisconnect(789, 'Chromium process exited');
assert.equal(disconnected.snapshot().browserDisconnectCount, 1);
assert.throws(() => disconnected.assertNoBrowserFailure(), /Chromium browser disconnected unexpectedly/);

assert.equal(ACTIVE_MOVEMENT_STALL_LIMIT_MS, 5000);
assert.doesNotThrow(() => assertMovementResponsive({ count: 4, max: 4999.99, tickCount: 4 }));
assert.throws(
  () => assertMovementResponsive({ count: 4, max: ACTIVE_MOVEMENT_STALL_LIMIT_MS, tickCount: 4 }),
  /Severe main-thread stall during active movement/
);
assert.throws(
  () => assertMovementResponsive({ count: 0, max: null, tickCount: 0 }),
  /No RAF intervals observed/
);
assert.throws(
  () => assertMovementResponsive({ count: 1, max: 100, tickCount: 0 }),
  /No RAF intervals observed/
);

// Protect the measurement boundary logic: include the pre-first-frame and
// post-last-frame gaps, and expose the number of actual RAF callbacks.
import fs from 'node:fs';
const runnerSource = fs.readFileSync(new URL('./playwright_game_measurement.mjs', import.meta.url), 'utf8');
assert.match(runnerSource, /state\.frameTimes\.push\(state\.frameMonitorStartAt\)/);
assert.match(runnerSource, /frameTimes: \[\.\.\.state\.frameTimes, at\]/);
assert.match(runnerSource, /marks\.start\.frameStartAt/);
assert.match(runnerSource, /tickCount: marks\.end\.frameMonitorTickCount/);

console.log('PASS browser health guards: persistent crash failure, severe movement stall, and invalid RAF samples');
