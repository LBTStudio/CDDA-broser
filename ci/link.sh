#!/usr/bin/env bash
# CDDA 0.I ブラウザ版: 全シャードの .o を集めて wasm にリンクする
#
# ==================================================================
# このスクリプトの立ち位置
# ==================================================================
#   plan ジョブ    : ci/shard-plan.sh で 440 個を N 分割
#   compile ジョブ : ci/build-shard.sh を N 並列で実行
#   link ジョブ    : このスクリプト  ← ここ
#
# ==================================================================
# なぜリンク段は分割できないのか（時間短縮の理論的下限）
# ==================================================================
# emcc のリンクは 2 段構えである:
#
#   (1) wasm-ld による通常のリンク（シンボル解決）
#   (2) Binaryen（wasm-opt）による wasm モジュール全体の最適化
#
# 問題は (2) である。RELEASE=1 では LDFLAGS に -Os が付き、
# さらに -sASYNCIFY が指定されている。Asyncify は
# 【モジュール全体の呼び出しグラフを解析】して、
# どの関数がスタックを巻き戻す必要があるかを判定し、
# 該当関数すべてにステート機械を挿入する変換である。
#
# この解析は本質的に大域的で、しかも wasm-opt は単一スレッドで動く。
# 「モジュールを分割して並列に最適化する」ことは
# 呼び出しグラフが分断されるため原理的にできない。
#
# したがって:
#   コンパイル段 = 並列化でいくらでも縮む
#   リンク段     = 縮まない（これが所要時間の下限）
#
# 分割によって全体は「リンク段の時間 + α」まで縮むが、
# それ以上は縮まない。これは技術的な限界であり、
# 誤魔化さずに認識しておくべき事実である。
#
# ==================================================================
# 【最重要】復元した .o のタイムスタンプ問題
# ==================================================================
# ここが分割ビルドで最も事故りやすい箇所である。
#
# Makefile の規則は
#     $(ODIR)/%.o: $(SRC_DIR)/%.cpp $(PCH_P)
#     $(TARGET): $(OBJS)
# であり、make は【mtime を比較して】再ビルドを判断する。
#
# アーティファクトから .o を復元すると、その mtime は
# 「展開した時刻」ではなく実装依存であり、
# しかも同じジョブで checkout した .cpp の mtime は
# 「checkout した時刻」＝ほぼ現在時刻になる。
#
# つまり高い確率で【.cpp が .o より新しい】と判定され、
# make は 440 個を全部コンパイルし直そうとする。
# そうなると分割した意味が完全に消え、
# しかも「なぜか遅いだけで成功する」ので気づきにくい。
#
# 【対策】リンク前に .o と PCH の mtime を明示的に
# 「今」に更新し、ソースより新しい状態を作る。
#
# これは一見乱暴だが、正当性は担保されている:
#   ・全シャードは同一コミット・同一パッチ・同一 make 変数で
#     コンパイルしている（apply-patches.sh / make-args.sh で強制）
#   ・したがって復元した .o は、そのソースから作られた
#     正しい成果物であることが保証されている
#   ・実在検証（後述）で 440 個すべての存在を確認している
#
# touch の順序も重要である。PCH → .o の順に新しくする必要がある
# （.o は PCH に依存しているため、PCH が .o より新しいと
# やはり再コンパイル対象になる）。
#
# ==================================================================
# 使い方
# ==================================================================
#   cdda のチェックアウト先で
#       ../ci/link.sh <オブジェクト全一覧ファイル>
#
#   前提: 全シャードのアーティファクトが obj/tiles/ 以下に
#         すでに展開されていること。
set -euo pipefail

OBJS_ALL="${1:-}"

if [ -z "$OBJS_ALL" ]; then
    echo "使い方: link.sh <オブジェクト全一覧ファイル>" >&2
    exit 1
fi

if [ ! -f "$OBJS_ALL" ]; then
    echo "ERROR: オブジェクト一覧が見つかりません: $OBJS_ALL" >&2
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

link_started_at="$( date +%s )"

echo "=================================================================="
echo "[LINK] リンク開始"
echo "=================================================================="

# ------------------------------------------------------------------
# 手順 1: オブジェクトの完全性検証
# ------------------------------------------------------------------
# 【必須】1 個でも欠けていたらここで止める。
#
# 欠けたまま進むと wasm-ld が未定義シンボルを報告するが、
# その時点ではすでに Binaryen の前段まで時間を使っている。
# さらにエラーメッセージはマングルされた C++ シンボル名なので
# 「どのシャードが失敗したか」を読み取るのが非常に困難である。
#
# ここでファイル名として報告すれば、原因のシャードが即座に分かる。
echo "[LINK] === 手順 1/4: オブジェクトの完全性検証 ==="

total="$( grep -c . "$OBJS_ALL" )"
missing=0
empty=0
missing_list=''

