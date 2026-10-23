#!/usr/bin/env bash
# Regression test for bench/run-all.sh's exit status and parent-shell counters.
set -euo pipefail

repo_root="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
tmp="$( mktemp -d )"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bench" "$tmp/bin"
cp "$repo_root/bench/run-all.sh" "$tmp/bench/run-all.sh"
cp "$repo_root/bench/yield_cost.c" "$tmp/bench/yield_cost.c"

cat > "$tmp/bin/emcc" <<'FAKE_EMCC'
#!/usr/bin/env bash
set -u
out=""
while [ "$#" -gt 0 ]; do
    if [ "$1" = "-o" ] && [ "$#" -ge 2 ]; then
        out="$2"
        shift 2
    else
        shift
    fi
done
[ -n "$out" ] || exit 2
mkdir -p "$( dirname "$out" )"
: > "$out"
[ "${FAKE_BUILD_FAIL:-}" != "yield_cost" ]
FAKE_EMCC
cat > "$tmp/bin/node" <<'FAKE_NODE'
#!/usr/bin/env bash
if [ "${1:-}" = "-e" ]; then
    exit 0
fi
if [ "${FAKE_FAIL_BENCH:-}" = "yield_cost" ]; then
    echo "TIMEOUT"
    exit 1
fi
echo "DONE"
[ "${FAKE_EXIT_NONZERO:-}" != "yield_cost" ]
FAKE_NODE
cat > "$tmp/bin/chrome" <<'FAKE_CHROME'
#!/usr/bin/env bash
exit 0
FAKE_CHROME
chmod +x "$tmp/bin/emcc" "$tmp/bin/node" "$tmp/bin/chrome"

common_env=(PATH="$tmp/bin:$PATH" CHROME_PATH="$tmp/bin/chrome" ONLY=yield_cost PORT=0)

env "${common_env[@]}" bash "$tmp/bench/run-all.sh" > "$tmp/success.log" 2>&1
grep -q "成功: 1 / 失敗: 0" "$tmp/success.log"

if env "${common_env[@]}" FAKE_FAIL_BENCH=yield_cost bash "$tmp/bench/run-all.sh" > "$tmp/run-failure.log" 2>&1; then
    echo "ERROR: runtime timeout was reported as success" >&2
    cat "$tmp/run-failure.log" >&2
    exit 1
fi
grep -q "成功: 0 / 失敗: 1" "$tmp/run-failure.log"

if env "${common_env[@]}" FAKE_EXIT_NONZERO=yield_cost bash "$tmp/bench/run-all.sh" > "$tmp/nonzero-after-done.log" 2>&1; then
    echo "ERROR: non-zero runner exit was ignored because DONE was present" >&2
    cat "$tmp/nonzero-after-done.log" >&2
    exit 1
fi
grep -q "成功: 0 / 失敗: 1" "$tmp/nonzero-after-done.log"

if env "${common_env[@]}" FAKE_BUILD_FAIL=yield_cost bash "$tmp/bench/run-all.sh" > "$tmp/build-failure.log" 2>&1; then
    echo "ERROR: compile failure was reported as success" >&2
    cat "$tmp/build-failure.log" >&2
    exit 1
fi
grep -q "成功: 0 / 失敗: 1" "$tmp/build-failure.log"

echo "PASS run-all exit status: success, runtime timeout, and build failure"
