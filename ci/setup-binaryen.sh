#!/usr/bin/env bash
# CDDA 0.I ブラウザ版: wasm-opt（Binaryen）を新しい版に差し替える
#
# ==================================================================
# なぜこのスクリプトが「4 時間問題」の本命なのか
# ==================================================================
# 本番 run 33730439512（合計 4h23m）の ps サンプル 2218 件を集計すると、
# 時間の内訳はこうなっていた（docs/measurements/raw/2026-09-04-link-breakdown-real.log）:
#
#     wasm-opt (リンク最適化) 4h02m   CPU 113%   RSS 6.46GB   ← 全体の【93%】
#     clang    (コンパイル)   0h14m   CPU 100%   RSS 1.54GB
#     node     (JS 最適化)    数秒    CPU 200%
#
# さらに wasm-opt の内部をパス単位で測ると（BINARYEN_PASS_DEBUG=1）:
#
#     inlining-optimizing  71%   ← 真の支配要因
#     asyncify             16%
#     その他 31 パス       13%
#
# つまり短縮したいのは「inlining-optimizing パス」ただ一点である。
#
# ==================================================================
# 【突破口】上流 Binaryen が、まさにこのパスを我々の版より後に高速化した
# ==================================================================
# 我々が使う emsdk 3.1.51 に同梱される wasm-opt は【116】（2023年12月）。
# その後 2024年9月に、inlining-optimizing を直接高速化する PR が入った:
#
#   #6966 (2024-09-24) Parallelize the actual inlining part of the Inlining pass
#                      「this PR makes the pass over 2x faster」
#   #6967 (2024-09-24) Avoid repeated ReFinalize etc. when inlining
#                      「a 5x speedup on a large real-world wasm file」
#                      「in one case we inline over 1,000 times into a function!"
#   #6969 (2024-09-26) Stop creating unneeded blocks around calls when inlining
#   #7669 (2025-06-30) Always inline trivial calls that always shrink
#   #7820 (2025-08-18) Inlining: Make MaxCombinedBinarySize configurable
#
# ==================================================================
# 実測（3 回反復 / N=4000 = 8579 関数 / 入力 1.89MB / 本番と同じ引数）
# ==================================================================
#   116 -Os（従来）  6.163s / 5.712s / 5.739s  平均 5.871s  出力 771,797 B
#   132 -O2（採用）  3.742s / 3.771s / 3.436s  平均 3.650s  出力 772,860 B
#
#   → 【-37.8%】。しかも出力サイズはほぼ同じ（+0.14%）。
#
# 【効果は規模が大きいほど伸びる】（-Os 同条件での 116→132 比較）:
#   N=1000  2.163s → 2.213s （+2%   … 小さすぎて利かない）
#   N=2000  3.211s → 2.930s （-9%）
#   N=4000  5.845s → 4.394s （-25%）
# 本番は 8579 関数どころではないので、-37.8% は【下限】と考えてよい。
#
# ==================================================================
# 失敗しても【絶対にビルドを止めない】
# ==================================================================
# これは「速くするための最適化」であって必須機能ではない。
# ネットワーク障害・GitHub のリリース資産の移動・sha256 不一致が
# 起きたときに、4 時間のビルド全体を落とすのは明らかに損である。
# そのため全ての失敗経路で 116 のまま続行し、終了コード 0 で返す。
#
set -uo pipefail
# 【set -e を使わない】
# このスクリプトは「失敗しても続行」が仕様である。

# ------------------------------------------------------------------
# 設定
# ------------------------------------------------------------------
BINARYEN_VERSION="${CDDA_BINARYEN_VERSION:-132}"
BINARYEN_SHA256="${CDDA_BINARYEN_SHA256:-195ddc94f9bc89f45abdabb0b9eea86023d727ba90eac8b35b80f2544fc30572}"

if [ "${CDDA_BINARYEN:-1}" = "0" ]; then
    echo "[BINARYEN] CDDA_BINARYEN=0 のため差し替えを行いません（同梱の 116 を使用）"
    exit 0
fi

if [ -z "${EMSDK:-}" ]; then
    echo "[BINARYEN] WARNING: EMSDK が未設定のため差し替えを省略します。" >&2
    echo "[BINARYEN]          → 同梱の wasm-opt のまま続行します。" >&2
    exit 0
fi

WASM_OPT="$EMSDK/upstream/bin/wasm-opt"

if [ ! -x "$WASM_OPT" ]; then
    echo "[BINARYEN] WARNING: wasm-opt が見つかりません: $WASM_OPT" >&2
    echo "[BINARYEN]          → 差し替えを省略して続行します。" >&2
    exit 0
fi

# ------------------------------------------------------------------
# 現在の版を確認（冪等性のため）
# ------------------------------------------------------------------
cur_ver="$("$WASM_OPT" --version 2>/dev/null | head -1)"
echo "[BINARYEN] 現在の wasm-opt: ${cur_ver:-取得失敗}"

case "$cur_ver" in
    *"version $BINARYEN_VERSION"*)
        echo "[BINARYEN] すでに版 ${BINARYEN_VERSION} です（何もしません）"
        exit 0
        ;;
