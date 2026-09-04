#!/usr/bin/env bash
# bench/ の全ベンチをビルドして実行し、out/*.log に結果を保存する。
#
# 使い方:
#   source /path/to/emsdk/emsdk_env.sh
#   export CHROME_PATH=~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome
#   ./run-all.sh
#
# 環境変数:
#   CHROME_PATH   Chromium の実行ファイル（必須）
#   CDDA_SRC      CDDA の src/ ディレクトリ。指定すると
#                 cata_web_yield.{h,cpp} / cata_web_text_input.{h,cpp} を
#                 コピーして実装そのものの検証も走らせる（任意）
#   PORT          配信ポート（既定 8099）
#   ONLY          特定のベンチだけ走らせる（例: ONLY=craft_real）

set -u

cd "$( dirname "$0" )" || exit 1
OUT_DIR="out"
PORT="${PORT:-8099}"
ONLY="${ONLY:-}"

mkdir -p "$OUT_DIR"

# ---- 前提チェック -------------------------------------------------------
fail=0
if ! command -v emcc >/dev/null 2>&1; then
    echo "ERROR: emcc が見つかりません。emsdk_env.sh を source してください。" >&2
    fail=1
fi
if [ -z "${CHROME_PATH:-}" ] || [ ! -x "${CHROME_PATH:-}" ]; then
    echo "ERROR: CHROME_PATH が未設定か実行できません。" >&2
    echo "  例: export CHROME_PATH=~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome" >&2
    fail=1
fi
if ! node -e "require('playwright-core')" >/dev/null 2>&1; then
    echo "ERROR: playwright-core が見つかりません。npm i playwright-core してください。" >&2
    fail=1
fi
[ "$fail" -ne 0 ] && exit 1

# ---- ビルドフラグ -------------------------------------------------------
#
# 注意: ASYNCIFY_STACK_SIZE / STACK_SIZE は本番 CI（16MB / 4MB）より
# 意図的に小さくしている。検証環境の RAM が 1GB 級だと本番値では
# OOM でブラウザが落ちる。ベンチはスタックを深く使わないので 1MB で足りる。
# 詳細は README.md「ビルドフラグの注意」を参照。
EMFLAGS="-O3 -sASYNCIFY -sINITIAL_MEMORY=64MB -sALLOW_MEMORY_GROWTH"
EMFLAGS="$EMFLAGS -sASYNCIFY_STACK_SIZE=1048576 -sSTACK_SIZE=1048576"

# ---- ブラウザで走らせるベンチ一覧 ---------------------------------------
# 形式: <ソース>:<タイムアウトms>
BROWSER_BENCHES="
yield_cost.c:120000
yield_kinds.c:120000
asyncify_overhead.cpp:120000
paint_starve.c:120000
per_turn_yield.c:180000
idbfs_cost.c:180000
idbfs_scale.c:300000
idbfs_mount.c:180000
save_real.c:180000
load_yield.c:180000
load_paint.c:180000
craft_real.c:180000
activity_paths.c:900000
"

# ---- HTTP サーバ起動 ----------------------------------------------------
python3 -m http.server "$PORT" --directory "$OUT_DIR" >/dev/null 2>&1 &
SERVER_PID=$!
cleanup() { kill "$SERVER_PID" 2>/dev/null; }
trap cleanup EXIT
sleep 1
echo "== HTTP サーバ起動 (pid=$SERVER_PID, port=$PORT, root=$OUT_DIR)"
echo

ok=0
ng=0

run_browser_bench() {
    local src="$1" timeout_ms="$2"
    local name="${src%.*}"
    local ext="${src##*.}"
    local cc="emcc"
    [ "$ext" = "cpp" ] && cc="em++ -std=c++17"

    if [ -n "$ONLY" ] && [ "$ONLY" != "$name" ]; then
        return
    fi
    if [ ! -f "$src" ]; then
        echo "-- SKIP $name (ソースなし)"
        return
    fi

    echo "== BUILD $name"
    # shellcheck disable=SC2086
    if ! $cc "$src" -o "$OUT_DIR/$name.html" $EMFLAGS > "$OUT_DIR/$name.build.log" 2>&1; then
        echo "   BUILD FAILED -> $OUT_DIR/$name.build.log"
        ng=$(( ng + 1 ))
        return
    fi

    echo "== RUN   $name (timeout ${timeout_ms}ms)"
    if node run-bench-all.js "http://localhost:$PORT/$name.html" "$timeout_ms" \
            > "$OUT_DIR/$name.log" 2>&1; then
        :
    fi
    # DONE が出ていれば成功扱い。TIMEOUT なら失敗。
    if grep -q "DONE" "$OUT_DIR/$name.log"; then
        echo "   OK -> $OUT_DIR/$name.log"
        ok=$(( ok + 1 ))
    else
        echo "   TIMEOUT/FAIL -> $OUT_DIR/$name.log"
        ng=$(( ng + 1 ))
    fi
    echo
}

echo "$BROWSER_BENCHES" | while read -r entry; do
    [ -z "$entry" ] && continue
    run_browser_bench "${entry%%:*}" "${entry##*:}"
done

