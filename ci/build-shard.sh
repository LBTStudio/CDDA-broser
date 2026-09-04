#!/usr/bin/env bash
# CDDA 0.I ブラウザ版: シャード 1 個ぶんのオブジェクトをコンパイルする
#
# ==================================================================
# このスクリプトの立ち位置
# ==================================================================
#   plan ジョブ    : ci/shard-plan.sh で 440 個を N 分割
#   compile ジョブ : このスクリプトを N 並列で実行  ← ここ
#   link ジョブ    : ci/link.sh で全 .o を集めてリンク
#
# 各 compile ジョブは【別のマシン】で走る。したがって、
# ソース・パッチ・Makefile の調整・make 変数のすべてが
# 全ジョブで同一になるよう、
# apply-patches.sh / tune-makefile.sh / make-args.sh を
# 例外なく同じ手順で通す必要がある。
#
# ==================================================================
# PCH（プリコンパイル済みヘッダ）の扱い
# ==================================================================
# CDDA の .o 生成規則は
#     $(ODIR)/%.o: $(SRC_DIR)/%.cpp $(PCH_P)
# であり、PCH を前提にしている。PCH は 1 個あたり数百 MB あり、
# 生成にも 1 分前後かかる。
#
# 【重要】PCH をアーティファクトで配ってはいけない。
# PCH には生成時の絶対パス・コンパイラのバージョン・
# 有効なマクロ（__OPTIMIZE__ 等）が焼き込まれており、
# 少しでも環境が違うと
#     error: __OPTIMIZE__ predefined macro was enabled in PCH file
#            but is currently disabled
# のような形で拒否される。しかもサイズが大きいので
# アップロード／ダウンロードの時間が生成時間を上回りかねない。
#
# したがって【各シャードで PCH をローカルに作る】。
# 1 分前後の重複コストは払うが、これは N ジョブが並列に払うので
# 全体の所要時間には 1 分ぶんしか効かない。
#
# ==================================================================
# version.h / prefix.h について
# ==================================================================
# Makefile は version / prefix ターゲットでこれらを生成する。
# 多くの TU が version.h を include するため、
# 【コンパイル前に必ず生成しておかなければならない】。
#
# さらに厄介なのは、version.h の中身が
#     git describe + git rev-parse + git diff の dirty 判定
# で決まる点である。シャードごとに中身が変わると、
# それを含む TU の内容が変わり、リンク時に ODR 違反
# （-Wodr が有効）や不整合を招きうる。
#
# 幸い、全ジョブが【同一コミットを同一手順でチェックアウトし、
# 同一パッチを適用する】ので、git の状態は一致し
# version.h の内容も一致する。この前提を壊さないことが重要で、
# だからこそパッチ適用を apply-patches.sh に一本化している。
#
# ==================================================================
# 使い方
# ==================================================================
#   cdda のチェックアウト先で
#       ../ci/build-shard.sh <シャード番号> <シャード一覧ファイル>
#
#   例:
#       ../ci/build-shard.sh 2 ../shard-plan/shard-2.txt
set -euo pipefail

SHARD_ID="${1:-}"
SHARD_LIST="${2:-}"

if [ -z "$SHARD_ID" ] || [ -z "$SHARD_LIST" ]; then
    echo "使い方: build-shard.sh <シャード番号> <シャード一覧ファイル>" >&2
    exit 1
fi

if [ ! -f "$SHARD_LIST" ]; then
    echo "ERROR: シャード一覧が見つかりません: $SHARD_LIST" >&2
    exit 1
fi

if [ ! -f Makefile ]; then
    echo "ERROR: CDDA のソースツリー内で実行してください" >&2
    exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "$0" )" && pwd )"
# shellcheck source=./make-args.sh
. "$SCRIPT_DIR/make-args.sh"
# shellcheck source=./progress.sh
. "$SCRIPT_DIR/progress.sh"

# 一覧を配列に読む。make に一度に渡してターゲットとして指定する。
# 【なぜ 1 個ずつ make を呼ばないのか】
# 1 個ずつだと make の起動と依存解析が 73 回走り、
# さらに並列化できない（-j が効かない）。
# まとめて渡せば make が自分で -j4 に振り分ける。
mapfile -t SHARD_OBJS < "$SHARD_LIST"
TOTAL="${#SHARD_OBJS[@]}"

if [ "$TOTAL" -eq 0 ]; then
    echo "ERROR: シャード一覧が空です: $SHARD_LIST" >&2
    exit 1
fi

echo "=================================================================="
echo "[SHARD $SHARD_ID] コンパイル開始"
echo "[SHARD $SHARD_ID] 担当オブジェクト数: $TOTAL"
echo "[SHARD $SHARD_ID] 並列度: $CDDA_MAKE_JOBS"
echo "=================================================================="

shard_started_at="$( date +%s )"

# ------------------------------------------------------------------
# 手順 1: version.h / prefix.h の生成
# ------------------------------------------------------------------
# ここを飛ばすと version.h が無いまま多数の TU が
# コンパイルに入り「version.h: No such file or directory」で落ちる。
echo "[SHARD $SHARD_ID] === 手順 1/3: version.h / prefix.h 生成 ==="
make -s version prefix "${CDDA_MAKE_ARGS[@]}"

if [ ! -f src/version.h ]; then
    echo "ERROR: src/version.h が生成されませんでした。" >&2
    exit 1
