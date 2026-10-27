# CDDA WebAssembly architecture experiments

## Goal and guardrails

This experiment set compares compiler flags and generated Wasm, Asyncify versus
JSPI execution, and SDL renderer choices. It does not change CDDA game rules or
reduce enemy AI, danger checks, turn updates, random-number consumption, or save
frequency.

The public build remains the existing baseline: Emscripten 3.1.51, Asyncify,
C++ compilation at `-O3`, Binaryen 132 link optimization at `-O2`, and SIMD
disabled. The SIMD toggle is opt-in and experiments must use `deploy_pages=false`.
The existing JSPI guard also requires `deploy_pages=false`.

## Experiment order

### A. WebAssembly SIMD

Run two builds from the same commit, with the same Emscripten version, runtime,
link optimization, shard count, and source tree:

| Build | `wasm_simd` input | Expected compiler configuration |
|---|---|---|
| Scalar baseline | `off` | No `-msimd128` in C++ or link flags |
| SIMD candidate | `on` | `-msimd128` in both C++ compile and link flags |

Choose the `perf/architecture-simd-validation` branch in **Actions → Build CDDA
0.I WebAssembly → Run workflow**. Set `runtime_backend=asyncify`,
`link_opt=O2`, `shards=8`, and **`deploy_pages=false`** for both runs; change
only `wasm_simd`. Record both run IDs and their source commit SHA. Do not publish
either experiment as a side effect.

After each build completes, open **Actions → CDDA Playwright browser measurements
→ Run workflow**. Keep `base_url` at its default, set `artifact_run_id` to the
corresponding successful build run ID, choose `scenario=all`, `renderer=compare`,
and `population=0`. This downloads and measures the precise `cdda-0I-web-bundle`
artifact rather than accidentally measuring whatever version is currently on Pages.
Repeat the measurement workflow three times per build artifact using the same
options. Retain all run IDs and artifacts.

A successful configuration test proves only that the flag is propagated to compile
and link phases. A full build must instantiate in Chromium and complete real-game
scenarios before it can be considered a candidate.

### B. Asyncify versus JSPI

After the SIMD result is available, compare the best validated Wasm configuration
with the same configuration using `runtime_backend=jspi` and
`deploy_pages=false`. JSPI selects Emscripten 4.0.15 and native Wasm exception
handling, so this is a toolchain-and-exception-ABI comparison, not a pure JSPI-only
comparison. Do not attribute differences to JSPI if other build flags differ.

### C. Renderer

The measurement workflow's `renderer=compare` option compares SDL's `software`
and `opengles2` paths for the same artifact. Record the browser/GPU backend. On
GitHub-hosted Linux runners, WebGL can use SwiftShader, so these results do not
prove hardware-GPU performance on ChromeOS.

### D. LTO and PGO

Do not enable LTO or PGO as part of the SIMD run. Test them separately after
collecting the first matrix. LTO changes global optimization and build/link costs;
PGO requires a representative training workload. Any PGO profile must include
movement, world loading, inventory/map UI, save/reload, and long-running activities.
A profile from only a read/sleep benchmark is not representative enough.

## Measurements and acceptance

For each build/measurement pair, record:

- source commit SHA, Emscripten/Binaryen versions, runtime backend, SIMD mode,
  renderer and renderer backend;
- SHA-256 of all seven distribution files, build and startup duration, Wasm size,
  Wasm heap size, JS heap report, and Chromium RSS/PSS samples;
- reading and four-hour sleep results, `[perf]` turns and elapsed time, and saved
  state after each scenario;
- input-to-next-frame latency p50/p95/max, requestAnimationFrame intervals,
  Long Tasks, console/page errors, and failed requests;
- save-file update, IndexedDB sync start/completion, successful reload, and whether
  game state matches the baseline's expected progression.

Run each browser measurement three times and compare medians, p95 and maxima. Keep
scenario and renderer fixed when evaluating SIMD or runtime changes; use
`renderer=compare` to test renderer differences separately. Accept a change only
when all runs start, both activities complete and persist, no new runtime or
save/reload failure appears, and measured latency/throughput improves beyond
run-to-run variation without unacceptable increases in Wasm size or memory. If
results overlap within measurement noise, retain the current baseline.

The CI measurement environment is not a 4GB Chromebook: WebGL may use SwiftShader,
and Chromium RSS/PSS is not total OS memory. A low-memory and hardware-rendering
claim remains unproven until a matching ChromeOS device is measured. Do not label
an architecture experiment "faster" from compile-time reduction, synthetic loops,
frame-count changes alone, or one run.

## Scope of this PR

This PR adds an opt-in SIMD build switch, a configuration regression test, and an
exact-build artifact measurement path for the existing Playwright harness. It does
not claim SIMD or JSPI is faster; full-game A/B measurements must produce evidence
before defaults can change.
