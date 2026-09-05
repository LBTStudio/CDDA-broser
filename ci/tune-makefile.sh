#!/usr/bin/env bash
# CDDA 0.I ブラウザ版: Makefile のビルド設定調整
#
# apply-patches.sh と同じ理由（全シャードでフラグを完全一致させる）で
# スクリプトに切り出してある。
#
# 使い方: cdda のチェックアウト先で
#     ../ci/tune-makefile.sh
set -euo pipefail

if [ ! -f Makefile ]; then
    echo "ERROR: CDDA のソースツリー内で実行してください" >&2
    exit 1
fi

# ------------------------------------------------------------------
# リンク最適化レベルの切り替え（Action 短縮の【最大の要】）
# ------------------------------------------------------------------
# 【まず事実】Action 4時間23分の 93% は wasm-opt 1 プロセスである。
#
#   本番 run 33730439512 の ps サンプル 2218 件を解析した結果
#   （docs/measurements/raw/2026-09-04-link-breakdown-real.log）:
#
#     clang    (コンパイル) 0:14:00   CPU 100%   RSS 1.54GB
#     wasm-opt (リンク最適化) 4:02:04   CPU 113%   RSS 6.46GB   ← 93%
#     node     (JS 最適化)  数秒      CPU 200%
#
#   さらに wasm-opt の CPU は平均 105%＝【1.05 コアしか使っていない】。
#   ubuntu-latest は 4 コアなので約 2.9 コアが 4 時間遊んでいる。
#   RSS も 6.5GB / 16GB で余裕がある。
#   → ランナーのコア増強・メモリ増強はどちらも効かない。
#     効くのは「wasm-opt の仕事量そのものを減らす」ことだけである。
#
# 【過去の記述の訂正】
# 以前ここには「-O2 は -Os より 41% 速い」「-O1 はサイズ 18 倍なので却下」
# と書いていた。どちらも【誤り】だった。246KB の小さな wasm で測ったためで、
# 本番規模に近づけて測り直すと結論が変わった。
#
# 実測（docs/measurements/raw/2026-09-04-wasm-opt-scaling.log）
# 本番ログから採取した実際の wasm-opt 引数を使用:
#
#   N=1000 (raw 438KB)          N=2000 (raw 868KB)
#     -Os  17.457s  435,596 B     -Os  34.634s  862,809 B
#     -O2  13.993s  447,282 B     -O2  27.912s  886,119 B
#     -O1   3.817s  446,905 B     -O1   7.759s  884,835 B
#
#   → 規模を 2 倍にすると時間も 2 倍。【完全に線形】。
#   → -O2 は -Os より 20%速い（41% ではない）／サイズ +2.7%
#   → -O1 は -Os より 78%速い ／ サイズは +2.6% しか増えない
#     （「18 倍」は小規模ケース特有の見かけで、実規模では起きない）
#
# 【真犯人は Asyncify ではなかった】
# pass 単位の内訳（BINARYEN_PASS_DEBUG=1, N=1000, -Os）:
#     inlining-optimizing  12.422s = 71%   ← これが本体
#     asyncify              2.747s = 16%
#     他 31 種の合計        2.266s = 13%
# 長らく「Asyncify の大域解析が重い」と説明してきたが誤りで、
# 実際はインライン展開の反復が支配的である。
# -O1 が速いのはこの反復を回さないためである。
#
# ------------------------------------------------------------------
# 【方針】用途で 2 段構えにする
# ------------------------------------------------------------------
#   配信用（既定）  CDDA_LINK_OPT=O2 → 4:02 → 約 3:14（-20%）
#   検証用          CDDA_LINK_OPT=O1 → 4:02 → 約 53 分（-78%）
#
# ゲームの改良を回すのが目的なら、毎回 4 時間待つ必要はない。
# 挙動確認は O1 で回し、公開する成果物だけ O2 で作ればよい。
#
# さらに ci/setup-binaryen.sh による wasm-opt 116→132 差し替えが
# 加わるので、実際の配信用ビルドは -O2 で【約 148 分】になる見込み。
#
# 【-O0 は依然として却下である】
# 以前 -O0 を試して 4GB 機の実プレイが明確に重くなった。
# -O0 は最適化を【飛ばす】ので Asyncify の計装が未最適化のまま残る。
# 一方 -O1 は最適化を行ったうえでインライン反復だけ省くので別物である。
# ただし -O1 成果物での実プレイ確認は必須とし、配信既定は -O2 に置く。
CDDA_LINK_OPT="${CDDA_LINK_OPT:-O2}"

