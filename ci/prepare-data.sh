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
echo "[DATA] === 手順 1/6: 日本語翻訳のコンパイル ==="
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
echo "[DATA] === 手順 2/6: data/ と gfx/ の収集 ==="
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
echo "[DATA] === 手順 3/6: 廃止 mod と大型タイルセットの除去 ==="

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
echo "[DATA] === 手順 4/6: 追加 mod と日本語翻訳の同梱 ==="

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

# ------------------------------------------------------------------
# 日本語【以外】の翻訳は同梱しない
# ------------------------------------------------------------------
# 要件として明示されている。.mo は 1 言語あたり数十 MB あり、
# 増えるほど起動時のダウンロードとメモリを直接食う。
# 混入経路は 2 つある:
#   (1) make localization が別言語の .mo を作る（前回ビルドの残骸を含む）
#   (2) 上流や追加 mod が lang/mo/<other> を持ち込む
# どちらも【静かに起動を重くする】ので、ここで必ず落とす。
if [ -d "$BUNDLE_DIR/lang/mo" ]; then
    find "$BUNDLE_DIR/lang/mo" -mindepth 1 -maxdepth 1 -type d ! -name ja -exec rm -rf {} +
fi
other_mo="$( find "$BUNDLE_DIR" -name "*.mo" ! -path "*/lang/mo/ja/*" -print -quit )"
if [ -n "$other_mo" ]; then
    echo "ERROR: 日本語以外の翻訳が同梱されています: $other_mo" >&2
    echo "       この配布物は ja 以外の .mo を同梱しません（要件）。" >&2
    exit 1
fi
echo "[DATA] ja 以外の .mo が無いことを確認しました"
bundle_size="$( du -sh "$BUNDLE_DIR" | cut -f1 )"
echo "[DATA] 同梱前の合計: $bundle_size"

# ------------------------------------------------------------------
# 手順 5: パック分割のステージング
# ------------------------------------------------------------------
# 【なぜ分割するのか】
# メインメニューに到達するまでに読まれるのは、パッケージ全体の
# 約 25%（実測 5,892 ファイル中 74 / 134.6MB 中 33.6MB）にすぎない。
# 残りの大半は /data/json（世界を開始するまで一度も読まれない）と
# 未選択 tileset の画像である。
#
# そこで配布物を次の 4 種類に切り分け、シェル (shell/lazydata.js) が
# 「必要な瞬間」にだけ差し込む:
#
#   initial       : core + locale + tileset メタ + 既定 tileset の画像
#   world         : /data/json と /data/mods の実データ
#   gfx-<tileset> : 各 tileset の画像（tileset.txt / tile_config.json を除く）
#
# 【ゲーム本体には手を入れない】
# どれも file_packager --lz4 が生成する素のパックである。差し込みは
# Emscripten の LZ4 ローダー (Module.LZ4.loadPackage) を実行後に呼ぶだけで、
# ゲームコードから見れば「そのファイルが最初から在った」のと同じになる。
# packs/ を作らなければ従来の単一 .data と完全に同一の配布物になる。
echo "[DATA] === 手順 5/6: パック分割のステージング ==="

# 既定で使う tileset。この画像だけは起動パックに入れる（メニュー到達直後に
# タイルセットを解釈するため）。ここを間違えても壊れない: 実際に選ばれた
# tileset のパックは読み込み直前に差し込まれるので、遅れて届くだけになる。
DEFAULT_TILESETS=" ${CDDA_BROWSER_TILESETS:-UltimateCataclysm Larwick_Overmap} "
is_default_tileset() {
    case "$DEFAULT_TILESETS" in
        *" $1 "*) return 0 ;;
        *) return 1 ;;
    esac
}
for ts in $DEFAULT_TILESETS; do
    if [ ! -d "$BUNDLE_DIR/gfx/$ts" ]; then
        echo "ERROR: 既定 tileset が見つかりません: gfx/$ts" >&2
        echo "       CDDA_BROWSER_TILESETS で上書きできます。" >&2
        exit 1
    fi
done

PACK_ROOT=web_packs
rm -rf "$PACK_ROOT"
mkdir -p "$PACK_ROOT/initial" "$PACK_ROOT/world"

stage_file() {  # $1 = bundle からの相対パス, $2 = パック名
    local rel="$1" pack="$2"
    local dst="$PACK_ROOT/$pack/$rel"
    mkdir -p "$( dirname "$dst" )"
    # ハードリンクで複製する（実データは 1 コピーのまま）。
    ln "$BUNDLE_DIR/$rel" "$dst" 2>/dev/null || cp "$BUNDLE_DIR/$rel" "$dst"
}

count_initial=0
count_world=0
: > /tmp/pack-gfx.txt

while IFS= read -r rel; do
    case "$rel" in
        # 起動時に読まれる翻訳とフォント
        lang/*|data/font/*)
            stage_file "$rel" initial; count_initial=$(( count_initial + 1 )) ;;
        # 世界を開始するまで一切読まれない
        data/json/*)
            stage_file "$rel" world; count_world=$(( count_world + 1 )) ;;
        # MOD 一覧は起動時に走査されるので modinfo.json だけは手元に残す
        data/mods/*/modinfo.json|data/mods/default.json)
            stage_file "$rel" initial; count_initial=$(( count_initial + 1 )) ;;
        data/mods/*)
            stage_file "$rel" world; count_world=$(( count_world + 1 )) ;;
        # tileset の一覧生成と既定 tileset の解釈に必要
        gfx/*/tileset.txt|gfx/*/tile_config.json)
            stage_file "$rel" initial; count_initial=$(( count_initial + 1 )) ;;
        # 画像は選んだ tileset の分だけ後から届ける。ただし既定の
        # tileset だけは起動パックに入れる（起動直後に解釈されるため）。
        gfx/*/*)
            ts="${rel#gfx/}"; ts="${ts%%/*}"
            if is_default_tileset "$ts"; then
                stage_file "$rel" initial; count_initial=$(( count_initial + 1 ))
            else
                stage_file "$rel" "gfx-$ts"
                echo "gfx-$ts" >> /tmp/pack-gfx.txt
            fi ;;
        *)
            stage_file "$rel" initial; count_initial=$(( count_initial + 1 )) ;;
    esac
