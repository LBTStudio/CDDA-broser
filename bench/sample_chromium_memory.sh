#!/usr/bin/env bash
# Best-effort hosted-runner memory sampler. Sums Chromium process RSS/PSS; not device RAM.
set -euo pipefail
OUT=${1:?usage: sample_chromium_memory.sh OUTPUT_TSV NODE_PID}
ROOT_PID=${2:?usage: sample_chromium_memory.sh OUTPUT_TSV NODE_PID}
mkdir -p "$(dirname "$OUT")"
printf 'elapsed_ms\tprocesses\trss_kib_sum\tpss_kib_sum\n' > "$OUT"
started=$(date +%s%3N)
while kill -0 "$ROOT_PID" 2>/dev/null; do
  elapsed=$(( $(date +%s%3N) - started ))
  # Chromium uses several process names (including chrome-headless-shell). Match
  # the process-name prefix instead of an exact list so renderer/browser children
  # are not silently omitted on hosted Linux runners.
  pids=$(ps -eo pid=,comm= | awk '$2 ~ /^(chrome|chromium)/ {print $1}')
  count=0 rss=0 pss=0
  for pid in $pids; do
    [[ -r "/proc/$pid/status" ]] || continue
    read -r value < <(awk '/^VmRSS:/ {print $2; found=1; exit} END {if (!found) print 0}' "/proc/$pid/status")
    rss=$((rss + ${value:-0}))
    if [[ -r "/proc/$pid/smaps_rollup" ]]; then
      read -r value < <(awk '/^Pss:/ {print $2; found=1; exit} END {if (!found) print 0}' "/proc/$pid/smaps_rollup")
      pss=$((pss + ${value:-0}))
    fi
    count=$((count + 1))
  done
  printf '%s\t%s\t%s\t%s\n' "$elapsed" "$count" "$rss" "$pss" >> "$OUT"
  sleep 1
done