while IFS= read -r obj; do
    [ -n "$obj" ] || continue
    if [ ! -f "$obj" ]; then
        missing=$(( missing + 1 ))
        [ "$(( missing + empty ))" -le 20 ] && missing_list="${missing_list}  [欠落] $obj"$'\n'
    elif [ ! -s "$obj" ]; then
        # サイズ 0 は「作られたが中身が無い」状態。
        # コンパイルが途中で殺された（OOM 等）ときに起きる。
        # 存在チェックだけでは通ってしまうので別に検出する。
        empty=$(( empty + 1 ))
        [ "$(( missing + empty ))" -le 20 ] && missing_list="${missing_list}  [空] $obj"$'\n'
    fi
done < "$OBJS_ALL"

if [ "$missing" -gt 0 ] || [ "$empty" -gt 0 ]; then
    echo "ERROR: オブジェクトが揃っていません（欠落 ${missing} 個 / 空 ${empty} 個 / 全体 ${total} 個）" >&2
    printf '%s' "$missing_list" >&2
    echo "" >&2
    echo "       原因の切り分け:" >&2
    echo "       ・欠落 → 該当シャードのジョブが失敗、または" >&2
    echo "                 アーティファクトのアップロード／展開に失敗した" >&2
    echo "       ・空   → コンパイル中にプロセスが殺された（OOM の可能性）" >&2
    echo "                 該当シャードの並列度を下げるか、シャード数を増やす" >&2
    exit 1
fi

echo "[LINK] OK: ${total} 個すべて存在（サイズ 0 のものも無し）"
echo "[LINK] オブジェクト合計サイズ: $( du -sh "$CDDA_OBJ_DIR" 2>/dev/null | cut -f1 || echo '?' )"

# ------------------------------------------------------------------
# 手順 2: version.h / prefix.h の生成
# ------------------------------------------------------------------
# リンク自体は .h を読まないが、Makefile の依存に
# src/version.h が入っているため、無いと make が
# 「version.h を作るために version ターゲットを実行」し、
# その結果 version.h の mtime が更新されて
# version.cpp の再コンパイルが誘発されうる。
#
# 先に作っておけば内容が同一なので Makefile 側の
# 「変わっていなければ書き換えない」分岐が働き、mtime も動かない。
echo "[LINK] === 手順 2/4: version.h / prefix.h 生成 ==="
make -s version prefix "${CDDA_MAKE_ARGS[@]}"

# ------------------------------------------------------------------
# 手順 3: タイムスタンプの整合
# ------------------------------------------------------------------
echo "[LINK] === 手順 3/4: タイムスタンプの整合 ==="

# PCH が無い場合は作る。
# 【なぜリンクジョブで PCH が必要なのか】
# PCH は .o の依存に入っているので、無いと make は
# 「PCH を作る → その PCH より古い .o を全部作り直す」
# と判断してしまう。存在させるだけで再コンパイルを防げる。
PCH_TARGET="$( make -s print-PCH_P "${CDDA_MAKE_ARGS[@]}" )"
if [ ! -s "$PCH_TARGET" ]; then
    echo "[LINK] PCH が無いので生成します: $PCH_TARGET"
    make "${CDDA_MAKE_ARGS[@]}" "$PCH_TARGET"
    if [ ! -s "$PCH_TARGET" ]; then
        echo "ERROR: PCH の生成に失敗しました: $PCH_TARGET" >&2
        exit 1
    fi
fi

# 【順序が重要】ソース → PCH → .o の順で新しくする。
# 逆順にすると make が再コンパイルを判断してしまう。
#
# ソースには触らない（触ると git の状態が変わり、
# version.h の dirty 判定に影響しうる）。
# PCH と .o を「今」にすれば、checkout 済みソースより
# 新しくなるので十分である。
touch "$PCH_TARGET"
sleep 1
# 1 秒待つ理由: ファイルシステムの mtime 粒度が 1 秒の場合、
# 同一秒内の touch では PCH と .o が同時刻になる。
# make は「同時刻なら再ビルド不要」と扱うのが一般的だが、
# 実装差に賭けたくないので明確に差を付ける。

# xargs でまとめて touch する。440 回 fork するより速い。
tr '\n' '\0' < "$OBJS_ALL" | xargs -0 -r touch
echo "[LINK] PCH と ${total} 個の .o を現在時刻に更新しました"

# 整合できたことの確認: make が「何も作り直す必要が無い」と
# 判断するかを -q（question モード）で問う。
#
# make -q は「更新が必要なら 1、不要なら 0」を返す。
# ターゲット自身（wasm）はまだ無いので 1 が返るのが正常であり、
# ここでは代わりに【個々の .o が最新と見なされるか】を確かめる。
stale=0
stale_list=''
# 全部調べると 440 回 make を呼ぶことになるので、
# 代表として最初の 5 個だけ検査する。
# タイムスタンプ整合は全 .o に一律 touch しているので、
# 標本で十分に判定できる。
sample=0
while IFS= read -r obj; do
    [ -n "$obj" ] || continue
    sample=$(( sample + 1 ))
    [ "$sample" -gt 5 ] && break
    if ! make -q "${CDDA_MAKE_ARGS[@]}" "$obj" 2>/dev/null; then
        stale=$(( stale + 1 ))
        stale_list="${stale_list}  $obj"$'\n'
    fi
