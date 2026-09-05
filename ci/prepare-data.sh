#!/usr/bin/env bash
# CDDA 0.I ブラウザ版: ゲームデータを lz4 パックする（日本語翻訳込み）
#
# ==================================================================
# なぜ独立したスクリプト／独立したジョブにするのか
# ==================================================================
# 上流の build-scripts/prepare-web.sh は
#
#   (A) data/ と gfx/ を web_bundle/ に集める
#   (B) 廃止 mod を除去する
#   (C) file_packager で lz4 パックし .data と .data.js を作る
#   (D) build/ を作り、index.html / .data / .data.js /
#       .js / .wasm / フォント / アイコンをコピーする
#
# を一気に行う。しかし依存関係を見ると:
#
#   (A)(B)(C) は【.js / .wasm に一切依存しない】
#   (D) だけが .js / .wasm を必要とする
#
# 従来は単一ジョブだったため (A)〜(C) がリンクの後に
# 直列で足されていた。data/ と gfx/ は数百 MB あり、
# lz4 圧縮にも時間がかかるので、これは無駄である。
#
# そこで (A)(B)(C) をこのスクリプトに切り出し、
# 【コンパイル・リンクと並行して別ジョブで走らせる】。
# (D) は ci/prepare-bundle.sh が担当する。
#
# ==================================================================
# 上流スクリプトを書き換えず、必要部分を実行する方針
# ==================================================================
# 上流の prepare-web.sh を sed で改造する方式（従来のワークフローが
# 採っていた方式）は、上流が 1 行変えるだけで壊れる。
# ここでは上流の処理内容を【明示的に再実装】する。
#
# 【なぜ再実装が許容されるのか】
# 対象は「ファイルをコピーして file_packager にかける」だけで、
# ビルドロジックではない。上流が変わった場合は
# 後述の検証（生成物の実在とサイズ）で気づける。
#
# 上流との差分は 2 点のみ:
#   ・追加 mod（mods/stats_through_kills）を同梱する
#   ・日本語 .mo を同梱する
set -euo pipefail

# ラッパーリポジトリのルート（mods/ がある場所）。
WRAPPER_ROOT="${1:-..}"

if [ ! -f Makefile ] || [ ! -d data ]; then
    echo "ERROR: CDDA のソースツリー内で実行してください" >&2
    exit 1
fi

if [ -z "${EMSDK:-}" ]; then
    echo "ERROR: EMSDK が設定されていません。emsdk_env.sh を source してください。" >&2
    exit 1
fi

FILE_PACKAGER="$EMSDK/upstream/emscripten/tools/file_packager"
if [ ! -x "$FILE_PACKAGER" ]; then
    echo "ERROR: file_packager が見つかりません: $FILE_PACKAGER" >&2
    exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "$0" )" && pwd )"
# shellcheck source=./progress.sh
. "$SCRIPT_DIR/progress.sh"

started_at="$( date +%s )"
BUNDLE_DIR=web_bundle
DATA_DIR="$BUNDLE_DIR/data"

echo "=================================================================="
echo "[DATA] ゲームデータの同梱を開始"
echo "=================================================================="

# ------------------------------------------------------------------
# 手順 1: 日本語翻訳のコンパイル
# ------------------------------------------------------------------
# cataclysm-tiles.js は上位の all ターゲットに依存しないため、
# 翻訳（.mo）は自動では作られない。明示的に作る。
echo "[DATA] === 手順 1/5: 日本語翻訳のコンパイル ==="
make localization LANGUAGES=ja

MO_PATH=lang/mo/ja/LC_MESSAGES/cataclysm-dda.mo
if [ ! -s "$MO_PATH" ]; then
    echo "ERROR: 日本語翻訳が生成されませんでした: $MO_PATH" >&2
    echo "       gettext（msgfmt）がインストールされているか確認してください。" >&2
    exit 1
fi
echo "[DATA] 翻訳: $( du -h "$MO_PATH" | cut -f1 )"

# ------------------------------------------------------------------
# 手順 2: データの収集
# ------------------------------------------------------------------
echo "[DATA] === 手順 2/5: data/ と gfx/ の収集 ==="
rm -rf "$BUNDLE_DIR"
mkdir -p "$DATA_DIR"

# 上流 prepare-web.sh と同一の一覧。
cp -R data/{core,font,fontdata.json,json,mods,names,raw,motd,credits,title} "$DATA_DIR/"
cp -R gfx "$BUNDLE_DIR/"

# .DS_Store の除去（上流と同じ）。
find "$BUNDLE_DIR" -name ".DS_Store" -type f -delete

# ------------------------------------------------------------------
# 手順 3: 不要なものの除去
# ------------------------------------------------------------------
# 【なぜ除去するのか】
# ここで削るものは、そのまま .data のサイズ＝ブラウザが
# ダウンロードして展開する量に効く。
# 利用者の要求は「RAM 4GB 機で動くこと」なので、
# 使えない／使わないデータを載せない意味は大きい。
echo "[DATA] === 手順 3/5: 廃止 mod と大型タイルセットの除去 ==="