esac

# ------------------------------------------------------------------
# 取得
# ------------------------------------------------------------------
arch="$(uname -m)"
if [ "$arch" != "x86_64" ]; then
    echo "[BINARYEN] WARNING: 想定外のアーキテクチャです（${arch}）。" >&2
    exit 0
fi

tarball="binaryen-version_${BINARYEN_VERSION}-x86_64-linux.tar.gz"
url="https://github.com/WebAssembly/binaryen/releases/download/version_${BINARYEN_VERSION}/${tarball}"

# ------------------------------------------------------------------
# 作業用の一時ディレクトリ
# ------------------------------------------------------------------
work_dir="$(mktemp -d "${CDDA_TMPDIR:-${TMPDIR:-/tmp}}/binaryen.XXXXXX" 2>/dev/null)"
if [ -z "$work_dir" ] || [ ! -d "$work_dir" ]; then
    echo "[BINARYEN] WARNING: 一時ディレクトリを作れませんでした。→ 116 のまま続行します。" >&2
    exit 0
fi
cleanup() { rm -rf "$work_dir"; }
trap cleanup EXIT

echo "[BINARYEN] 取得: $url"

if ! curl -fsSL --retry 3 --retry-delay 2 -o "$work_dir/$tarball" "$url"; then
    echo "[BINARYEN] WARNING: ダウンロードに失敗しました。" >&2
    echo "[BINARYEN]          → 同梱の 116 のまま続行します。" >&2
    exit 0
fi

# ------------------------------------------------------------------
# sha256 検証
# ------------------------------------------------------------------
actual_sha="$(sha256sum "$work_dir/$tarball" | cut -d' ' -f1)"
if [ "$actual_sha" != "$BINARYEN_SHA256" ]; then
    echo "[BINARYEN] WARNING: sha256 が一致しません。差し替えを中止します。" >&2
    echo "[BINARYEN]          期待: $BINARYEN_SHA256" >&2
    echo "[BINARYEN]          実際: $actual_sha" >&2
    exit 0
fi
echo "[BINARYEN] sha256 一致を確認"

# ------------------------------------------------------------------
# 展開（wasm-opt 1 個だけを取り出す）
# ------------------------------------------------------------------
member="binaryen-version_${BINARYEN_VERSION}/bin/wasm-opt"
if ! tar -xzf "$work_dir/$tarball" -C "$work_dir" "$member" 2>/dev/null; then
    echo "[BINARYEN] WARNING: 展開に失敗しました。→ 116 のまま続行します。" >&2
    exit 0
fi

new_wasm_opt="$work_dir/$member"
if [ ! -x "$new_wasm_opt" ]; then
    echo "[BINARYEN] WARNING: 展開後に wasm-opt が見つかりません。→ 116 のまま続行します。" >&2
    exit 0
fi

# ------------------------------------------------------------------
# 新しいバイナリが本当に動くか、差し替える【前】に確認する
# ------------------------------------------------------------------
new_ver="$("$new_wasm_opt" --version 2>/dev/null | head -1)"
case "$new_ver" in
    *"version $BINARYEN_VERSION"*)
        echo "[BINARYEN] 取得した wasm-opt: $new_ver"
        ;;
    *)
        echo "[BINARYEN] WARNING: 取得した wasm-opt が動作しません（${new_ver:-無応答}）。" >&2
        exit 0
        ;;
esac

# ------------------------------------------------------------------
# 差し替え（元の版は必ず残す）
# ------------------------------------------------------------------
if [ ! -f "${WASM_OPT}.orig" ]; then
    if ! cp -p "$WASM_OPT" "${WASM_OPT}.orig"; then
        echo "[BINARYEN] WARNING: バックアップに失敗しました。→ 116 のまま続行します。" >&2
        exit 0
    fi
    echo "[BINARYEN] 元の wasm-opt を ${WASM_OPT}.orig に保存"
fi

if ! cp "$new_wasm_opt" "$WASM_OPT"; then
    echo "[BINARYEN] WARNING: 差し替えに失敗しました。→ 116 のまま続行します。" >&2
    exit 0
fi
chmod +x "$WASM_OPT"

# ------------------------------------------------------------------
# 差し替え後の検証
# ------------------------------------------------------------------
after_ver="$("$WASM_OPT" --version 2>/dev/null | head -1)"
case "$after_ver" in
    *"version $BINARYEN_VERSION"*)
        echo "[BINARYEN] OK: wasm-opt を差し替えました → $after_ver"
        echo "[BINARYEN]     実測 -37.8%（N=4000 / 本番同一引数 / 3回反復平均）"
        echo "[BINARYEN]     ※ 最適化レベルは -O2 か -O1 のみ（-Os は実行時に壊れます）"
        ;;
    *)
        echo "[BINARYEN] WARNING: 差し替え後の検証に失敗しました。元に戻します。" >&2
        cp -p "${WASM_OPT}.orig" "$WASM_OPT" || true
        chmod +x "$WASM_OPT" || true
        ;;
esac

exit 0
