#!/usr/bin/env node
import assert from 'node:assert/strict';
import {
  ACTIVE_MOVEMENT_STALL_LIMIT_MS,
  assertMovementResponsive,
  createBrowserHealth
} from './browser_health.mjs';

const healthy = createBrowserHealth();
assert.deepEqual(healthy.snapshot(), { pageCrashCount: 0, pageCrashes: [] });
assert.doesNotThrow(() => healthy.assertNoPageCrash());

// The crash must remain fatal even if the page's startup wait already resolved.
const crashed = createBrowserHealth();
crashed.notePageCrash(123.5);
crashed.notePageCrash(456.25, 'renderer exited');
assert.equal(crashed.snapshot().pageCrashCount, 2);
assert.throws(() => crashed.assertNoPageCrash(), /Chromium page crashed 2 time\(s\)/);
assert.deepEqual(crashed.snapshot().pageCrashes, [
  { atMs: 123.5, message: 'Chromium page crashed' },
  { atMs: 456.25, message: 'renderer exited' }
]);

assert.equal(ACTIVE_MOVEMENT_STALL_LIMIT_MS, 5000);
assert.doesNotThrow(() => assertMovementResponsive({ count: 4, max: 4999.99 }));
assert.throws(
  () => assertMovementResponsive({ count: 4, max: ACTIVE_MOVEMENT_STALL_LIMIT_MS }),
  /Severe main-thread stall during active movement/
);
assert.throws(
  () => assertMovementResponsive({ count: 0, max: null }),
  /No RAF intervals observed/
);

console.log('PASS browser health guards: persistent crash failure, severe movement stall, and invalid RAF samples');
