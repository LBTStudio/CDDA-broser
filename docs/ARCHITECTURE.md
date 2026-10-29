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
              (boot package + split packs, see "Split data packs")
4. `link`   — download all shards, reconcile timestamps, then
              `make NATIVE=emscripten TILES=1 RELEASE=1 cataclysm-tiles.js`
              -> `.js` glue + ~45MB `.wasm`. **Not parallelizable** (see below)
5. `bundle` — assemble `build/` from the link + data artifacts and overwrite
              the upstream `index.html` with the profile-aware `shell/index.html`
              (plus `shell/lazydata.js` and `packs/`)
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
- **Not parallelizable, but not fixed either — link.** Measured from 2218 `ps`
  samples of the 4h23m run 33730439512: **`wasm-opt` alone is 4h02m = 93%** of
  the whole build, running at **1.05 of the 4 available cores** with a 6.46 GB
  peak RSS out of 16 GB. Upsizing the runner or adding RAM therefore cannot
  help, and `wasm-opt` is invoked exactly once so there is nothing to shard.
  **Correction of an earlier claim in this document:** the bottleneck is *not*
  Asyncify's whole-module analysis. `BINARYEN_PASS_DEBUG=1` at realistic scale
  gives `inlining-optimizing` **71%**, `asyncify` **16%**, 31 other passes 13%.
  Because inlining dominates, lowering the link optimization level really does
  shrink this stage — measured with the production argument list:
  `-O2` is **−20%** wall time at +2.7% size, `-O1` is **−78%** at +2.6% size.
  The workflow exposes this as the `link_opt` input (`O2` = release default,
  ~3h14m; `O1` = verification, ~53min), consumed by `ci/tune-makefile.sh` via
  `CDDA_LINK_OPT`. Raw data:
  `docs/measurements/raw/2026-09-04-link-breakdown-real.log` and
  `docs/measurements/raw/2026-09-04-wasm-opt-scaling.log`.
- **The single biggest win — upgrading Binaryen (F-26).** The apparent
  contradiction between web.dev's claim that Binaryen uses "all available CPU
  cores" and our measured 1.05 cores was resolved by reading the source:
  v116 `src/passes/Inlining.cpp:1105` says `// perform inlinings TODO:
  parallelize`. Candidate *scanning* was parallel; the *application* was not.
  Upstream PRs **#6966** ("makes the pass over 2x faster") and **#6967**
  ("a 5x speedup on a large real-world wasm file") landed in **Sept 2024** —
  i.e. after the Binaryen 116 bundled with emsdk 3.1.51 (Dec 2023), and they
  target precisely our 71% hot pass. Swapping in **version 132** measures
  **−37.8%** (5.871s → 3.650s, 3 repeats, production arguments, N=4000) for
  **+0.14%** output size, and the advantage *grows* with module size (+2% at
  N=1000, −9% at N=2000, −25% at N=4000), so −37.8% is a **lower bound**.
  Projected link stage: **242min → ~148min**. `ci/setup-binaryen.sh` performs
  the swap (sha256-pinned, idempotent, and **falling back to 116 on every
  failure path with exit 0** so a network outage can never fail a 4-hour
  build). Only `wasm-opt` is replaced, not the whole emsdk: the binary is
  statically linked, so the sole side effect is a harmless
  `unexpected binaryen version` warning.
  **Pitfall that must not be re-introduced:** version 132 with `-Os`/`-O3`/
  `-Oz` produces a build that **succeeds with exit code 0 but fails at
  runtime** with `LinkError: Import #0 "a" "a": function import requires a
  callable` (import-name minification disagreeing with the emscripten 3.1.51
  JS glue). Since the exit code cannot protect us, `ci/tune-makefile.sh`
  **rejects those levels at the entry point**; `-O2` and `-O1` are both
  verified correct. Raw data:
  `docs/measurements/raw/2026-09-04-wasm-opt-binaryen-version.log`.
- **Skippable — link.** `wasm-opt` is a deterministic transform of
  (440 `.o` files + link arguments), so identical inputs give a bit-identical
  `.js`/`.wasm`. `ci/link-fingerprint.sh` hashes the `.o` **contents** (not
  names or mtimes — restored artifacts always have fresh mtimes, so an
  mtime-sensitive key would never hit), plus `CDDA_MAKE_ARGS`, `CDDA_LINK_OPT`,
  the `Makefile`, `ci/*.sh`, `emcc --version` **and `wasm-opt --version`**.
  The last one is separate on purpose: `ci/setup-binaryen.sh` replaces
  `wasm-opt` independently of emcc, so without it a run that fell back to 116
  would happily reuse a cache entry produced by 132 (or vice versa) and ship a
  binary that was not built with the intended optimizer. The link job
  restores/saves
  `linkout-<fp>` around the link step, so a run that changes only `data/`,
  `gfx/`, mods, the HTML wrapper or workflow settings skips the whole 4-hour
  stage and finishes in ~15 minutes. `restore-keys` is deliberately omitted:
  a prefix match could serve a `.wasm` built from different inputs, which
  would silently publish a stale build. Any missing `.o` invalidates the
  fingerprint, and the artifacts are re-checked with `-s` even on a cache hit.

