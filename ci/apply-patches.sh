#!/usr/bin/env bash
# CDDA 0.I ブラウザ版: パッチ適用と Makefile 調整
#
# ------------------------------------------------------------------
# なぜスクリプトに切り出したか
# ------------------------------------------------------------------
# ビルドを複数の並列ジョブに分割（シャーディング）すると、
# 全シャードと最終リンクジョブが【完全に同一のソースと同一の
# コンパイルフラグ】を持たなければならない。
# 1 バイトでも違えば、同一の .o を前提とするリンクが壊れる。
#
# ワークフロー YAML に同じ内容を 3 箇所コピーすると必ずずれるので、
# 単一のスクリプトにして全ジョブがこれを呼ぶ形にした。
# ローカルでも実行・検証できるという利点もある。
#
# 使い方: cdda のチェックアウト先で
#     ../ci/apply-patches.sh ../patches
#
# 第 1 引数: patches ディレクトリのパス（既定 ../patches）
set -euo pipefail

PATCH_DIR="${1:-../patches}"

if [ ! -f Makefile ] || [ ! -d src ]; then
    echo "ERROR: CDDA のソースツリー内で実行してください（Makefile と src/ が必要）" >&2
    exit 1
fi

# ------------------------------------------------------------------
# パッチ適用（順序に意味がある）
# ------------------------------------------------------------------
# activity-and-ime は他の 6 つが当たった後の行番号を前提にしているため
# activity-and-ime より前のものは順序が固定されている。
#
# input-queue は activity-and-ime が sdltiles.cpp に入れた
# wait_for_input_events() ラムダと pump_events() の CATA_WEB_YIELD()
# を前提に行番号が決まっているため、【activity-and-ime の後】に当てる。
PATCHES="
mo-reader
loader-yield
mod-finalize-yield
world-yield
json-cache
idbfs-debounce
activity-and-ime
input-queue
"

for name in $PATCHES; do
    f="$PATCH_DIR/cdda-0I-emscripten-$name.patch"
    if [ ! -f "$f" ]; then
        echo "ERROR: パッチが見つかりません: $f" >&2
        exit 1
    fi
    echo "[PATCH] $name"
    git apply --check "$f"
    git apply "$f"
done

git diff --check

# ------------------------------------------------------------------
# 適用結果の検証（各パッチの目印を 1 つずつ確認）
# ------------------------------------------------------------------
# パッチが「当たったのに意図した箇所ではない」事故を防ぐため、
# 変更後のソースに現れるべき文字列を実際に grep する。
echo "[VERIFY] パッチ適用結果を確認"
grep -q "MEMFS does not provide a POSIX mmap view" src/mmap_file.cpp
grep -q "Keep the browser responsive"              src/init.cpp
grep -q "World mod interactions were not covered"  src/init.cpp
# mapgen.cpp の yield 箇所。world-yield パッチが英語コメントで入れた後、
# activity-and-ime パッチが共通マクロ + 日本語コメントに置き換えるため、
# 最終状態の目印はマクロ名で確認する。
grep -q "CATA_WEB_YIELD"                           src/mapgen.cpp
grep -q "fsSyncDebounceMs"                         src/main.cpp
grep -q "mount_idbfs( idbfs_dir.c_str() )"         src/main.cpp
grep -q "fsPersistenceReady"                       src/main.cpp
grep -q "CDDA_ON_IDBFS_MOUNTED"                    src/main.cpp
grep -q "pagehide"                                 src/main.cpp
grep -q "CDDA_SET_TEXT_INPUT"                      src/sdltiles.cpp
grep -q "cdda_web_wants_ime"                       src/sdltiles.cpp
grep -q "web_data_cache_root"                      src/json_loader.cpp
grep -q "assume_immutable_root"                    src/flexbuffer_cache.cpp

# activity-and-ime パッチの目印。
# 新規ファイル 2 対が存在し、3 つの主要修正が入っていること。
test -f src/cata_web_yield.h
test -f src/cata_web_yield.cpp
test -f src/cata_web_text_input.h
test -f src/cata_web_text_input.cpp
# cata_web_yield.cpp の中身は namespace cata_web の内側なので
# 修飾名 cata_web::yield_now では出てこない。実体の目印で確認する。
grep -q "namespace cata_web"                       src/cata_web_yield.cpp
grep -q "MessageChannel"                           src/cata_web_yield.cpp
grep -q "text_input_scope"                         src/cata_web_text_input.h
# F-18: キーポーリング 100ms -> 16ms
grep -q "activity_poll_interval_ms = 16"           src/do_turn.cpp
# F-20: mon_info_update のターン数間引き
grep -q "mon_info_update_interval_turns = 16"      src/do_turn.cpp
grep -q "mon_info_update_throttled"                src/do_turn.cpp
# F-19: IME はスタック覗き見をやめて参照カウントへ
grep -q "cata_web::text_input_active"              src/sdltiles.cpp

# mon_info_update() の生呼び出しが do_turn.cpp の
# 【間引きラッパの中だけ】に閉じていることを確認する。
# 3 箇所の呼び出し点が差し替え漏れなく置き換わったかの検査。
raw_calls="$( grep -c 'g->mon_info_update()' src/do_turn.cpp )"
if [ "$raw_calls" != "2" ]; then
    echo "ERROR: do_turn.cpp の g->mon_info_update() 生呼び出しが ${raw_calls} 箇所。" >&2
    echo "       期待値は 2（間引きラッパの Emscripten 側と native 側のみ）。" >&2
    echo "       呼び出し点の差し替えが漏れている可能性がある。" >&2
    exit 1
