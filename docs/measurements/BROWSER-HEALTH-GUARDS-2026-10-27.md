# Browser crash and severe-stall guards — 2026-10-27

## What the runtime measurement now fails on

- Any Chromium page-crash event is retained in the health state and checked again after the gameplay measurement. The crash event no longer relies solely on rejecting the startup promise, which may already have resolved.
- The active movement window must yield at least one valid independent RAF interval.
- A maximum RAF-monitor interval of 5,000 ms or longer during active movement fails the run as a severe main-thread stall. The measured interval and crash history are included in success output; failure output keeps the last runtime statistics and crash history for diagnosis.
- Existing unhandled page-error checks remain fatal.

## Scope and limitations

The five-second threshold is a severe-freeze guard for the controlled, foreground input/movement window, not a general performance budget. It does not assert that every frame gap over 50 or 100 ms is a freeze, and it does not claim to detect every hang during startup, saves, or long activities. Those phases already have independent step timeouts and performance records, and need separate scenario-specific criteria.

The GitHub Actions browser is not a physical 4GB Chromebook. A successful run means this controlled workload completed without a recorded page crash, invalid movement RAF sample, or five-second-or-longer monitored movement stall; it is not proof that all game states are crash-free.

## Regression test

`node bench/browser_health_test.mjs` unit-tests crash retention, fatal health assertions, the 5,000 ms boundary, and invalid RAF samples. It is part of `ci/apply-patches.sh`, so the runtime regression workflow validates this guard on every PR and push to `main`.
