#!/usr/bin/env bash
# CDDA 0.I ブラウザ版: リンク結果のキャッシュキー（指紋）を計算する

set -euo pipefail

objs_list="${1:-}"

if [ -z "$objs_list" ] || [ ! -f "$objs_list" ]; then
    echo "usage: link-fingerprint.sh <objs-all.txt>" >&2
    exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ------------------------------------------------------------------
# (1) 全 .o の内容ハッシュ
# ------------------------------------------------------------------
obj_count="$(grep -c . "$objs_list" || true)"
found_count="$(
    while IFS= read -r o; do
        [ -n "$o" ] || continue
        if [ -f "$o" ]; then
            echo x
        fi
    done < "$objs_list" | wc -l
)"

if [ "$obj_count" -eq 0 ] || [ "$found_count" -ne "$obj_count" ]; then
    echo "WARN: .o が ${found_count}/${obj_count} 個しかありません。" >&2
    echo "incomplete-$(date +%s)"
    exit 0
fi

obj_hash="$(
    sort "$objs_list" | tr '\n' '\0' \
        | xargs -0 sha256sum | sha256sum | cut -d' ' -f1
)"

# ------------------------------------------------------------------
# (2)(3) リンク引数と最適化レベル
# ------------------------------------------------------------------
# shellcheck source=/dev/null
source "${script_dir}/make-args.sh" 2>/dev/null || true

args_hash="$(
    {
        printf '%s\n' "${CDDA_MAKE_ARGS[@]:-}"
        echo "CDDA_LINK_OPT=${CDDA_LINK_OPT:-O2}"
    } | sha256sum | cut -d' ' -f1
)"

# ------------------------------------------------------------------
# (4)(5) Makefile と CI スクリプト
# ------------------------------------------------------------------
env_hash="$(
    {
        [ -f Makefile ] && sha256sum Makefile || echo "no-makefile"
        cat "${script_dir}"/*.sh 2>/dev/null | sha256sum || echo "no-ci-scripts"
        emcc --version 2>/dev/null | head -1 || echo "emcc-unknown"
        
        if [ -n "${EMSDK:-}" ] && [ -x "$EMSDK/upstream/bin/wasm-opt" ]; then
            "$EMSDK/upstream/bin/wasm-opt" --version 2>/dev/null | head -1
        elif command -v wasm-opt >/dev/null 2>&1; then
            wasm-opt --version 2>/dev/null | head -1
        else
            echo "wasm-opt-unknown"
        fi
    } | sha256sum | cut -d' ' -f1
)"

# ------------------------------------------------------------------
# 最終的な指紋
# ------------------------------------------------------------------
printf '%s%s%s' "$obj_hash" "$args_hash" "$env_hash" \
    | sha256sum | cut -c1-16
