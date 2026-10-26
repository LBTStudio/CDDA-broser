#!/usr/bin/env bash
# Best-effort hosted-runner memory sampler. Sums Chromium process RSS/PSS; not device RAM.
set -euo pipefail
OUT=${1:?usage: sample_chromium_memory.sh OUTPUT_TSV NODE_PID}
ROOT_PID=${2:?usage: sample_chromium_memory.sh OUTPUT_TSV NODE_PID}
mkdir -p "$(dirname "$OUT")"
printf 'elapsed_ms\tprocesses\trss_kib_sum\tpss_kib_sum\tmatched_process_names\n' > "$OUT"
started=$(date +%s%3N)
while kill -0 "$ROOT_PID" 2>/dev/null; do
  elapsed=$(( $(date +%s%3N) - started ))
  # Chromium's comm field can differ across Playwright headless builds.
  # Inspect executable symlinks under /proc instead of relying on one process name.
  pids=()
  names=""
  for proc in /proc/[0-9]*; do
    [[ -r "$proc/comm" && -r "$proc/status" ]] || continue
    pid=${proc##*/}
    comm=$(<"$proc/comm")
    exe=$(readlink "$proc/exe" 2>/dev/null || true)
    if [[ "$comm" == chrome* || "$comm" == chromium* || "$exe" == *chrome* || "$exe" == *chromium* ]]; then
      pids+=("$pid")
      names+="$comm;"
    fi
  done
  count=0 rss=0 pss=0
  for pid in "${pids[@]}"; do
    [[ -r "/proc/$pid/status" ]] || continue
    read -r value < <(awk '/^VmRSS:/ {print $2; found=1; exit} END {if (!found) print 0}' "/proc/$pid/status")
    rss=$((rss + ${value:-0}))
    if [[ -r "/proc/$pid/smaps_rollup" ]]; then
      read -r value < <(awk '/^Pss:/ {print $2; found=1; exit} END {if (!found) print 0}' "/proc/$pid/smaps_rollup")
      pss=$((pss + ${value:-0}))
    fi
    count=$((count + 1))
  done
  printf '%s\t%s\t%s\t%s\t%s\n' "$elapsed" "$count" "$rss" "$pss" "${names%;}" >> "$OUT"
  sleep 1
done