# ---- CDDA の実装そのものを検証するベンチ（CDDA_SRC 指定時のみ） ----------
if [ -n "${CDDA_SRC:-}" ]; then
    echo "== CDDA_SRC=$CDDA_SRC が指定されたので実装検証も実行"
    echo

    if [ -f "$CDDA_SRC/cata_web_yield.cpp" ]; then
        cp "$CDDA_SRC"/cata_web_yield.{h,cpp} . 2>/dev/null
        echo "== BUILD verify_primitive (cata_web_yield 実物)"
        # shellcheck disable=SC2086
        if em++ -std=c++17 verify_primitive.cpp cata_web_yield.cpp \
                -o "$OUT_DIR/verify_primitive.html" \
                -O2 -sASYNCIFY -sINITIAL_MEMORY=64MB -sALLOW_MEMORY_GROWTH \
                -sASYNCIFY_STACK_SIZE=1048576 -sSTACK_SIZE=1048576 \
                > "$OUT_DIR/verify_primitive.build.log" 2>&1; then
            echo "== RUN   verify_primitive"
            node run-bench-all.js "http://localhost:$PORT/verify_primitive.html" 120000 \
                > "$OUT_DIR/verify_primitive.log" 2>&1
            grep -q "DONE" "$OUT_DIR/verify_primitive.log" \
                && echo "   OK -> $OUT_DIR/verify_primitive.log" \
                || echo "   TIMEOUT/FAIL -> $OUT_DIR/verify_primitive.log"
        else
            echo "   BUILD FAILED -> $OUT_DIR/verify_primitive.build.log"
        fi
        echo

        # 厳格警告での単体コンパイル（CDDA は -Wpedantic -Werror でビルドする）
        echo "== CHECK cata_web_yield.cpp を -Wpedantic -Werror でコンパイル"
        if em++ -std=c++17 -c cata_web_yield.cpp -o "$OUT_DIR/cata_web_yield.o" \
                -Wall -Wextra -Wpedantic -Werror -DEMSCRIPTEN \
                > "$OUT_DIR/cata_web_yield.warn.log" 2>&1; then
            echo "   OK 警告ゼロ"
        else
            echo "   FAILED -> $OUT_DIR/cata_web_yield.warn.log"
        fi
        echo
    fi

    if [ -f "$CDDA_SRC/cata_web_text_input.cpp" ]; then
        cp "$CDDA_SRC"/cata_web_text_input.{h,cpp} . 2>/dev/null
        # これはブラウザ不要。純粋な C++ ロジックなのでネイティブで走らせる。
        echo "== BUILD/RUN text_input_scope_test (ネイティブ)"
        if g++ -std=c++17 -DEMSCRIPTEN -Wall -Wextra -Wpedantic \
                text_input_scope_test.cpp cata_web_text_input.cpp \
                -o "$OUT_DIR/text_input_scope_test" \
                > "$OUT_DIR/text_input_scope_test.build.log" 2>&1; then
            if "$OUT_DIR/text_input_scope_test" > "$OUT_DIR/text_input_scope_test.log" 2>&1; then
                echo "   OK -> $OUT_DIR/text_input_scope_test.log"
                sed 's/^/     /' "$OUT_DIR/text_input_scope_test.log"
            else
                echo "   ASSERT FAILED -> $OUT_DIR/text_input_scope_test.log"
            fi
        else
            echo "   BUILD FAILED -> $OUT_DIR/text_input_scope_test.build.log"
        fi
        echo

        echo "== CHECK cata_web_text_input.cpp を -Wpedantic -Werror でコンパイル"
        em_ok=1
        em++ -std=c++17 -c cata_web_text_input.cpp -o "$OUT_DIR/cata_web_text_input.o" \
            -Wall -Wextra -Wpedantic -Werror -DEMSCRIPTEN \
            > "$OUT_DIR/cata_web_text_input.warn.log" 2>&1 || em_ok=0
        [ "$em_ok" -eq 1 ] && echo "   OK (Emscripten) 警告ゼロ" \
                           || echo "   FAILED (Emscripten) -> $OUT_DIR/cata_web_text_input.warn.log"

        # 非 EMSCRIPTEN では実質空 TU になるべき（ネイティブビルドに影響しないこと）
        nat_ok=1
        g++ -std=c++17 -c cata_web_text_input.cpp -o "$OUT_DIR/cata_web_text_input_native.o" \
            -Wall -Wextra -Wpedantic -Werror \
            > "$OUT_DIR/cata_web_text_input.native.log" 2>&1 || nat_ok=0
        if [ "$nat_ok" -eq 1 ]; then
            sz=$( stat -c %s "$OUT_DIR/cata_web_text_input_native.o" )
            echo "   OK (native) ${sz} バイト = 実質空 TU"
        else
            echo "   FAILED (native) -> $OUT_DIR/cata_web_text_input.native.log"
        fi
        echo
    fi
else
    echo "-- CDDA_SRC 未指定のため実装検証（verify_primitive / text_input_scope_test）はスキップ"
    echo "   例: CDDA_SRC=/path/to/cdda/src ./run-all.sh"
    echo
fi

echo "== 完了。結果は $OUT_DIR/*.log"
echo "   数値が FACTS.md と違ったら FACTS.md を更新し、"
echo "   生ログを docs/measurements/raw/ に置いてください。"