done < <( cd "$BUNDLE_DIR" && find . -type f -printf '%P\n' )

GFX_PACKS="$( sort -u /tmp/pack-gfx.txt )"
echo "[DATA] initial: ${count_initial} ファイル / world: ${count_world} ファイル"
echo "[DATA] tileset 別パック:"
for p in $GFX_PACKS; do echo "[DATA]   $p"; done

# ------------------------------------------------------------------
# 手順 6: lz4 パック
# ------------------------------------------------------------------
echo "[DATA] === 手順 6/6: file_packager による lz4 パック ==="
echo "[DATA] この工程は単一プロセスで、数分かかります。"

mkdir -p packs
rm -f packs/*.data packs/*.js packs/manifest.json

pack() {  # $1 = 出力名（.data を除く）, $2 = ステージング, $3 = .js 出力名（省略可）
    local out="$1" src="$2" jsout="${3:-${1}.js}"
    "$FILE_PACKAGER" "${out}.data" \
        --js-output="$jsout" \
        --no-node \
        --preload "${src}@/" \
        --lz4
}

# 進捗バーは最初の（= 一番大きい）パックに付ける。
cat > /tmp/data-targets.txt <<'EOF'
cataclysm-tiles.data
cataclysm-tiles.data.js
EOF

progress_monitor_start /tmp/data-targets.txt 2 "data pack (initial)" 30
trap progress_monitor_stop EXIT

pack_started_at="$( date +%s )"
pack "cataclysm-tiles" "$PACK_ROOT/initial" "cataclysm-tiles.data.js"
pack "packs/world" "$PACK_ROOT/world"
for p in $GFX_PACKS; do
    pack "packs/$p" "$PACK_ROOT/$p"
done
pack_elapsed=$(( $( date +%s ) - pack_started_at ))

progress_monitor_stop
trap - EXIT

# シェルはこの manifest の有無で「分割ビルドかどうか」を判断する。
{
    printf '{"version":1,"packs":['
    first=1
    for p in world $GFX_PACKS; do
        [ "$first" -eq 1 ] || printf ','
        printf '"%s"' "$p"
        first=0
    done
    printf '],"eager":false}'
} > packs/manifest.json

# 生成物の検証（分割は「あるべきものが無い」と静かに壊れる）
for f in cataclysm-tiles.data cataclysm-tiles.data.js packs/manifest.json packs/world.data packs/world.js; do
    if [ ! -s "$f" ]; then
        echo "ERROR: 生成されていません: $f" >&2
        exit 1
    fi
done
for p in $GFX_PACKS; do
    for ext in data js; do
        if [ ! -s "packs/$p.$ext" ]; then
            echo "ERROR: 生成されていません: packs/$p.$ext" >&2
            exit 1
        fi
    done
done

# ハードリンクのステージングを片付ける
rm -rf "$PACK_ROOT"

data_size="$( du -h cataclysm-tiles.data | cut -f1 )"
data_bytes="$( wc -c < cataclysm-tiles.data )"
data_mib=$(( data_bytes / 1048576 ))
world_mib=$(( $( wc -c < packs/world.data ) / 1048576 ))
packs_bytes="$( cat packs/*.data | wc -c )"
packs_mib=$(( packs_bytes / 1048576 ))
total_elapsed=$(( $( date +%s ) - started_at ))

echo "=================================================================="
echo "[DATA] 完了"
echo "[DATA] パック所要:   $( format_hms "$pack_elapsed" )"
echo "[DATA] ジョブ全体:   $( format_hms "$total_elapsed" )"
echo "[DATA] 同梱前:       $bundle_size"
echo "[DATA] 起動パック:   $data_size (${data_mib} MiB)  ← メニューまでこれだけ"
echo "[DATA] 追加パック:   ${packs_mib} MiB（world ${world_mib} MiB + tileset 別）"
echo "=================================================================="

# ヘッダは自分で書く（ジョブ間で共有されないため。F-22-4）
step_summary_table "データ同梱" \
    "| data pack | $bundle_size 分 | $( format_hms "$pack_elapsed" ) | 起動 ${data_mib} MiB + 追加 ${packs_mib} MiB |"
