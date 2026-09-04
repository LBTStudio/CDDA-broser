#!/usr/bin/env bash
# CDDA 0.I ブラウザ版: 配布用の build/ を組み立てる
#
# ==================================================================
# このスクリプトの立ち位置
# ==================================================================
#   data ジョブ    : ci/prepare-data.sh   → .data / .data.js
#   link ジョブ    : ci/link.sh           → .js / .wasm
#   bundle ジョブ  : このスクリプト        ← ここで合流させる
#
# 上流 build-scripts/prepare-web.sh の最終段（build/ への
# コピー）に相当する。.js / .wasm と .data / .data.js の
# 両方が揃って初めて実行できるので、独立させてある。
#
# ==================================================================
# 検証を厚くしている理由
# ==================================================================
# ここは【ビルドの最終関門】である。ここを通った成果物が
# そのまま GitHub Pages に載り、利用者のブラウザで動く。
#
# 分割ビルドでは成果物が複数のジョブから集まってくるため、
# 「あるジョブのアーティファクトのダウンロードだけ失敗した」
# という壊れ方がありうる。その場合ファイルが 1 個欠けた
# 状態で公開され、ブラウザ側で初めて
#   ・.data が無い → ゲームデータが無くタイトルすら出ない
#   ・.wasm が無い → 真っ白な画面
# という形で発覚する。利用者が最初に踏むことになる。
#
# したがってここでは【1 個ずつ、サイズまで】検証する。
set -euo pipefail

if [ ! -f Makefile ] || [ ! -d data ]; then
    echo "ERROR: CDDA のソースツリー内で実行してください" >&2
    exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "$0" )" && pwd )"
# shellcheck source=./make-args.sh
. "$SCRIPT_DIR/make-args.sh"
# shellcheck source=./progress.sh
. "$SCRIPT_DIR/progress.sh"

echo "=================================================================="
echo "[BUNDLE] 配布物の組み立てを開始"
echo "=================================================================="

# ------------------------------------------------------------------
# 手順 1: 入力の検証
# ------------------------------------------------------------------
echo "[BUNDLE] === 手順 1/3: 入力ファイルの検証 ==="

# 【期待される入力と、その出どころ】
#   cataclysm-tiles.data     ← data ジョブ
#   cataclysm-tiles.data.js  ← data ジョブ
#   cataclysm-tiles.js       ← link ジョブ
#   cataclysm-tiles.wasm     ← link ジョブ
#   build-data/web/index.html← CDDA のソース（後で差し替える）
#   data/font/Terminus.ttf   ← CDDA のソース
#   data/cataicon.ico        ← CDDA のソース
missing=0
check_input() {
    local path="$1" origin="$2"
    if [ ! -s "$path" ]; then
        echo "ERROR: 入力が欠けています: $path" >&2
        echo "       出どころ: $origin" >&2
        missing=$(( missing + 1 ))
    else
        printf '[BUNDLE]   OK %-28s %8s  (%s)\n' \
            "$path" "$( du -h "$path" | cut -f1 )" "$origin"
    fi
}

check_input cataclysm-tiles.data     "data ジョブ / prepare-data.sh"
check_input cataclysm-tiles.data.js  "data ジョブ / prepare-data.sh"
check_input "$CDDA_TARGET_JS"        "link ジョブ / link.sh"
check_input "$CDDA_TARGET_WASM"      "link ジョブ / link.sh"
check_input build-data/web/index.html "CDDA ソース"
check_input data/font/Terminus.ttf   "CDDA ソース"
check_input data/cataicon.ico        "CDDA ソース"

if [ "$missing" -gt 0 ]; then
    echo "ERROR: ${missing} 個の入力が欠けているため中止します。" >&2
    echo "       アーティファクトのダウンロードが失敗した可能性があります。" >&2
    exit 1
fi

# ------------------------------------------------------------------
# 手順 2: build/ の組み立て
# ------------------------------------------------------------------
echo "[BUNDLE] === 手順 2/3: build/ の組み立て ==="

# 【rm -rf する理由】
# 前回の残骸が混ざると、古い .wasm が公開されうる。
# 「古いが存在する」ファイルは検証を通ってしまうので、
# 必ず空から作る。
rm -rf build
mkdir -p build

cp build-data/web/index.html build/
cp cataclysm-tiles.data     build/
cp cataclysm-tiles.data.js  build/
cp "$CDDA_TARGET_JS"        build/
cp "$CDDA_TARGET_WASM"      build/
cp data/font/Terminus.ttf   build/
cp data/cataicon.ico        build/favicon.ico

# ------------------------------------------------------------------
# 手順 3: 成果物の検証
# ------------------------------------------------------------------
echo "[BUNDLE] === 手順 3/3: 成果物の検証 ==="

# 上流ワークフローが test -s していたものと同一の一覧。
# 【ここを削ってはいけない】。1 個でも欠けると
# ブラウザで真っ白な画面になり、原因が分かりにくい。
for f in \
    build/index.html \
    build/cataclysm-tiles.data \
    build/cataclysm-tiles.data.js \
    build/cataclysm-tiles.js \
    build/cataclysm-tiles.wasm \
    build/Terminus.ttf \
    build/favicon.ico
do
    if [ ! -s "$f" ]; then
        echo "ERROR: 成果物が欠けています: $f" >&2
        exit 1
    fi
    printf '[BUNDLE]   OK %-34s %8s\n' "$f" "$( du -h "$f" | cut -f1 )"
done

# 【4GB RAM 機で動くかの目安】
# ブラウザは .data を丸ごと展開して仮想ファイルシステムに
# 載せ、.wasm もコンパイル済みコードとしてメモリに持つ。
# したがってこの 2 つの合計は、そのままメモリ消費の下限になる。
data_mib=$(( $( wc -c < build/cataclysm-tiles.data ) / 1048576 ))
wasm_mib=$(( $( wc -c < build/cataclysm-tiles.wasm ) / 1048576 ))
total_mib="$(( data_mib + wasm_mib ))"

echo "=================================================================="
echo "[BUNDLE] 完了"
echo "[BUNDLE] 配布物合計: $( du -sh build | cut -f1 )"
echo "[BUNDLE] .data ${data_mib} MiB + .wasm ${wasm_mib} MiB = ${total_mib} MiB"
echo "[BUNDLE] （この合計がブラウザのメモリ消費の下限の目安です）"
echo "=================================================================="

step_summary "| bundle | 7 ファイル | - | 合計 $( du -sh build | cut -f1 ) / メモリ下限目安 ${total_mib} MiB |"