# 廃止 mod（modinfo.json に obsolete: true があるもの）。
# 選択肢に出てもロードできないので載せる意味がない。
removed=0
for MOD_DIR in "$DATA_DIR"/mods/*/ ; do
    [ -f "$MOD_DIR/modinfo.json" ] || continue
    if jq -e '.[] | select(.type == "MOD_INFO") | .obsolete' "$MOD_DIR/modinfo.json" >/dev/null 2>&1; then
        rm -rf "$MOD_DIR"
        removed=$(( removed + 1 ))
    fi
done
echo "[DATA] 廃止 mod を ${removed} 個除去しました"

# 上流が web 版で除外しているもの。
rm -rf "$DATA_DIR/mods/MA"
rm -rf "$BUNDLE_DIR/gfx/Ultica_iso"

# ------------------------------------------------------------------
# 手順 4: 追加 mod と日本語翻訳の同梱
# ------------------------------------------------------------------
echo "[DATA] === 手順 4/5: 追加 mod と日本語翻訳の同梱 ==="

if [ -d "$WRAPPER_ROOT/mods" ]; then
    cp -R "$WRAPPER_ROOT/mods/." "$DATA_DIR/mods/"
    echo "[DATA] 追加 mod を同梱しました:"
    ls -1 "$WRAPPER_ROOT/mods" | sed 's/^/[DATA]   /'
else
    echo "ERROR: 追加 mod のディレクトリが見つかりません: $WRAPPER_ROOT/mods" >&2
    exit 1
fi

# 【必須】同梱できたことを確認する。
# cp が成功しても、パスが違えば静かに別の場所へ置かれる。
if [ ! -s "$DATA_DIR/mods/stats_through_kills/modinfo.json" ]; then
    echo "ERROR: 追加 mod の同梱に失敗しました。" >&2
    exit 1
fi

mkdir -p "$BUNDLE_DIR/lang/mo/ja/LC_MESSAGES"
cp "$MO_PATH" "$BUNDLE_DIR/lang/mo/ja/LC_MESSAGES/cataclysm-dda.mo"

if [ ! -s "$BUNDLE_DIR/lang/mo/ja/LC_MESSAGES/cataclysm-dda.mo" ]; then
    echo "ERROR: 日本語翻訳の同梱に失敗しました。" >&2
    exit 1
fi
echo "[DATA] 日本語翻訳を同梱しました"

bundle_size="$( du -sh "$BUNDLE_DIR" | cut -f1 )"
echo "[DATA] 同梱前の合計: $bundle_size"

# ------------------------------------------------------------------
# 手順 5: lz4 パック
# ------------------------------------------------------------------
echo "[DATA] === 手順 5/5: file_packager による lz4 パック ==="
echo "[DATA] この工程は単一プロセスで、数分かかります。"

# 進捗バー。file_packager は内部進捗を出さないので
# 生成物の実在（0% → 100%）しか取れないが、
# 止まって見えるときにメモリ状況を出し続けることで
# ハングと「正常に重い」の切り分けができる。
cat > /tmp/data-targets.txt <<'EOF'
cataclysm-tiles.data
cataclysm-tiles.data.js
EOF

progress_monitor_start /tmp/data-targets.txt 2 "data pack (単一プロセス)" 30
trap progress_monitor_stop EXIT

pack_started_at="$( date +%s )"
"$FILE_PACKAGER" cataclysm-tiles.data \
    --js-output=cataclysm-tiles.data.js \
    --no-node \
    --preload "$BUNDLE_DIR@/" \
    --lz4
pack_elapsed=$(( $( date +%s ) - pack_started_at ))

progress_monitor_stop
trap - EXIT

for f in cataclysm-tiles.data cataclysm-tiles.data.js; do
    if [ ! -s "$f" ]; then
        echo "ERROR: 生成されていません: $f" >&2
        exit 1
    fi
done

data_size="$( du -h cataclysm-tiles.data | cut -f1 )"
data_bytes="$( wc -c < cataclysm-tiles.data )"
data_mib=$(( data_bytes / 1048576 ))
total_elapsed=$(( $( date +%s ) - started_at ))

echo "=================================================================="
echo "[DATA] 完了"
echo "[DATA] パック所要:   $( format_hms "$pack_elapsed" )"
echo "[DATA] ジョブ全体:   $( format_hms "$total_elapsed" )"
echo "[DATA] 同梱前:       $bundle_size"
echo "[DATA] .data:        $data_size (${data_mib} MiB)"
echo "=================================================================="

# ヘッダは自分で書く（ジョブ間で共有されないため。F-22-4）
step_summary_table "データ同梱" \
    "| data pack | $bundle_size 分 | $( format_hms "$pack_elapsed" ) | .data ${data_mib} MiB |"
