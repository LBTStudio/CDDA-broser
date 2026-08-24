#!/usr/bin/env bash
# Bare-metal build (Linux/macOS host, 8+ GB RAM). Produces ./build/ static site.
set -euxo pipefail
CDDA_TAG="${CDDA_TAG:-0.I}"
EMSDK_VERSION="${EMSDK_VERSION:-3.1.51}"
WORKDIR="${WORKDIR:-$PWD/cdda-web-workdir}"
mkdir -p "$WORKDIR" && cd "$WORKDIR"
[ -d emsdk ] || git clone --depth 1 https://github.com/emscripten-core/emsdk.git
./emsdk/emsdk install "$EMSDK_VERSION"
./emsdk/emsdk activate "$EMSDK_VERSION"
source ./emsdk/emsdk_env.sh
[ -d Cataclysm-DDA ] || git clone --depth 1 --branch "$CDDA_TAG" https://github.com/CleverRaven/Cataclysm-DDA.git
cd Cataclysm-DDA
make -j"$(nproc)" NATIVE=emscripten BACKTRACE=0 TILES=1 TESTS=0 RUNTESTS=0 RELEASE=1 CCACHE=0 LINTJSON=0 cataclysm-tiles.js
bash build-scripts/prepare-web.sh
echo "Done. Static site in: $(pwd)/build"
echo "NOTE: replace build/index.html with this repo's shell/index.html for per-player save profiles."