# ------------------------------------------------------------------
# 【重要】-Os / -O3 / -Oz を受け付けてはいけない
# ------------------------------------------------------------------
# ci/setup-binaryen.sh が wasm-opt を版 132 に差し替えるが、
# 132 は -Os / -O3 / -Oz でリンクすると【ビルドは成功するのに
# 実行時に】次のエラーで落ちる:
#
#   failed to asynchronously prepare wasm: LinkError:
#   WebAssembly.instantiate(): Import #0 "a" "a":
#   function import requires a callable
#
# import 名の短縮が emscripten 3.1.51 の JS グルーと食い違うためである。
#
# 【なぜ「エラーで止める」形にするのか】
# 破損ビルドでも em++ の終了コードは 0 である。CI は緑になり、
# 4 時間かけて「起動しない成果物」を作って配布してしまう。
# 実測で安全が確認できているのは -O2 と -O1 だけ
# （期待値 SUM=10650 が 116 と一致）なので、
# それ以外は【入口で】弾くのが唯一の防ぎ方である。
#
# 根拠: docs/measurements/FACTS.md F-26
case "$CDDA_LINK_OPT" in
    O2|O1) ;;
    Os|O3|Oz)
        echo "ERROR: CDDA_LINK_OPT=${CDDA_LINK_OPT} は使用できません。" >&2
        echo "       wasm-opt 132 は -Os / -O3 / -Oz で【実行時に】壊れます:" >&2
        echo "         LinkError: Import #0 \"a\" \"a\": function import requires a callable" >&2
        echo "       ビルドは成功してしまうため、ここで止めています。" >&2
        echo "       O2（配信用）か O1（検証用）を指定してください。" >&2
        echo "       詳細: docs/measurements/FACTS.md F-26" >&2
        exit 1
        ;;
    *)
        echo "ERROR: CDDA_LINK_OPT は O2 か O1 を指定してください（現在: ${CDDA_LINK_OPT}）" >&2
        echo "       O2 = 配信用（約148分）/ O1 = 検証用（約53分）" >&2
        exit 1
        ;;
esac

# 【冪等にする】
# このスクリプトは 4 つのジョブ（compile 各シャード / link / bundle）で
# それぞれ実行される。さらに同じ作業ディレクトリで 2 回走ることもある。
# 「-Os が必ず存在する」前提で書くと 2 回目に set -e で落ちるので、
# 【すでに目的の値なら何もしない】形にする。
# 【置換対象を RELEASE 分岐に限定する】
# Makefile には最適化フラグ行が 2 つある:
#     699行  LDFLAGS += -Os                     ← RELEASE=1（置換したいのはこれ）
#     703行  LDFLAGS += -O1 # Emscripten ...    ← デバッグ分岐（触ってはいけない）
#
# 単純な grep 'LDFLAGS += -O1' だと 703 行に誤マッチし、
# CDDA_LINK_OPT=O1 のとき「既に O1 なので何もしない」と誤判定して
# RELEASE 分岐が -Os のまま残る。実際にこの誤判定を検出したので、
# 【行番号で範囲を絞って】判定・置換する。
#
# RELEASE 行の特定方法:
#   '# Release-mode Linker flags.' の次にある 'LDFLAGS += -O…' 行。
#   コメントを目印にすることで、値が何であっても位置を特定できる。
release_opt_line="$(
    awk '/# Release-mode Linker flags\./ { inrel = 1; next }
         inrel && /^[[:space:]]*LDFLAGS \+= -O/ { print NR; exit }
         inrel && /^[[:space:]]*else/ { exit }' Makefile
)"

if [ -z "$release_opt_line" ]; then
    echo "ERROR: RELEASE 分岐のリンク最適化フラグ行を特定できません。" >&2
    echo "       上流の Makefile が変わった可能性があります。" >&2
    grep -n -- 'LDFLAGS += -O' Makefile >&2 || true
    exit 1
fi

cur="$( sed -n "${release_opt_line}s/.*LDFLAGS += -\([A-Za-z0-9]*\).*/\1/p" Makefile )"

if [ "$cur" = "$CDDA_LINK_OPT" ]; then
    echo "[TUNE] リンク最適化は既に -${CDDA_LINK_OPT} です（何もしません / ${release_opt_line} 行目）"
else
    # 行番号を指定して置換するので、デバッグ分岐には絶対に触れない。
    sed -i "${release_opt_line}s/LDFLAGS += -${cur}/LDFLAGS += -${CDDA_LINK_OPT}/" Makefile
    case "$CDDA_LINK_OPT" in
        O2) echo "[TUNE] リンク最適化を -${cur} から -O2 に変更（実測 -20%: 4:02→約3:14 / サイズ +2.7%）" ;;
        O1) echo "[TUNE] リンク最適化を -${cur} から -O1 に変更（実測 -78%: 4:02→約53分 / サイズ +2.6%）" ;;
    esac
    echo "[TUNE]   ※ ${release_opt_line} 行目（RELEASE 分岐）のみを変更しました"
    echo "[TUNE]   ※ 検証用に短縮したい場合は CDDA_LINK_OPT=O1 を設定してください"
fi

# ------------------------------------------------------------------
# コンパイル最適化は -Os -> -O3（速度優先）
# ------------------------------------------------------------------
# マップ移動・アクティビティは mapgen / イベント / AI のホットループを
# 通る。-O3 のベクトル化とインライン展開で低性能機の 1 ターンあたり
# CPU 時間が実測で下がる。
# サイズ増は上の -Os リンク段（Binaryen のサイズ最適化）が
# モジュール全体に効くので抑えられる。
grep -q 'OPTLEVEL = -Os' Makefile
# 範囲限定 sed: emscripten の分岐だけを書き換える
# （osx / mingw の分岐はそれぞれの値を保つ）。
sed -i \
  '/else ifeq ($(NATIVE), emscripten)/,/else/ s/OPTLEVEL = -Os/OPTLEVEL = -O3/' \
  Makefile

