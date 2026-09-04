# Architecture

CDDA 0.I (2026-06-06) still carries the SDL2-based Emscripten target in its Makefile
(-sUSE_SDL=2 / SDL2_image / SDL2_ttf, ASYNCIFY, IDBFS, ALLOW_MEMORY_GROWTH to 4GB).
Upstream marked the wasm target unsupported on 2026-08-12 (commit 080986b0, master only)
after moving to SDL3. The 0.I tag predates that change, so `emsdk 3.1.51` builds it as-is.

Pipeline (all in CI, nothing large is committed to the repo). Since PR #6 the
workflow is a 6-job DAG instead of one serial job — see "CI topology" below:

```
    plan ──┬──> compile (N 並列 / matrix) ──> link ──┐
           │                                         ├──> bundle ──> deploy
           └──> data ────────────────────────────────┘
```

1. `plan`   — checkout CleverRaven/Cataclysm-DDA @ 0.I, apply `patches/`,
              tune the Makefile, then split `make print-OBJS` (440 TUs) into
              N round-robin shards and emit them as a job matrix
2. `compile`— N parallel jobs, each building only its own shard's `.o`
              (emsdk 3.1.51 + ccache, uploaded as `objs-shard-<i>`)
3. `data`   — runs *in parallel with compile/link* because the LZ4 data
              package does not depend on `.js`/`.wasm`: localization,
              data/gfx collection, obsolete-mod removal, `file_packager --lz4`
4. `link`   — download all shards, reconcile timestamps, then
              `make NATIVE=emscripten TILES=1 RELEASE=1 cataclysm-tiles.js`
              -> `.js` glue + ~45MB `.wasm`. **Not parallelizable** (see below)
5. `bundle` — assemble `build/` from the link + data artifacts and overwrite
              the upstream `index.html` with the profile-aware `shell/index.html`
6. `deploy` — GitHub Pages (skippable via the `deploy_pages` workflow input,
              so a build can be verified from the artifact without touching
              the live site)

## CI topology (`ci/*.sh` + `manual/build-and-release.yml`)

A single-job build took ~4 hours. The work splits into a parallelizable part
and a hard floor:

- **Parallelizable — compile.** `make print-OBJS` yields **440 translation
  units**, each independent. `ubuntu-latest` is 4 vCPU, so N matrix jobs give
  effectively 4×N compile threads. Measured split at N=8: **55 TUs per shard**.
  Shards are assigned **round-robin (index % N)**, not contiguous blocks — the
  sorted object list clusters same-prefix heavyweights (`mapgen*`, `monster*`)
  and contiguous slicing would leave one shard dominating total wall time.
- **Parallelizable — data packing.** The LZ4 package depends only on
  `data/`, `gfx/` and the `.mo` catalogs, never on `.js`/`.wasm`, so `data`
  runs concurrently with `compile`+`link` instead of after them.
- **Not parallelizable — link.** `LDFLAGS` carry `-Os` and `-sASYNCIFY`.
  Asyncify does a **whole-module call-graph analysis** to insert unwind/rewind
  state machines, and `wasm-opt` is single-threaded. There is no shardable
  unit of work here, so **link time is the theoretical floor** of this
  workflow. This is documented honestly rather than papered over.

Supporting scripts, all with Japanese comments:

