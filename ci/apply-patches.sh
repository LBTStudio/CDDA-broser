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
activity-perf
runtime-efficiency
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
# Danger checks have gameplay side effects; never throttle them (F-20 retracted).
if grep -q 'mon_info_update_throttled\|mon_info_update_interval_turns' src/do_turn.cpp; then
    echo "ERROR: unsafe danger-check throttling remains" >&2
    exit 1
fi
raw_calls="$( grep -c '^[[:space:]]*g->mon_info_update();' src/do_turn.cpp )"
if [ "$raw_calls" != "3" ]; then
    echo "ERROR: expected all 3 upstream danger-check call sites" >&2
    exit 1
fi
grep -q '^bool player_activity::has_progress_message() const' src/player_activity.cpp
grep -q 'u.activity.has_progress_message()' src/do_turn.cpp
grep -q 'bool has_progress_message() const;' src/player_activity.h
# F-19: IME uses explicit input scopes.
grep -q "cata_web::text_input_active" src/sdltiles.cpp

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

# ------------------------------------------------------------------
# F-29: 複数ターン行動の所要時間の可視化（activity-perf パッチ）
# ------------------------------------------------------------------
# 実行文として存在することを確認する（コメント内の言及では通さない）。
grep -q '^void sample_activity_turn( const avatar &u )'  src/do_turn.cpp
grep -q '^    sample_activity_turn( u );'                src/do_turn.cpp
grep -q '\[perf\] %1\$s: %2\$d turns in %3\$d ms'        src/do_turn.cpp

# 粉砕の進捗表示。
# 宣言（ヘッダ）と定義（実装）の両方が無いとリンクエラーになるので
# 片方だけ入った状態を検出できるように別々に確認する。
#
# 【重要】宣言の検査でファイル全体を grep してはいけない。
# `std::string get_progress_message( const player_activity & ) const override;`
# という同一の行は他の activity_actor にも存在し（実測で 3 箇所）、
# pulp のものを消してもファイル全体の grep は通ってしまう。
# 実際に negative test でこの見逃しを踏んだ。
# そこで pulp クラスの本体だけを切り出して検査する。
pulp_class_line="$( grep -n '^class pulp_activity_actor' \
                    src/activity_actor_definitions.h | head -1 | cut -d: -f1 )"
if [ -z "$pulp_class_line" ]; then
    echo "ERROR: class pulp_activity_actor が見つからない。" >&2
    exit 1
fi
# クラス本体は次の `^class ` までとする（十分な余裕をとって 120 行見る）
if ! sed -n "${pulp_class_line},+120p" src/activity_actor_definitions.h \
        | sed -n "1,/^class [a-z_]*activity_actor/p" \
        | grep -q '^ *std::string get_progress_message( const player_activity & ) const override;'; then
    echo "ERROR: pulp_activity_actor クラス内に get_progress_message の" >&2
    echo "       宣言が無い（${pulp_class_line} 行目のクラスを検査した）。" >&2
    exit 1
fi
grep -q '^std::string pulp_activity_actor::get_progress_message' \
                                                          src/activity_actor.cpp

# 計測の呼び出しが【毎ターン通る位置】にあることを行番号で検査する。
#
# 行数固定の grep -A/-B では検査できない（間に説明コメントが
# 60 行以上入る）。また「呼ばれてはいるが while ループの内側に
# 入ってしまった」場合は計測が壊れる（1 ターンで複数回加算される）。
# そこで
#     debug_hour_timer.print_time() 行 < sample_activity_turn 行
#                                     < u.update_body() 行
# を確認する。この 3 つは do_turn() の直列部分に並んでいるので、
# 順序が保たれていればループの外にあることが保証される。
hour_timer_line="$( grep -n 'g->debug_hour_timer.print_time();' src/do_turn.cpp \
                    | head -1 | cut -d: -f1 )"
sample_line="$( grep -n '^    sample_activity_turn( u );' src/do_turn.cpp \
                | head -1 | cut -d: -f1 )"
update_body_line="$( grep -n '^    u.update_body();' src/do_turn.cpp \
                     | head -1 | cut -d: -f1 )"
if [ -z "$hour_timer_line" ] || [ -z "$sample_line" ] || [ -z "$update_body_line" ]; then
    echo "ERROR: 計測呼び出しの位置検査に必要な目印が見つからない。" >&2
    echo "       hour_timer=${hour_timer_line} sample=${sample_line} update_body=${update_body_line}" >&2
    exit 1
fi
if [ "$sample_line" -le "$hour_timer_line" ] || \
   [ "$sample_line" -ge "$update_body_line" ]; then
    echo "ERROR: sample_activity_turn() が do_turn() の直列部分にない。" >&2
    echo "       期待: ${hour_timer_line} 行 < sample < ${update_body_line} 行" >&2
    echo "       実際: sample = ${sample_line} 行" >&2
    echo "       ループ内に入ると 1 ターンで複数回加算され計測が壊れる。" >&2
    exit 1
fi

# 呼び出しは 1 箇所だけであること（複数あると二重計上になる）。
sample_calls="$( grep -c '^    sample_activity_turn( u );' src/do_turn.cpp )"
if [ "$sample_calls" != "1" ]; then
    echo "ERROR: sample_activity_turn() の呼び出しが ${sample_calls} 箇所。" >&2
    echo "       期待値は 1（複数あると 1 ターンで二重に数える）。" >&2
    exit 1
fi

# Exercise actual patched control flow before any expensive compilation.
# Existing Actions jobs already call this script; no workflow changes needed.
# Dependencies: Python 3, g++ (C++17), Node.js 22 (available on ubuntu-latest).
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
python3 "$SCRIPT_DIR/../bench/runtime_efficiency_test.py" "$PWD"
python3 "$SCRIPT_DIR/../bench/input_utf8_test.py" "$PWD"
node "$SCRIPT_DIR/../bench/asset_loader_test.js"
node "$SCRIPT_DIR/../bench/ime_bridge_test.js"
node "$SCRIPT_DIR/../bench/idbfs_sync_test.js" "$PWD"

echo "[VERIFY] OK: 全 10 パッチとランタイム・IME・保存回帰テストが合格"
