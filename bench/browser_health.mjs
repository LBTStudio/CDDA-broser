// Runtime health guards shared by the Playwright measurement runner and unit tests.
export const ACTIVE_MOVEMENT_STALL_LIMIT_MS = 5000;

export function createBrowserHealth() {
  const pageCrashes = [];

  return {
    notePageCrash(atMs, message = 'Chromium page crashed') {
      pageCrashes.push({ atMs: Number(atMs), message: String(message) });
    },

    snapshot() {
      return {
        pageCrashCount: pageCrashes.length,
        pageCrashes: pageCrashes.map(crash => ({ ...crash }))
      };
    },

    assertNoPageCrash() {
      if (pageCrashes.length === 0) return;
      const last = pageCrashes[pageCrashes.length - 1];
      throw new Error(`Chromium page crashed ${pageCrashes.length} time(s); last at ${last.atMs} ms: ${last.message}`);
    }
  };
}

// This guard is intentionally scoped to the active input/movement window.
// A five-second RAF gap there is a severe stall, not an ordinary low frame rate.
export function assertMovementResponsive(frameStats, thresholdMs = ACTIVE_MOVEMENT_STALL_LIMIT_MS) {
  if (!frameStats || !Number.isFinite(frameStats.max) || frameStats.count < 1) {
    throw new Error('No RAF intervals observed during active movement; responsiveness measurement is invalid');
  }
  if (frameStats.max >= thresholdMs) {
    throw new Error(`Severe main-thread stall during active movement: maximum RAF interval ${frameStats.max} ms meets/exceeds ${thresholdMs} ms guard`);
  }
}