| script | role |
|---|---|
| `ci/make-args.sh` | single source of truth for the make variables (`NATIVE`, `TILES`, `RELEASE`, `LOCALIZE`, `CCACHE=1`, …). Every job sources it, because a variable divergence between the compile and link jobs breaks the build in ways that surface late (`TILES` changes `IMGUI_SOURCES` -> undefined symbols at link; `RELEASE` changes `OPTLEVEL`/`-DRELEASE` -> PCH mismatch at compile) |
| `ci/shard-plan.sh` | runs `make print-OBJS` and writes `objs-all.txt`, `shard-<i>.txt`, `matrix.json`. Never uses `ls src/*.cpp`: `SOURCES += $(THIRD_PARTY_SOURCES)` (flatbuffers/zstd) and the SDL-conditional `$(IMGUI_SOURCES)` would be missed |
| `ci/progress.sh` | the progress bar (see below) |
| `ci/build-shard.sh` | per-shard compile: `make version` -> PCH -> `make -j4 <shard objects>` under the progress monitor -> per-object `[ -s ]` verification -> ccache stats |
| `ci/link.sh` | completeness check (distinguishing *missing* from *zero-byte*, i.e. an OOM-killed compile), **timestamp reconciliation**, then the link |
| `ci/prepare-data.sh` | localization + data/gfx collection + obsolete-mod removal (`jq` on `MOD_INFO.obsolete`) + bundled `mods/` + ja `.mo` + `file_packager --lz4` |
| `ci/prepare-bundle.sh` | the join point: validates the 7 inputs (each labeled with the job that should have produced it), assembles `build/`, re-verifies the 7 outputs |

Two non-obvious correctness requirements, both of which were caught by
rehearsing the workflow locally rather than by a failing build:

- **PCH is not distributed.** The precompiled header is 19MB and bakes in
  absolute paths, the compiler version and `__OPTIMIZE__`; shipping it between
  jobs produces a hard mismatch error. Generating it costs only ~5s, so every
  shard builds its own.
- **Timestamps must be reconciled before linking.** Restored `.o` files are
  *older* than the freshly checked-out `.cpp` files, so make would consider all
  440 objects stale and recompile them in the link job — the failure mode is
  "slow but succeeds", which is exactly why it can go unnoticed and silently
  negate the whole sharding effort. `ci/link.sh` therefore touches the PCH,
  `sleep 1` (mtime granularity can be 1s), then touches every `.o`, and
  asserts with a `make -q` sample. Measured: `make -q` returned 1 before the
  fix and 0 after.

Caching: ccache is enabled (`CCACHE=1`) and persisted with `actions/cache`.
Measured on a 2-TU shard: **7s -> 0s** with 2/2 hits. The cache key includes
`github.run_id` plus `restore-keys`, because `actions/cache` refuses to
overwrite an existing key — without the run id the first-saved cache would be
reused forever and never grow.

## Build progress bar

The workflow prints a live progress bar for the long phases (compile per
shard, data packing, link). GitHub Actions logs are **append-only**, so `\r`
in-place updates do not render; the monitor emits one new bar line per
interval instead:

```
[PROGRESS] compile shard-2 [############------------------]  40% ( 22/ 55) 経過 3m12s 残り推定 4m48s
```

Progress is measured **externally**, by counting how many of the shard's
expected `.o` files exist (`ci/progress.sh:count_existing_objects`), rather
than by parsing make's output — make's line volume is not proportional to
work, and `make -n` pre-counting doubles the analysis cost. The counter is a
pure-bash loop because `[ -f ]` is a shell builtin (zero forks), so 440
iterations are cheaper than one external process. The monitor subshell starts
with `set +e; set +o pipefail` so that instrumentation can never fail the
build — an early version inherited `pipefail`, and a `cmd | ... || echo 0`
fallback appended `0` to already-emitted output, producing
`[: 1\n0: integer expression expected`.

Each job also appends a row to `$GITHUB_STEP_SUMMARY`, so the slowest shard is
visible at a glance without opening individual logs.

## Patches (all guarded by `#if defined(EMSCRIPTEN)`, native builds unchanged)

