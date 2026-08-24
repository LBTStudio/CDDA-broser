# Architecture

CDDA 0.I (2026-06-06) still carries the SDL2-based Emscripten target in its Makefile
(-sUSE_SDL=2 / SDL2_image / SDL2_ttf, ASYNCIFY, IDBFS, ALLOW_MEMORY_GROWTH to 4GB).
Upstream marked the wasm target unsupported on 2026-08-12 (commit 080986b0, master only)
after moving to SDL3. The 0.I tag predates that change, so `emsdk 3.1.51` builds it as-is.

Pipeline (all in CI, nothing large is committed to the repo):
1. actions/checkout of CleverRaven/Cataclysm-DDA @ 0.I
2. emsdk 3.1.51 + ccache
3. make NATIVE=emscripten TILES=1 RELEASE=1 cataclysm-tiles.js  ->  .js glue + ~45MB .wasm
4. build-scripts/prepare-web.sh  ->  LZ4 data bundle (~68MB) + build/ site layout
5. shell/index.html (profile-aware) replaces the upstream build/index.html
6. deploy to GitHub Pages

Per-player save isolation:
The game resolves its user dir as getenv("HOME") + "/.cataclysm-dda/"
(src/path_info.cpp, init_user_dir). The shell sets Module.ENV.HOME=/home/<profile>
before boot, so each player name gets an independent subtree on the IDBFS-backed
persistent filesystem (IndexedDB). The profile list lives in localStorage.

Runtime: ~350-550MB steady state, WebGL2, works in stock ChromeOS Chrome
(no Crostini/Linux container required).