fi
throttled_calls="$( grep -c 'mon_info_update_throttled( !u.activity )' src/do_turn.cpp )"
if [ "$throttled_calls" != "3" ]; then
    echo "ERROR: 間引き呼び出しが ${throttled_calls} 箇所。期待値は 3（613/696/762 行）。" >&2
    exit 1
fi

# ------------------------------------------------------------------
# F-28: 入力取りこぼしの修正（input-queue パッチ）
# ------------------------------------------------------------------
# 実行文として存在することを確認する（コメント内の言及では通さない）。
grep -q '^[[:space:]]*static std::deque<input_event> pending_input_queue;' \
                                                    src/sdltiles.cpp
grep -q '^[[:space:]]*stash_pending_input( last_input );'   src/sdltiles.cpp
grep -q '^[[:space:]]*return_pending_input( last_input );'  src/sdltiles.cpp
grep -q '^[[:space:]]*drain_pending_input_if_idle();'       src/sdltiles.cpp

# 【最重要の検査】pump_events() が入力を破棄していないこと。
#
# 上流の pump_events() は
#     CheckMessages();
#     last_input = input_event();   ← ここで捨てている
# という構造で、ブラウザではこれが「ターン処理中に押したキーが
# 全部消える」という致命的な症状になる（F-28）。
# 破棄の前に return_pending_input() を通っていることを確認する。
#
# 行番号ではなく前後関係で検査する理由: 上流が pump_events() の
# 中身を変えたときに、行番号ベースの検査は「当たったが意図した
# 位置ではない」を見逃す。
# -A 4 なのは、間に #endif と空行が挟まるため。
# 行頭一致にしてコメントアウトを弾く。
if ! grep -A 4 '^[[:space:]]*return_pending_input( last_input );' \
        src/sdltiles.cpp | grep -q '^[[:space:]]*last_input = input_event();'; then
    echo "ERROR: pump_events() で return_pending_input() の直後に" >&2
    echo "       last_input のクリアが来ていない。" >&2
    echo "       入力退避が pump_events() に入っていない可能性がある。" >&2
    exit 1
fi

# 排出ループ冒頭の退避が入っていること。
# switch の 577 行に散在する last_input 代入点を 1 点で拾う設計なので、
# ここが抜けると 2 個目以降の入力が全て消える。
#
# 行数固定の grep -B N では検査できない（間に説明コメントが 20 行入る）。
# 代わりに「排出ループの開始行より後、かつ switch の開始行より前」に
# stash 呼び出しがあることを行番号で検査する。
# ここが崩れるのは上流がループ構造を変えたときだけなので、
# その場合は必ず気付けるようにする。
drain_loop_line="$( grep -n 'while( SDL_PollEvent( &ev ) ) {' src/sdltiles.cpp \
                    | tail -1 | cut -d: -f1 )"
# 行頭が // でない（＝コメントアウトされていない）実行文だけを拾う。
# コメントアウトを見逃すと「検証は通ったのに機能していない」に
# なるので、ここは実行文であることを必ず確かめる。
stash_line="$( grep -n '^[[:space:]]*stash_pending_input( last_input );' \
               src/sdltiles.cpp | head -1 | cut -d: -f1 )"
process_input_line="$( grep -n 'imclient->process_input( &ev );' src/sdltiles.cpp \
                       | head -1 | cut -d: -f1 )"
if [ -z "$drain_loop_line" ] || [ -z "$stash_line" ] || [ -z "$process_input_line" ]; then
    echo "ERROR: 排出ループ / stash / process_input のいずれかが見つからない。" >&2
    echo "       drain=${drain_loop_line} stash=${stash_line} proc=${process_input_line}" >&2
    exit 1
fi
if [ "$stash_line" -le "$drain_loop_line" ] || \
   [ "$stash_line" -ge "$process_input_line" ]; then
    echo "ERROR: stash_pending_input() が排出ループ冒頭に無い。" >&2
    echo "       期待: ${drain_loop_line} 行 < stash < ${process_input_line} 行" >&2
    echo "       実際: stash = ${stash_line} 行" >&2
    echo "       上流がループ構造を変えた可能性がある。" >&2
    exit 1
fi

# キューの取り出しが 3 つの入力待ち分岐すべてに入っていること
# （inputdelay < 0 / > 0 / == 0）。1 つでも漏れると、
# 「排出ループ最後のイベントが last_input を設定しない種類」
# だったときに取りこぼす。
drain_calls="$( grep -c '^[[:space:]]*drain_pending_input_if_idle();' \
                src/sdltiles.cpp )"
if [ "$drain_calls" != "3" ]; then
    echo "ERROR: drain_pending_input_if_idle() の呼び出しが ${drain_calls} 箇所。" >&2
    echo "       期待値は 3（inputdelay の < 0 / > 0 / == 0 の各分岐）。" >&2
    exit 1
fi

echo "[VERIFY] OK: 全 8 パッチが意図どおり適用された"