| patch | purpose |
|---|---|
| `mo-reader` | MEMFS cannot mmap LZ4-preloaded files; read MO catalogs into owned memory. Also hardens the mmap layer for the browser: (1) MEMFS `mmap` throws a JS `TypeError` ("Cannot read properties of null (reading 'buffer')") for never-written files instead of failing like POSIX, so `map_view()` returns `false` for zero-length files on EMSCRIPTEN — callers then take their existing empty-file path (`base=nullptr, len=0`), from which zzip regenerates the footer and resizes the file; (2) `WORLD_COMPRESSION2` defaults to `false` on EMSCRIPTEN (writeable mmap views are heap-emulated on MEMFS — slow and fragile on 4GB devices; the option itself is untouched and can still be enabled per world); (3) `zzip.cpp` null-guards the dictionary `map_file()` result before dereferencing. Together these fix the post-character-creation crash `TypeError ... at Object.mmap` seen when a freshly created, still-empty compressed save (`.zzip`) was mapped |
| `loader-yield` | yield to the browser every 50ms while iterating core/mod JSON files |
| `mod-finalize-yield` | same 50ms budget for mod interactions, per-object JSON array dispatch, finalization and verification passes (the phases that run during world creation) |
| `world-yield` | yields in `start_game` / `overmap::generate` / `place_specials` / mapgen so the deepest post-worldgen call chain cannot hold the main thread; a throttled yield inside `input_manager::pump_events` (SDL build) turns every upstream cooperative marker — map/overmap saving, JSON verification, world serialization, submap generation — into a real browser yield, which removes the "wait or leave page" dialog during post-worldgen finalization (throttle raised 50ms→250ms: each yield is a full Asyncify stack round trip and showed up as per-turn overhead during map movement); ui_manager's unconditional post-redraw `emscripten_sleep(1)` is throttled to ~30fps pacing for the same reason (the input wait loop's `SDL_Delay(1)` already yields after each keypress redraw, so responsiveness is unaffected); carries the web IME bridge in `sdltiles.cpp` — see "Japanese IME input" below — now including string-input-context gating: `input_context` registers itself on the (previously Android-only) `input_context_stack` on EMSCRIPTEN too, and `StartTextInput()` engages the browser IME proxy only when the top-of-stack category is `STRING_INPUT` / `STRING_EDITOR` / `HELP_KEYBINDINGS` (mirroring Android's `is_string_input`), so an active Japanese IME can no longer swallow movement keys during normal play |
| `json-cache` | the stock flexbuffer disk caches (`<base>/cache`, `<data>/cache`) live in MEMFS on the web and vanish every page load, so every start re-parsed ~60MB of core JSON during "core loading / finalization"; redirect the base/data caches to `<user_dir>/json_cache/<data-package-tag>/` (inside the IDBFS mount, persisted like saves), run them in a new `assume_immutable_root` mode (packaged files get fresh, meaningless mtimes each load, so mtime staleness checks are skipped), and version the directory by the data package's HTTP ETag (`CDDA_DATA_VERSION` env exported by the shell; falls back to the game version string) — a game update starts a fresh cache and older tag directories are removed to bound IndexedDB usage |
| `activity-and-ime` | **must be applied last** (it touches files the other patches also touch). Two halves. (1) *Activity throughput* — the yield primitives are centralized in new `src/cata_web_yield.{h,cpp}` and every ad-hoc `emscripten_sleep` site is converted to them, so a single policy governs how often the browser is given control. Measurement showed the dominant per-turn cost during long multi-turn activities (pulping, reading, crafting) was **`g->mon_info_update()` at 0.6170ms of a 0.8600ms turn = 71.7%**; it is now throttled to once per 16 game turns via `mon_info_update_throttled( bool force )`, with `force = !u.activity` so interactive play is untouched and only unattended activities are decimated. The yield interval likewise switches 16ms/100ms depending on whether an activity is running. Measured 31% faster reading/crafting. (2) *Text input* — new `src/cata_web_text_input.{h,cpp}` plus the `input_popup`/`string_input_popup`/`string_editor_window` changes make search-type fields report themselves as text-input contexts, which is what actually lets the browser IME reach them (see "Japanese IME input" below) |
| `idbfs-debounce` | mount IDBFS at the profile's real user dir (`$HOME/.cataclysm-dda`, resolved at runtime) instead of the hardcoded `/home/web_user`; coalesce persistence into one debounced (250ms), serialized `FS.syncfs` instead of one transaction per file mutation; force-flush on `pagehide` / `visibilitychange: hidden` so ChromeOS tab discard cannot drop the last writes; expose a `window.CDDA_ON_IDBFS_MOUNTED(mountPoint)` hook so the shell can migrate config *after* the mount (an IDBFS mount shadows anything written there during preRun); `window.setFsNeedsSync` is defined *before* the first `await` because `filesystem.cpp` invokes it on every file mutation and character creation writes files while the initial restore is still pending (calling an undefined function was the "Exception thrown, see JavaScript console" hang); a failed IndexedDB mount now degrades to MEMFS and notifies the shell via `window.CDDA_ON_IDBFS_ERROR` instead of halting the game with an uncaught rejection |

## Memory / stack policy for 4GB devices (applied to the Makefile in CI)

- `INITIAL_MEMORY 512MB -> 256MB`: measured floor for reaching the Japanese menu.
- `MAXIMUM_MEMORY 4GB -> 2GB`: with ALLOW_MEMORY_GROWTH this is a cap, not
  committed memory. A 1GB cap was tried first and made growth fail exactly
  during world generation (the peak-memory moment), which surfaced as a
  frozen/blank worldgen menu. 2GB keeps idle usage identical and gives the
  generation spike headroom while still protecting the device from runaway growth.
- `ASYNCIFY_STACK_SIZE 16KB -> 16MB`: the yield patches call `emscripten_sleep()`
  from deep call chains (overmap specials, mapgen). Asyncify unwinds every live
  frame into this fixed buffer; 16KB overflows there and corrupts state silently,
  and an exhausted unwind buffer surfaces as a SIGILL-style trap in the loading
  phases right after world/character creation (4MB was close to the observed
  peak; 16MB is 0.8% of the 2GB cap and puts the limit far above it).
- `STACK_SIZE 256KB -> 4MB`: wasm stack exhaustion during mapgen recursion is
  the other SIGILL-style trap; 4MB of one-time heap removes it with margin.
- Link at upstream's `-Os`: an `-O0` link was used previously to skip the
  long Binaryen stage, but it leaves the Asyncify instrumentation
  unoptimized — measurably sluggish gameplay on 4GB devices — and roughly
  doubles the wasm download. `-Os` matches upstream's shipped web builds;
  the CI heartbeat keeps the long link stage observable.
- Compile at `-O3` (upstream web default was `-Os`): map movement runs the
  mapgen/event/AI hot loops through this code and `-O3`'s vectorization and
  inlining reduce per-turn CPU. The size cost is bounded because the `-Os`
  link stage still runs Binaryen's size-aware whole-module optimizer, and
  the persistent asset cache makes the one-time download less critical. The
  workflow edits only the emscripten branch of the Makefile's OPTLEVEL
  selection (bounded sed range + awk assertion).

## In-browser mod support

- The shell can install mod zips at runtime: files are unpacked (JSZip, already
  bundled for save export) into `<HOME>/.cataclysm-dda/mods/<dir>/`, which is
  `PATH_INFO::user_moddir` — the game's standard user-mod search path — and
  lives inside the IDBFS mount, so mods persist like saves. The installer keys
  on `modinfo.json` locations inside the zip (works with or without a top-level
  folder, and with multi-mod bundles), skips path-traversal entries, and asks
  before overwriting. `Manage Mods` lists installed mods and removes them.
- `mods/` in this repo is copied into `data/mods/` at bundle time (workflow
  step). First bundled mod: `stats_through_kills` (modinfo.json byte-identical
  to 0.H). The mod's data files were removed from the 0.I tree, but the entire
  engine implementation (kill_tracker XP accrual, upgrade prompts, scores UI,
  `STATS_THROUGH_KILLS` external option) is still present and functional.

## Persistent asset cache (shell only, no engine change)

GitHub Pages serves everything with `cache-control: max-age=600` and no
immutable variant, so the regular HTTP cache re-downloads the ~120MB
(gzip-transfer) wasm+data pair on nearly every visit; low-disk Chromebooks
evict it even sooner. The shell therefore:
- stores `cataclysm-tiles.wasm` and `cataclysm-tiles.data` in Cache Storage
  (`cdda-assets-v1`), which has no header-driven expiry, and requests
  `navigator.storage.persist()` so the bucket (asset cache + IDBFS saves)
  survives disk pressure;
- revalidates with a conditional GET against the stored ETag on every boot:
  HTTP 304 (or a network failure = offline) boots from cache, anything else
  refreshes the cache — new deployments are picked up automatically with a
  one-`If-None-Match` roundtrip;
- hands the data package to the stock loader via `Module.getPreloadedPackage`
  (dropping its own reference so the buffer is not retained twice) and the
  wasm via `Module.instantiateWasm` + `WebAssembly.instantiateStreaming`,
  overlapping disk read/download with compilation;
- falls back per-asset to the stock XHR loader whenever Cache Storage,
  fetch-streaming, or the quota is unavailable, so the fast path can never
  make loading less reliable than before.

`-Werror` note: EM_ASM argument interpolation (`$0`/`$1`) trips
`-Wdollar-in-identifier-extension` under the project's `-Wpedantic -Werror`;
`cdda_pump_web_ime` is wrapped in a targeted
`#pragma clang diagnostic ignored` push/pop (compile-verified against
emsdk 3.1.51 with the exact CI flag set).

## Japanese IME input (web)

Browser IMEs compose only into focused editable DOM elements; SDL's canvas
never receives composition events, so IME text could not be typed at all.
Bridge (world-yield patch, sdltiles.cpp + shell):
- Game -> shell: `StartTextInput`/`StopTextInput` call
  `window.CDDA_SET_TEXT_INPUT(1/0)`; the shell focuses/blurs a tiny,
  effectively invisible `#ime-proxy` input, so the IME candidate window
  appears only while an in-game text field accepts characters.
- String-input-context gating: `get_input_event()` calls `StartTextInput()`
  on **every** input poll in keychar mode (the default), i.e. also during
  ordinary map movement. The first bridge version forwarded that signal
  unconditionally, so the proxy stayed focused during play and an active
  Japanese IME turned every movement key into a kana composition and
  swallowed it. Now `input_context` registers itself on the (previously
  Android-only) `input_context_stack` on EMSCRIPTEN too, and
  `cdda_web_wants_ime()` reports 1 only when the top-of-stack category is
  `STRING_INPUT` / `STRING_EDITOR` / `HELP_KEYBINDINGS` — the exact
  classification Android's `is_string_input()` uses. Notifications are
  edge-triggered (`cdda_web_notify_ime`) so the JS boundary is not crossed
  on every poll.
- Shell -> game: composition events fill `window.cddaImeState`
  ({commits[], preview}); `cdda_pump_web_ime()` (called at the top of
  `CheckMessages`) drains it and re-injects commits as `SDL_TEXTINPUT` and
  previews as `SDL_TEXTEDITING` via `SDL_PushEvent`, so `string_input_popup`
  and every other consumer works unmodified, including the composition
  preview (`edit`/`edit_refresh`) path.
- Commits are split at UTF-8 boundaries to fit SDL's fixed event payload;
  raw keydown/keyup are swallowed at capture phase while composing (Enter /
  arrows / Esc belong to the IME; keyCode 229 included), so candidate
  selection never leaks into the game. ASCII typing is untouched: the proxy
  lets those keys bubble to SDL's own window-level listeners.

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

## Playability criteria used for the performance work

All the turn-processing / loading work is measured against fixed, published
thresholds rather than "feels faster" (Nielsen, *Usability Engineering*, 1993):

| # | criterion | threshold |
|---|---|---|
| A | max gap between paints | <= 50ms |
| B | input response | <= 100ms |
| C | progress indicator update | <= 500ms |
| D | sustained frame rate | >= 20fps |

Raw measurements and the reasoning behind each conclusion are recorded in
`docs/measurements/FACTS.md` (facts F-01 … F-21) with the raw logs under
`docs/measurements/raw/` and the microbenchmarks under `bench/`. That file is
the intended shortcut for any future technical fact-checking: check it before
re-measuring.

Runtime: ~350-550MB steady state, WebGL2, works in stock ChromeOS Chrome
(no Crostini/Linux container required).