done < "$OBJS_ALL"

if [ "$stale" -gt 0 ]; then
    echo "WARNING: 標本 5 個のうち ${stale} 個が「再ビルド必要」と判定されました:" >&2
    printf '%s' "$stale_list" >&2
    echo "         リンク時に再コンパイルが走り、分割の効果が失われます。" >&2
    echo "         タイムスタンプ整合が効いていない可能性があります。" >&2
    # 【失敗にはしない】理由:
    # 再コンパイルされても成果物は正しい。遅くなるだけである。
    # ここで止めるとビルドが完全に通らなくなり、
    # 「遅いが動く」より悪い状態になる。警告に留める。
else
    echo "[LINK] OK: 標本 5 個すべて最新と判定（再コンパイルは発生しません）"
fi

# ------------------------------------------------------------------
# 手順 4: リンク
# ------------------------------------------------------------------
echo "[LINK] === 手順 4/4: リンク（Binaryen/wasm-opt 込み） ==="
echo "[LINK] この工程は単一スレッドで、分割できません。"
echo "[LINK] ここが所要時間の理論的下限です。"

# 進捗バーの分母を 2 にする（.js と .wasm）。
# 【正直に書くと】リンクの内部進捗は外から観測できない。
# wasm-opt は進捗を出さないので、パーセンテージは
# 「生成物が出たか」の 0% → 100% しか取れない。
#
# それでも監視は回す。理由は、止まって見えるときに
# メモリ状況を出し続けることで
# 「OOM でスワップに落ちている」のか
# 「正常に重い処理をしている」のかを切り分けられるからである。
# 従来の HEARTBEAT が果たしていた役割をここが担う。
cat > /tmp/link-targets.txt <<EOF
$CDDA_TARGET_JS
$CDDA_TARGET_WASM
EOF

progress_monitor_start /tmp/link-targets.txt 2 "link (単一スレッド)" 30
trap progress_monitor_stop EXIT

link_make_started_at="$( date +%s )"
make -j"$CDDA_MAKE_JOBS" "${CDDA_MAKE_ARGS[@]}" "$CDDA_TARGET_JS"
link_elapsed=$(( $( date +%s ) - link_make_started_at ))

progress_monitor_stop
trap - EXIT

# ------------------------------------------------------------------
# 成果物の検証
# ------------------------------------------------------------------
for f in "$CDDA_TARGET_JS" "$CDDA_TARGET_WASM"; do
    if [ ! -s "$f" ]; then
        echo "ERROR: 成果物が生成されていません: $f" >&2
        exit 1
    fi
done

js_size="$( du -h "$CDDA_TARGET_JS" | cut -f1 )"
wasm_size="$( du -h "$CDDA_TARGET_WASM" | cut -f1 )"
wasm_bytes="$( wc -c < "$CDDA_TARGET_WASM" )"
total_elapsed=$(( $( date +%s ) - link_started_at ))

echo "=================================================================="
echo "[LINK] 完了"
echo "[LINK] リンク所要:   $( format_hms "$link_elapsed" )"
echo "[LINK] ジョブ全体:   $( format_hms "$total_elapsed" )"
echo "[LINK] $CDDA_TARGET_JS:   $js_size"
echo "[LINK] $CDDA_TARGET_WASM: $wasm_size"
echo "=================================================================="

# ------------------------------------------------------------------
# 4GB RAM 機で動くかの目安チェック
# ------------------------------------------------------------------
# 【なぜここで見るのか】
# 利用者の要求は「RAM 4GB の PC でも動くこと」である。
# wasm のサイズはそのままブラウザのメモリ消費に効く
# （wasm はコンパイル後のコードもメモリに載る）。
#
# tune-makefile.sh で INITIAL_MEMORY=256MB / MAXIMUM_MEMORY=2GB に
# 絞っているが、wasm 自体が肥大すればその前提が崩れる。
# ビルドのたびに気づける場所に出しておく。
#
# 60MiB を目安にしているのは、
#   ・wasm 本体 + ブラウザのコンパイル済みコード（概ね同程度）
#   ・+ MAXIMUM_MEMORY 2GB の線形メモリ
#   ・+ ブラウザ自身とタブの基礎消費
# を足して 4GB 機の空きに収まる範囲、という見積りである。
wasm_mib=$(( wasm_bytes / 1048576 ))
echo "[LINK] wasm サイズ: ${wasm_mib} MiB"
if [ "$wasm_mib" -gt 60 ]; then
    echo "[LINK] 注意: wasm が ${wasm_mib} MiB あります（目安 60 MiB 超）。"
    echo "[LINK]       4GB RAM 機での読み込みが厳しくなる可能性があります。"
    echo "[LINK]       LDFLAGS の -Os が効いているか確認してください。"
fi

# ------------------------------------------------------------------
# 実行結果ページ（Summary）への要約
# ------------------------------------------------------------------
step_summary "| link | ${total} 個 | $( format_hms "$link_elapsed" ) | js $js_size / wasm $wasm_size (${wasm_mib} MiB) |"