# ------------------------------------------------------------------
# メモリ設定（4GB 機向け）
# ------------------------------------------------------------------
# 上流は即座に 512MiB を確保し 4GiB ヒープを許すが、
# ブラウザ実測で日本語メニューは 256MiB + 成長許可で起動する。
sed -i 's/-sINITIAL_MEMORY=512MB/-sINITIAL_MEMORY=256MB/' Makefile

# ワールド生成が最大メモリの瞬間（core+mod JSON ロード、overmap 生成、
# mapgen）。ALLOW_MEMORY_GROWTH では最大値は予約上限にすぎず
# 実際に確保されるわけではないが、1GiB 上限では 4GB 機の
# ワールド生成中にちょうど成長に失敗して abort した。
# 2GiB なら平常時の使用量は同じまま生成のピークを吸収できる。
sed -i 's/-sMAXIMUM_MEMORY=4GB/-sMAXIMUM_MEMORY=2GB/' Makefile

# yield パッチは overmap / mapgen の深い呼び出し連鎖の中で
# ブラウザに制御を返す。Asyncify は生きている全フレームを
# 固定バッファに巻き戻すので、上流の 16KiB では溢れて
# 静かにメモリを壊す（真っ白な画面、メニューが出ない）。
# 16MiB は 1 回だけの確保（2GiB 上限の 0.8%）で、
# 観測された最深の連鎖よりはるかに上に上限を置ける。
sed -i 's/-sASYNCIFY_STACK_SIZE=16384/-sASYNCIFY_STACK_SIZE=16777216/' Makefile

# 深く入れ子になった overmap special は 256KiB のメインスタックも圧迫する。
# wasm のスタック枯渇は mapgen の再帰中に SIGILL 相当のトラップになる。
# 4MiB の 1 回確保で余裕をもって解消する。
sed -i 's/-sSTACK_SIZE=262144/-sSTACK_SIZE=4194304/' Makefile

# ------------------------------------------------------------------
# シャーディング用の print- ターゲットを追加
# ------------------------------------------------------------------
# ビルドを並列ジョブに分割するには、Makefile が考えるオブジェクト
# 一覧を【Makefile 自身に聞く】必要がある。
# ここで一覧を推測（ls src/*.cpp など）してはいけない:
# third-party（flatbuffers / zstd / imgui）や条件分岐で増減する
# ソースを取りこぼし、リンク時に未定義シンボルになる。
#
# 冪等にするため、既に追加済みなら何もしない。
if ! grep -q '^print-%:' Makefile; then
    printf '\nprint-%%:\n\t@echo $($*)\n' >> Makefile
fi

# ------------------------------------------------------------------
# 検証
# ------------------------------------------------------------------
grep -n -E 'INITIAL_MEMORY|MAXIMUM_MEMORY|ASYNCIFY_STACK_SIZE|-sSTACK_SIZE|LDFLAGS \+= -O' Makefile
grep -q -- '-sINITIAL_MEMORY=256MB'          Makefile
grep -q -- '-sMAXIMUM_MEMORY=2GB'            Makefile
grep -q -- '-sASYNCIFY_STACK_SIZE=16777216'  Makefile
grep -q -- '-sSTACK_SIZE=4194304'            Makefile
# リンク最適化が指定どおりになっていること。
# 既定 -O2（配信用 / -20%）、CDDA_LINK_OPT=O1 なら -O1（検証用 / -78%）。
# 実測根拠: docs/measurements/raw/2026-09-04-wasm-opt-scaling.log
#
# 【RELEASE 分岐の行だけを見る】
# デバッグ分岐にも 'LDFLAGS += -O1' があるため、
# ファイル全体を grep すると誤判定する（実際に踏んだ）。
verify_line="$(
    awk '/# Release-mode Linker flags\./ { inrel = 1; next }
         inrel && /^[[:space:]]*LDFLAGS \+= -O/ { print NR; exit }
         inrel && /^[[:space:]]*else/ { exit }' Makefile
)"
verify_opt="$( sed -n "${verify_line}s/.*LDFLAGS += -\([A-Za-z0-9]*\).*/\1/p" Makefile )"
if [ "$verify_opt" != "$CDDA_LINK_OPT" ]; then
    echo "ERROR: RELEASE 分岐のリンク最適化が -${verify_opt} です（-${CDDA_LINK_OPT} のはず）" >&2
    grep -n -- 'LDFLAGS += -O' Makefile >&2 || true
    exit 1
fi
echo "[TUNE] リンク最適化を確認: ${verify_line} 行目 = -${verify_opt}"
grep -q '^print-%:'                          Makefile
# emscripten のコンパイル分岐が -O3 になっていること。
awk '/else ifeq \(\$\(NATIVE\), emscripten\)/{found=1} found && /OPTLEVEL = -O3/{ok=1} END{exit ok?0:1}' Makefile

echo "[TUNE] OK: Makefile の設定を確認した"
