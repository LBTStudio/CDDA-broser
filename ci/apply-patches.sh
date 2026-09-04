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
# 【必ず最後】に当てる。
PATCHES="
mo-reader
loader-yield
mod-finalize-yield
world-yield
json-cache
idbfs-debounce
activity-and-ime
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

echo "[VERIFY] OK: 全 7 パッチが意図どおり適用された"