Supporting scripts, all with Japanese comments:

| script | role |
|---|---|
| `ci/make-args.sh` | single source of truth for the make variables (`NATIVE`, `TILES`, `RELEASE`, `LOCALIZE`, `CCACHE=1`, …). Every job sources it, because a variable divergence between the compile and link jobs breaks the build in ways that surface late (`TILES` changes `IMGUI_SOURCES` -> undefined symbols at link; `RELEASE` changes `OPTLEVEL`/`-DRELEASE` -> PCH mismatch at compile) |
| `ci/shard-plan.sh` | runs `make print-OBJS` and writes `objs-all.txt`, `shard-<i>.txt`, `matrix.json`. Never uses `ls src/*.cpp`: `SOURCES += $(THIRD_PARTY_SOURCES)` (flatbuffers/zstd) and the SDL-conditional `$(IMGUI_SOURCES)` would be missed |
| `ci/progress.sh` | the progress bar (see below) |
| `ci/setup-binaryen.sh` | replaces `$EMSDK/upstream/bin/wasm-opt` with Binaryen **132** (sha256-pinned) before the fingerprint is computed — the single largest speedup at **−37.8%** (F-26). Extracts only the one 17MB binary from the tarball, because unpacking everything exceeds 260MB and overflowed a 493MB `/tmp` in practice (`curl: (23) Failure writing output to destination`); `CDDA_TMPDIR` redirects the scratch area. **Every failure path keeps 116 and exits 0** — this is an optimization, so it must never fail a 4-hour build. Idempotent, keeps `wasm-opt.orig`, verifies the new binary *before* overwriting and rolls back if the post-swap check fails |
| `ci/verify-wasm.sh` | parses the wasm code section and measures the **largest single function**, failing the build if it exceeds V8's `kV8MaxWasmFunctionSize` (7,654,321 B) and warning above 95%. Added after run #75 shipped green but would not start in the browser (`size 7657177 > maximum function size 7654321`). Comparing against the last working build (#71) showed its largest function was already at **97.45%** of the limit — the bomb predated the Binaryen change, which merely pushed it 2.65% over. Necessary because `em++`/`wasm-opt` exit 0 even when the output cannot be instantiated, so only inspecting the artifact can catch it (F-27) |
| `ci/build-shard.sh` | per-shard compile: `make version` -> PCH -> `make -j4 <shard objects>` under the progress monitor -> per-object `[ -s ]` verification -> ccache stats |
| `ci/link-fingerprint.sh` | content hash of the link inputs, used as the `linkout-<fp>` cache key so an unchanged link can be skipped entirely. `sort`s the object list (find order is filesystem-dependent) and hashes contents, never mtimes. Emits `incomplete-<epoch>` — a deliberately never-matching key — if any `.o` is absent |
| `ci/link.sh` | completeness check (distinguishing *missing* from *zero-byte*, i.e. an OOM-killed compile), **`.d` deletion** (F-25 — without it 406/440 objects are recompiled), **timestamp reconciliation**, then the link. Its expected-duration display is derived from `CDDA_LINK_OPT` (O1 53min / O2 148min with Binaryen 132 / Os 242min) instead of the old 45min constant, which was off by 5× against the measured 4h02m |
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
- **Timestamps alone are not enough — the `.d` files must be deleted.**
  Touching mtimes did *not* stop the recompilation: run 33888888789 still
  rebuilt **406 of 440** objects in the link job, wasting 19 minutes. Cause:
  `-include ${OBJS:.o=.d}` pulls in dependency files whose paths are
  **absolute**, and `mymindstorm/setup-emsdk` unpacks emsdk into a
  **different random UUID directory in every job** (all 9 jobs of that run
  differed). From the link job those headers do not exist, and make treats a
  target with a *missing prerequisite* as always out of date — no amount of
  touching can override that. Two independently-reproduced mechanisms apply:
  (A) the PCH's `.d` is stale, so the PCH is regenerated and every
  PCH-dependent `src/*.o` follows; (B) each object's own `.d` is stale.
  Mechanism A alone exactly explains the 406-vs-34 split, because the
  `third-party` pattern rule does not depend on the PCH. `ci/link.sh` now
  deletes all `.d` files before linking — they describe "which object to
  rebuild when a source changes", which is information the link stage never
  needs. Verified: recompilation dropped to zero. See FACTS.md F-25.

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
| `activity-and-ime` | **must be applied after the earlier loader/world patches** (it shares touched source files). Adds the common browser-yield primitives and Japanese IME/text-input scopes. Historical note: this patch initially carried a 16-game-turn throttle for `mon_info_update()`, but the later `runtime-efficiency` patch explicitly removes it because the function drives safemode and danger/distraction checks. The current applied source retains all three upstream `g->mon_info_update()` call sites; CI rejects `mon_info_update_throttled` / `mon_info_update_interval_turns`. Do not reintroduce or cite the old throttle as current behavior. The activity pace control is separate: normal mode adds waiting between turns; fast mode removes that wait but neither skips simulation turns nor changes the upstream activity-render cadence. (2) *Text input* — new `src/cata_web_text_input.{h,cpp}` and the popup/context changes allow browser IME to reach in-game text fields (see "Japanese IME input" below) |
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
- Link optimization, now selectable (`CDDA_LINK_OPT`, default `O2`): an `-O0`
  link was used long ago to skip the Binaryen stage, but it leaves the Asyncify
  instrumentation unoptimized — measurably sluggish gameplay on 4GB devices —
  and roughly doubles the wasm download, so `-O0` stays rejected. The default
  moved from upstream's `-Os` to `-O2` after measuring **−20% link time for
  +2.7% size** with the production argument list. `-O1` is offered as a
  verification-only level (**−78%**, +2.6% size): with it a full build finishes
  in roughly an hour instead of four, which is what makes an
  edit → build → play iteration loop practical. `-O1` still runs Binaryen's
  optimizer over the Asyncify output, so it is not the `-O0` failure mode.
  A previous entry here claimed `-O2` was 41% faster and that `-O1` inflated
  the module ~18×; both figures were wrong, measured on a 246 KB toy module
  instead of the real one. See
  `docs/measurements/raw/2026-09-04-wasm-opt-scaling.log`.
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

