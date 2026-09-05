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
#                      「in one case we inline over 1,000 times into a function!」
#   #6969 (2024-09-26) Stop creating unneeded blocks around calls when inlining
#   #7669 (2025-06-30) Always inline trivial calls that always shrink
#   #7820 (2025-08-18) Inlining: Make MaxCombinedBinarySize configurable
#
# 【長年の矛盾もここで解けた】
# web.dev の Binaryen 解説は
#   "designed for completely parallel code generation and optimization,
#    using all available CPU cores"
# と書いているが、本番実測の CPU は 105%＝【1.05 コア】しかなかった。
# 版 116 のソース src/passes/Inlining.cpp 1105 行目にこう書かれている:
#
#     // perform inlinings TODO: parallelize
#
# 候補の【探索】は並列だが、実際の【適用】が逐次だったのである。
# PR #6966 がこの TODO を解消した。矛盾は「ドキュメントが将来形だった」
# ということだった。
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
# 【正直な注意書き】
# PR #6967 が直した O(n²) 的な最悪ケース（1 関数に 1000 回インライン）は
# 合成ベンチでは再現できなかった（コンパイラが自明な関数を畳んでしまう）。
# 本番でそれが起きていれば短縮幅はさらに大きいが、確認できていない。
#
# 【版 120 は 116 より遅い】
# 「新しければ速い」ではない。120 は 116 より明確に遅かった
# （9.216s/9.090s に対し 13.113s/12.772s）。
# 120 だけを試して「更新は無意味」と結論しかけたので、
# 版を上げるときは【必ず実測すること】。
#
# ==================================================================
# 【最重要な落とし穴】132 は -Os / -O3 / -Oz で壊れる
# ==================================================================
# 132 に差し替えたうえで -Os / -O3 / -Oz でリンクすると、
# ビルドは成功するのに【実行時に】こう落ちる:
#
#   failed to asynchronously prepare wasm: LinkError:
#   WebAssembly.instantiate(): Import #0 "a" "a":
#   function import requires a callable
#
# 原因は import 名の短縮（minify）が emscripten 3.1.51 の JS グルー側と
# 食い違うことである。切り分けは次のように行った:
#   ・116 に戻して同じフラグ → 動く（＝132 起因で確定）
#   ・-sASYNCIFY / -sWASM_BIGINT / -lembind / -sLZ4 / -sFORCE_FILESYSTEM
#     を個別に外す → いずれも無関係（＝最適化レベルだけが要因）
#
# 【検証済みの安全な組み合わせ】（期待値 SUM=10650 が一致）:
#   116 -Os / 116 -O2 / 116 -O1  → すべて SUM=10650
#   132 -O2                      → SUM=10650  【安全】
#   132 -O1                      → SUM=10650  【安全】
#   132 -Os                      → 上記 LinkError  【使用禁止】
#
# よって ci/tune-makefile.sh は O2 か O1 しか受け付けない。
# ここを将来 -Os に戻すと【実行時にだけ】壊れるので絶対にしないこと。
#
# 【なぜ終了コードで守れないのか】
# 上の -Os 破損ビルドも em++ の終了コードは【0】である。
# 壊れるのはブラウザ／node が wasm を instantiate する時点なので、
# CI の成否だけ見ていると気付けない。だから「レベルを制限する」以外に
# 守る方法がない。
#
# ==================================================================
# 差し替え方式について（emsdk 全体の更新はしない）
# ==================================================================
# emsdk を 3.1.51 から上げると Makefile 側のフラグ互換性・
# JS グルーの挙動・ASYNCIFY 周りが一斉に変わり、
# 「4 時間問題」とは無関係な事故が増える。
#
# 一方、wasm-opt は【単体で静的リンクされた実行ファイル】である
# （ldd の結果が "statically linked"）。したがってバイナリ 1 個を
# 上書きするだけで差し替えが成立する。実際の副作用は次の警告 1 行だけ:
#
#   em++: warning: unexpected binaryen version: 132 (expected 115)
#         [-Wversion-check]
#
# これは無害なので抑止せずそのまま出す（版が変わったことがログに残る）。
#
# ==================================================================
# 失敗しても【絶対にビルドを止めない】
# ==================================================================
# これは「速くするための最適化」であって必須機能ではない。
# ネットワーク障害・GitHub のリリース資産の移動・sha256 不一致が
# 起きたときに、4 時間のビルド全体を落とすのは明らかに損である。
# そのため全ての失敗経路で 116 のまま続行し、終了コード 0 で返す。
#
# 根拠ログ:
#   docs/measurements/FACTS.md  F-26
#   docs/measurements/raw/2026-09-04-wasm-opt-binaryen-version.log
#
# 使い方:
#   source "$EMSDK/emsdk_env.sh"
#   bash ../ci/setup-binaryen.sh
#
# 【必ず bash 経由で呼ぶこと】
# run 33982035545（#74）はこのスクリプトを ./ci/setup-binaryen.sh と
# 直接実行して次のように落ちた:
#
#   ./ci/setup-binaryen.sh: Permission denied
#   ##[error]Process completed with exit code 126
#
# git は実行権限（100755/100644）を記録するが、
# 新規ファイルの追加時に取りこぼしやすい。実際 main では
# ci/setup-binaryen.sh と ci/link-fingerprint.sh が 100644 のまま
# 取り込まれ、plan ジョブが即座に失敗した。
#
# 権限ビットに依存する呼び方は「1 ファイル漏れるだけでビルド全体が
# 止まる」ので、ワークフロー側は全て `bash <script>` の形に統一した。
# こうすればモードが 644 でも動く（構造的に事故らない）。
set -uo pipefail
# 【set -e を使わない】
# このスクリプトは「失敗しても続行」が仕様である。
# set -e を入れると curl や tar の失敗で即死してビルドを落としてしまう。