fi
echo "[SHARD $SHARD_ID] version.h: $( tr -d '\n' < src/version.h | tail -c 80 )"

# ------------------------------------------------------------------
# 手順 2: PCH の生成
# ------------------------------------------------------------------
echo "[SHARD $SHARD_ID] === 手順 2/3: PCH 生成 ==="
PCH_TARGET="$( make -s print-PCH_P "${CDDA_MAKE_ARGS[@]}" )"
echo "[SHARD $SHARD_ID] PCH: $PCH_TARGET"

pch_started_at="$( date +%s )"
# Makefile の PCH 規則は先頭が '-' なので失敗しても make は続行する。
# つまり make の終了コードでは PCH の失敗を検出できない。
# 【生成物の実在で判定する】。
make "${CDDA_MAKE_ARGS[@]}" "$PCH_TARGET"

if [ ! -s "$PCH_TARGET" ]; then
    echo "ERROR: PCH が生成されませんでした: $PCH_TARGET" >&2
    echo "       PCH 無しでも .o は作れるが、全 TU が全ヘッダを" >&2
    echo "       毎回パースするためコンパイル時間が数倍に膨らむ。" >&2
    echo "       分割の意味が失われるのでここで失敗させる。" >&2
    exit 1
fi
echo "[SHARD $SHARD_ID] PCH 完了: $( format_hms $(( $( date +%s ) - pch_started_at )) ) / サイズ $( du -h "$PCH_TARGET" | cut -f1 )"

# ------------------------------------------------------------------
# 手順 3: 担当オブジェクトのコンパイル（進捗バー付き）
# ------------------------------------------------------------------
echo "[SHARD $SHARD_ID] === 手順 3/3: オブジェクトのコンパイル ==="

# 【進捗バーの分子について】
# 増分ビルド（ccache ヒットや再実行）では開始時点で
# すでに .o が存在することがある。その場合バーは途中から始まる。
# これは「実際に残っている作業量」を正しく表しているので、
# あえて 0 から始める補正はしない。
pre_existing="$( count_existing_objects "$SHARD_LIST" )"
if [ "$pre_existing" -gt 0 ]; then
    echo "[SHARD $SHARD_ID] 開始時点で存在するオブジェクト: $pre_existing / $TOTAL"
    echo "[SHARD $SHARD_ID] （キャッシュ復元によるものなので進捗バーは途中から始まります）"
fi

progress_monitor_start "$SHARD_LIST" "$TOTAL" "compile shard-$SHARD_ID" 15
# make が失敗してもモニタを必ず止める。
# 止めないとジョブが終わらずタイムアウトまで待つことになる。
trap progress_monitor_stop EXIT

compile_started_at="$( date +%s )"
make -j"$CDDA_MAKE_JOBS" "${CDDA_MAKE_ARGS[@]}" "${SHARD_OBJS[@]}"
compile_elapsed=$(( $( date +%s ) - compile_started_at ))

progress_monitor_stop
trap - EXIT

# ------------------------------------------------------------------
# 完成の検証
# ------------------------------------------------------------------
# 【必須】make が 0 を返しても、担当した .o が全部あるとは限らない。
# 例えばターゲット名の綴りが違えば make は「何もすることがない」と
# 判断して 0 を返しうる。取りこぼしはリンク段まで発覚しないので、
# ここで 1 個ずつ実在を確認する。
missing=0
missing_list=''
for obj in "${SHARD_OBJS[@]}"; do
    if [ ! -s "$obj" ]; then
        missing=$(( missing + 1 ))
        [ "$missing" -le 10 ] && missing_list="${missing_list}  $obj"$'\n'
    fi
done

if [ "$missing" -gt 0 ]; then
    echo "ERROR: [SHARD $SHARD_ID] 生成されていないオブジェクトが ${missing} 個あります:" >&2
    printf '%s' "$missing_list" >&2
    [ "$missing" -gt 10 ] && echo "  （ほか $(( missing - 10 )) 個）" >&2
    exit 1
fi

shard_elapsed=$(( $( date +%s ) - shard_started_at ))
obj_bytes="$( du -sh "$CDDA_OBJ_DIR" 2>/dev/null | cut -f1 || echo '?' )"

echo "=================================================================="
echo "[SHARD $SHARD_ID] 完了: $TOTAL 個すべて生成"
echo "[SHARD $SHARD_ID] コンパイル所要: $( format_hms "$compile_elapsed" )"
echo "[SHARD $SHARD_ID] シャード全体:   $( format_hms "$shard_elapsed" )"
echo "[SHARD $SHARD_ID] オブジェクト計: $obj_bytes"
echo "=================================================================="

# ccache の効き具合を残す。次回のキャッシュ設計の判断材料になる。
if command -v ccache >/dev/null 2>&1; then
    echo "[SHARD $SHARD_ID] ccache 統計:"
    ccache -s 2>/dev/null | sed 's/^/[SHARD '"$SHARD_ID"']   /' || true
fi

# ------------------------------------------------------------------
# 実行結果ページ（Summary）への要約
# ------------------------------------------------------------------
# ログは何千行にもなるので、1 行の要約を Summary に出す。
# N シャードが並ぶので、どのシャードが遅いか一目で分かり、
# 次回のシャード数の見直しに使える。
step_summary "| compile shard-$SHARD_ID | $TOTAL 個 | $( format_hms "$compile_elapsed" ) | $obj_bytes |"