## Persistent asset cache (shell only, no engine save-schema change)

The shell treats the four boot assets as one release: `cataclysm-tiles.data`,
`cataclysm-tiles.wasm`, `cataclysm-tiles.data.js`, and
`cataclysm-tiles.js`. Split data packs (next section) are deliberately
*outside* that release: `lazydata.js` is an ordinary `<script>` that is not
part of the snapshot, and each pack is fetched on demand into whichever
bundle bucket is currently active. The regular HTTP cache is short-lived, so the shell
uses Cache Storage for repeat and offline starts. The active implementation is
**versioned, immutable bundle snapshots** rather than the old mutable
`cdda-assets-v1` bucket:

- A candidate boot fetches all four assets and conditionally revalidates them
  using each entry's ETag. A 304 reuses that exact cached entry; a changed
  response is staged without overwriting the active snapshot.
- A new `cdda-assets-bundle-v3-*` bucket becomes active only after all four
  resources have been written and verified and the completion marker exists.
  The active pointer is kept in `cdda-assets-meta-v3`. The current and previous
  complete snapshots are retained for rollback; a partially failed deployment
  never mixes new files with stale cached files.
- If any candidate resource cannot be validated, the shell falls back to the
  previous **complete four-file snapshot**, not per-asset cache leftovers.
  If a cached WASM stream has to be retried, the retry ETag must match the
  selected bundle's ETag. A missing or different ETag fails closed instead of
  pairing a newer binary with older data or generated JavaScript.
- The WASM is handed to `Module.instantiateWasm` and
  `WebAssembly.instantiateStreaming`; the packaged data is passed once via
  `Module.getPreloadedPackage` and the reference is released after use.
  Generated JavaScript loaders are also fetched from the selected bundle and
  executed from temporary Blob URLs when their loader hooks are active.
- Cache Storage or quota failures do not invalidate the current online boot
  when the downloaded asset bytes are usable, and a failed cache write leaves
  the last known-good snapshot active. `navigator.storage.persist()` is best
  effort only: rejection is handled, but the browser is not guaranteed to
  grant persistent-storage protection.

The v1 bucket is not a valid offline bundle because it has no completeness
marker. The upgrade path only accepts the old v2 bucket when its complete
bundle marker and all four resources are present.

`-Werror` note: EM_ASM argument interpolation (`$0`/`$1`) trips
`-Wdollar-in-identifier-extension` under the project's `-Wpedantic -Werror`;
`cdda_pump_web_ime` is wrapped in a targeted
`#pragma clang diagnostic ignored` push/pop (compile-verified against
emsdk 3.1.51 with the exact CI flag set).

## Split data packs (lazy mount)

The single `cataclysm-tiles.data` used to carry the whole game data tree. Only a
small part of it is read before the main menu: measured on the 0.I tree,
**74 of 5,892 files / 33.6 MB of 134.6 MB** (25.8 MB of that is `gfx/`, and
most of the rest is `/data/json`, which is never touched until a world is
created). `ci/prepare-data.sh` therefore now emits the boot package plus a set
of extra packs:

| pack | contents | measured |
| --- | --- | --- |
| `cataclysm-tiles.data` (boot) | core, locale, tileset metadata, default tileset images | 31 MB (215 files) |
| `packs/world` | `/data/json` + `/data/mods` | 25 MiB (5,543 files) |
| `packs/gfx-<tileset>` | one pack per tileset in `gfx/` | remainder of 50 MiB |

`packs/manifest.json` (`{"version":1,"packs":[...],"eager":false}`) is the
only thing the shell needs to decide between the split and the classic
single-`.data` layout: **if it 404s, `lazydata.js` never does anything** and a
bundle built the old way keeps working.

**Engine side (`patches/cdda-0I-emscripten-lazy-data.patch`).** The packs are
plain `file_packager --lz4` output; the game itself is not restructured. Two
call sites are wrapped with `cata_web_ensure_pack()` (an `EM_ASYNC_JS`
bridge) so the data directory cannot be scanned before it exists:

- `init.cpp`, immediately before the JSON loader walks a data path
- `cata_tiles.cpp`, before a tileset's images are loaded (`"gfx:<id>"`)

`ci/apply-patches.sh` greps for all three markers (`src/cata_web_packs.h`,
the `init.cpp` call, the `cata_tiles.cpp` call). Without them the split build
fails in the worst possible way — an empty `/data/json` scans cleanly and the
game starts with missing data — so the guard is a hard `test`/`grep`, not a
warning.

**Shell side (`shell/lazydata.js`).** Loaded before the Emscripten bundle so
it can create `window.Module` first. Mounting one pack wraps
`Module.LZ4.loadPackage`, hands the already-fetched buffer to the generated
loader through `getPreloadedPackage` (no second network round trip), and
resolves only when the generated `loadPackage` call has actually run — its
`addRunDependency` is useless once execution has started. A 20 s timeout
turns a silent hang into a retry prompt; the retry resolves the same promise
the wasm side is awaiting, so the boot continues instead of restarting.
`warmup()` only warms Cache Storage after `menuready` (the pack bytes are not
kept on the heap); with `"eager":false` in the manifest nothing is downloaded
until the engine asks. `watchLazyPaths()` wraps `FS.open` and warns when
`/data/json` or `/data/mods` is read before `world` is mounted, which is the
diagnostic for a hook that went missing.

**Distribution gates.** `ci/prepare-bundle.sh` refuses to build when the
manifest exists but a listed pack is missing, and
`.github/workflows/build-and-release.yml` asserts `build/packs/manifest.json`
and `site/packs/manifest.json` before the Pages upload. The first version of
this change passed every compile and still shipped a `site/` without `packs/`
— the gate exists because the bundle step silently didn't copy the directory.

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
(src/path_info.cpp, init_user_dir). The shell selects a canonical profile ID
(1–24 ASCII letters, digits, `_` or `-`) and sets `ENV.HOME=/home/<profile>`
before CDDA initializes paths. The `idbfs-debounce` patch mounts IndexedDB at
that same profile-specific `$HOME/.cataclysm-dda`; older code pointed at a
fixed `/home/web_user` path, so saves were silently left in volatile MEMFS.
Profile IDs are rejected rather than punctuation-stripped or truncated into a
different save directory. An origin-wide exclusive Web Lock prevents two tabs
from opening the same profile concurrently; different profile IDs can still
run in separate tabs. If Web Locks are unavailable, the shell refuses to
launch the game rather than risk two tabs racing their IndexedDB writes. The
profile list remains in localStorage, and the post-mount
`CDDA_ON_IDBFS_MOUNTED` hook migrates config only after the real IndexedDB
contents are visible.

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
`docs/measurements/FACTS.md` (currently through F-29) with the raw logs under
`docs/measurements/raw/` and the microbenchmarks under `bench/`. Treat every
measurement according to its stated scope: a source-derived fixture is not a
whole-game timing, and a browser-run result is not a physical Chromebook result.

The old "~350-550MB steady state" summary is withdrawn as an unverified general
claim. A prior GitHub-hosted browser run recorded WebAssembly `HEAPU8.length`
of about 637.2 MiB in the `opengles2` path and 1,117.6 MiB in the software
renderer path. Those values are linear-memory buffer capacity, not resident RAM.
Chromium process PSS in that run was roughly 1.66-1.68 GiB, but it is not a
measure of the whole OS or every shared allocation. See the
[historical browser measurement run](https://github.com/LBTStudio/CDDA-broser/actions/runs/40574935396).
GitHub-hosted Chromium may use software WebGL, so the `opengles2` label alone
does not prove hardware-GPU acceleration. A stable memory range and support
claim for a 4GB Chromebook require repeated measurements and a physical
ChromeOS device; the current browser harness collects the relevant telemetry,
but it does not replace that device test.