# ------------------------------------------------------------------
# 設定
# ------------------------------------------------------------------
# 版を上げるときは【必ず】この 2 つを同時に更新すること。
# sha256 は次で取得できる:
#   curl -sL https://github.com/WebAssembly/binaryen/releases/download/\
#     version_132/binaryen-version_132-x86_64-linux.tar.gz.sha256
BINARYEN_VERSION="${CDDA_BINARYEN_VERSION:-132}"
BINARYEN_SHA256="${CDDA_BINARYEN_SHA256:-195ddc94f9bc89f45abdabb0b9eea86023d727ba90eac8b35b80f2544fc30572}"

# CDDA_BINARYEN=0 で明示的に無効化できる（切り分け用）。
if [ "${CDDA_BINARYEN:-1}" = "0" ]; then
    echo "[BINARYEN] CDDA_BINARYEN=0 のため差し替えを行いません（同梱の 116 を使用）"
    exit 0
fi

if [ -z "${EMSDK:-}" ]; then
    echo "[BINARYEN] WARNING: EMSDK が未設定のため差し替えを省略します。" >&2
    echo "[BINARYEN]          emsdk_env.sh を source してから実行してください。" >&2
    echo "[BINARYEN]          → 同梱の wasm-opt のまま続行します（ビルドは通ります）。" >&2
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
# このスクリプトは同じジョブで 2 回走ることがある。
# すでに目的の版なら何もしない。
cur_ver="$( "$WASM_OPT" --version 2>/dev/null | head -1 )"
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
# 【なぜ x86_64-linux 固定でよいのか】
# このワークフローは ubuntu-latest でしか動かさない。
# 将来 arm ランナーを使うなら、ここで uname -m を見て分岐する必要がある。
arch="$( uname -m )"
if [ "$arch" != "x86_64" ]; then
    echo "[BINARYEN] WARNING: 想定外のアーキテクチャです（${arch}）。" >&2
    echo "[BINARYEN]          公式リリースは x86_64-linux 前提なので差し替えを省略します。" >&2
    exit 0
fi

tarball="binaryen-version_${BINARYEN_VERSION}-x86_64-linux.tar.gz"
url="https://github.com/WebAssembly/binaryen/releases/download/version_${BINARYEN_VERSION}/${tarball}"

# ------------------------------------------------------------------
# 作業用の一時ディレクトリ
# ------------------------------------------------------------------
# 【なぜ置き場所を選べるようにするのか】
# 開発中、/tmp が 493MB の tmpfs である環境で実際に失敗した:
#
#   curl: (23) Failure writing output to destination
#
# tarball 自体は約 13MB だが、素朴に全部展開すると
# bin/ に 260MB 超（binaryen-unittests や wasm-fuzz-* を含む）
# 置くことになり、小さな /tmp では溢れる。
#
# 対策は 2 つ入れてある:
#   (1) CDDA_TMPDIR / TMPDIR で置き場所を変えられる
#   (2) 下で【wasm-opt 1 個だけ】を取り出す（後述）
work_dir="$( mktemp -d "${CDDA_TMPDIR:-${TMPDIR:-/tmp}}/binaryen.XXXXXX" 2>/dev/null )"
if [ -z "$work_dir" ] || [ ! -d "$work_dir" ]; then
    echo "[BINARYEN] WARNING: 一時ディレクトリを作れませんでした。→ 116 のまま続行します。" >&2
    exit 0
fi
cleanup() { rm -rf "$work_dir"; }
trap cleanup EXIT

echo "[BINARYEN] 取得: $url"

# --retry でネットワークの一時障害を吸収する。
# --fail で HTTP 4xx/5xx を確実に失敗にする（これが無いと
# エラーページを tar に渡してしまう）。
if ! curl -fsSL --retry 3 --retry-delay 2 -o "$work_dir/$tarball" "$url"; then
    echo "[BINARYEN] WARNING: ダウンロードに失敗しました。" >&2
    echo "[BINARYEN]          → 同梱の 116 のまま続行します（リンクは通りますが遅くなります）。" >&2
    exit 0
