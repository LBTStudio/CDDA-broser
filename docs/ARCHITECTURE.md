# Architecture

CDDA 0.I (2026-06-06) still carries the SDL2-based Emscripten target in its Makefile
(-sUSE_SDL=2 / SDL2_image / SDL2_ttf, ASYNCIFY, IDBFS, ALLOW_MEMORY_GROWTH to 4GB).
Upstream marked the wasm target unsupported on 2026-08-12 (commit 080986b0, master only)
after moving to SDL3. The 0.I tag predates that change, so `emsdk 3.1.51` builds it as-is.

Pipeline (all in CI, nothing large is committed to the repo):
1. actions/checkout of CleverRaven/Cataclysm-DDA @ 0.I
2. apply the Emscripten-only patches in `patches/` (see below)
3. emsdk 3.1.51
4. make NATIVE=emscripten TILES=1 RELEASE=1 cataclysm-tiles.js  ->  .js glue + ~45MB .wasm
5. build-scripts/prepare-web.sh  ->  LZ4 data bundle (~68MB) + build/ site layout
6. shell/index.html (profile-aware) replaces the upstream build/index.html
7. deploy to GitHub Pages (skippable via the `deploy_pages` workflow input,
   so a build can be verified from the artifact without touching the live site)

## Patches (all guarded by `#if defined(EMSCRIPTEN)`, native builds unchanged)

| patch | purpose |
|---|---|
| `mo-reader` | MEMFS cannot mmap LZ4-preloaded files; read MO catalogs into owned memory |
| `loader-yield` | yield to the browser every 50ms while iterating core/mod JSON files |
| `mod-finalize-yield` | same 50ms budget for mod interactions, per-object JSON array dispatch, finalization and verification passes (the phases that run during world creation) |
| `world-yield` | yields in `start_game` / `overmap::generate` / `place_specials` / mapgen so the deepest post-worldgen call chain cannot hold the main thread; additionally a throttled (50ms) yield inside `input_manager::pump_events` (SDL build) turns every upstream cooperative marker — map/overmap saving, JSON verification, world serialization, submap generation — into a real browser yield, which removes the "wait or leave page" dialog during post-worldgen finalization |
| `idbfs-debounce` | mount IDBFS at the profile's real user dir (`$HOME/.cataclysm-dda`, resolved at runtime) instead of the hardcoded `/home/web_user`; coalesce persistence into one debounced (250ms), serialized `FS.syncfs` instead of one transaction per file mutation; force-flush on `pagehide` / `visibilitychange: hidden` so ChromeOS tab discard cannot drop the last writes; expose a `window.CDDA_ON_IDBFS_MOUNTED(mountPoint)` hook so the shell can migrate config *after* the mount (an IDBFS mount shadows anything written there during preRun); `window.setFsNeedsSync` is defined *before* the first `await` because `filesystem.cpp` invokes it on every file mutation and character creation writes files while the initial restore is still pending (calling an undefined function was the "Exception thrown, see JavaScript console" hang); a failed IndexedDB mount now degrades to MEMFS and notifies the shell via `window.CDDA_ON_IDBFS_ERROR` instead of halting the game with an uncaught rejection |

## Memory / stack policy for 4GB devices (applied to the Makefile in CI)

- `INITIAL_MEMORY 512MB -> 256MB`: measured floor for reaching the Japanese menu.
- `MAXIMUM_MEMORY 4GB -> 2GB`: with ALLOW_MEMORY_GROWTH this is a cap, not
  committed memory. A 1GB cap was tried first and made growth fail exactly
  during world generation (the peak-memory moment), which surfaced as a
  frozen/blank worldgen menu. 2GB keeps idle usage identical and gives the
  generation spike headroom while still protecting the device from runaway growth.
- `ASYNCIFY_STACK_SIZE 16KB -> 4MB`: the yield patches call `emscripten_sleep()`
  from deep call chains (overmap specials, mapgen). Asyncify unwinds every live
  frame into this fixed buffer; 16KB overflows there and corrupts state silently.
- `STACK_SIZE 256KB -> 1MB`: headroom for mapgen recursion on the native stack.
- Link stays at `-O0` (sources still `-O3`): avoids the multi-hour
  binaryen/JS-optimizer stage that made CI unreliable; wasm-opt is skipped.

Per-player save isolation:
The game resolves its user dir as getenv("HOME") + "/.cataclysm-dda/"
(src/path_info.cpp, init_user_dir). The shell sets ENV.HOME=/home/<profile>
during preRun, and the `idbfs-debounce` patch makes the IDBFS mount follow
that same HOME instead of the upstream hardcoded `/home/web_user/.cataclysm-dda`.
Before this fix the two paths disagreed, so per-profile saves landed on volatile
MEMFS and were silently lost on reload. Now each player name gets an independent
subtree on the IDBFS-backed persistent filesystem (IndexedDB), config migration
runs via `CDDA_ON_IDBFS_MOUNTED` after the mount (so it is not shadowed), and
the profile list lives in localStorage.

Error reporting: the shell never uses blocking `alert()`. Runtime aborts,
uncaught exceptions, unhandled rejections and WebGL context loss all render a
dismissable overlay with a Japanese/English recovery hint (memory-pressure
failures get a "close other tabs" hint), keeping the first root cause visible.

Runtime: ~350-550MB steady state, WebGL2, works in stock ChromeOS Chrome
(no Crostini/Linux container required).