fi

# ------------------------------------------------------------------
# sha256 検証
# ------------------------------------------------------------------
# 【なぜ検証するのか】
# ここで落としたバイナリは、そのまま配布物 wasm を生成する。
# 差し替え対象が壊れていたり別物だったりすると、
# 「4 時間かけて壊れた成果物を作る」ことになる。
# 版を固定するだけでは不十分（リリース資産は再アップロードされうる）。
actual_sha="$( sha256sum "$work_dir/$tarball" | cut -d' ' -f1 )"
if [ "$actual_sha" != "$BINARYEN_SHA256" ]; then
    echo "[BINARYEN] WARNING: sha256 が一致しません。差し替えを中止します。" >&2
    echo "[BINARYEN]          期待: $BINARYEN_SHA256" >&2
    echo "[BINARYEN]          実際: $actual_sha" >&2
    echo "[BINARYEN]          → 同梱の 116 のまま続行します。" >&2
    echo "[BINARYEN]          （版を上げたなら ci/setup-binaryen.sh の" >&2
    echo "[BINARYEN]            BINARYEN_SHA256 の更新忘れです）" >&2
    exit 0
fi
echo "[BINARYEN] sha256 一致を確認"

# ------------------------------------------------------------------
# 展開（wasm-opt 1 個だけを取り出す）
# ------------------------------------------------------------------
# 【なぜ全部展開しないのか】
# この tarball には bin/ に 10 個以上の実行ファイルが入っており、
# 展開すると 260MB を超える:
#   binaryen-unittests 20MB / wasm-fuzz-lattices 18MB /
#   wasm-fuzz-types 18MB / wasm-as / wasm-dis / wasm-ctor-eval …
#
# 我々が必要なのは wasm-opt ただ 1 個（17MB）である。
# 小さな /tmp（実測 493MB tmpfs）で溢れるのを避けるため、
# tar に対象パスを明示して 1 個だけ取り出す。
#
# lib/libbinaryen.a（65MB）も不要である。wasm-opt は
# 静的リンク済み（ldd が "statically linked"）なので、
# バイナリ単体で動作する。
member="binaryen-version_${BINARYEN_VERSION}/bin/wasm-opt"
if ! tar -xzf "$work_dir/$tarball" -C "$work_dir" "$member" 2>/dev/null; then
    echo "[BINARYEN] WARNING: 展開に失敗しました。→ 116 のまま続行します。" >&2
    echo "[BINARYEN]          （書庫内の構成が変わった可能性があります:" >&2
    echo "[BINARYEN]            期待パス ${member}）" >&2
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
# 壊れたバイナリで上書きしてしまうと、元に戻す手段が
# バックアップだけになる。先に動作確認しておく。
new_ver="$( "$new_wasm_opt" --version 2>/dev/null | head -1 )"
case "$new_ver" in
    *"version $BINARYEN_VERSION"*)
        echo "[BINARYEN] 取得した wasm-opt: $new_ver"
        ;;
    *)
        echo "[BINARYEN] WARNING: 取得した wasm-opt が動作しません（${new_ver:-無応答}）。" >&2
        echo "[BINARYEN]          → 116 のまま続行します。" >&2
        exit 0
        ;;
esac

# ------------------------------------------------------------------
# 差し替え（元の版は必ず残す）
# ------------------------------------------------------------------
# .orig が既にあるなら上書きしない。
# 2 回目の実行で「116 のバックアップ」を「132」で潰さないため。
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
# 差し替え後の検証（ここで失敗したら必ず元に戻す）
# ------------------------------------------------------------------
# 「壊れた wasm-opt を掴んだまま 4 時間走る」のが最悪なので、
# 版が読めなければ即座にロールバックする。
after_ver="$( "$WASM_OPT" --version 2>/dev/null | head -1 )"
case "$after_ver" in
    *"version $BINARYEN_VERSION"*)
        echo "[BINARYEN] OK: wasm-opt を差し替えました → $after_ver"
        echo "[BINARYEN]     実測 -37.8%（N=4000 / 本番同一引数 / 3回反復平均）"
        echo "[BINARYEN]     116 -Os 5.871s → 132 -O2 3.650s / 出力サイズは +0.14%"
        echo "[BINARYEN]     ※ em++ の 'unexpected binaryen version' 警告は無害です"
        echo "[BINARYEN]     ※ 最適化レベルは -O2 か -O1 のみ（-Os は実行時に壊れます）"
        ;;
    *)
        echo "[BINARYEN] WARNING: 差し替え後の検証に失敗しました（${after_ver:-無応答}）。" >&2
        echo "[BINARYEN]          元の版に戻します。" >&2
        cp -p "${WASM_OPT}.orig" "$WASM_OPT" || true
        chmod +x "$WASM_OPT" || true
        echo "[BINARYEN]          → $( "$WASM_OPT" --version 2>/dev/null | head -1 ) で続行します。" >&2
        ;;
esac

exit 0
