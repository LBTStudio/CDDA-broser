#!/usr/bin/env bash
# Read-only process monitor for Binaryen wasm-opt, used only by the isolated QA workflow.
# It preserves wasm-opt arguments and exit status; progress records go to stderr so that
# Emscripten callers which capture stdout remain semantically unchanged.
set -euo pipefail

: "${CDDA_REAL_WASM_OPT:?CDDA_REAL_WASM_OPT must name the real Binaryen wasm-opt executable}"

if [[ "${1:-}" == "--version" ]]; then
  exec "$CDDA_REAL_WASM_OPT" "$@"
fi

started_epoch="$(date +%s)"
last_heartbeat_epoch="$started_epoch"
printf '::notice title=Asyncify/wasm-opt started::pid will be reported; heartbeat interval=60 seconds\n' >&2
"$CDDA_REAL_WASM_OPT" "$@" &
child_pid="$!"
printf '::notice title=Asyncify/wasm-opt started::pid=%s elapsed=00:00:00\n' "$child_pid" >&2

while kill -0 "$child_pid" 2>/dev/null; do
  # Check completion promptly; emit telemetry no more frequently than once per
  # minute so short helper invocations do not acquire an artificial 60-second delay.
  sleep 1 &
  wait "$!" || true
  if ! kill -0 "$child_pid" 2>/dev/null; then
    break
  fi
  now_epoch="$(date +%s)"
  if (( now_epoch - last_heartbeat_epoch < 60 )); then
    continue
  fi
  last_heartbeat_epoch="$now_epoch"
  elapsed="$((now_epoch - started_epoch))"
  process_stats="$(ps -o etime=,pcpu=,rss= -p "$child_pid" 2>/dev/null | xargs || true)"
  if [[ -z "$process_stats" ]]; then
    process_stats="unavailable"
  fi
  printf '::notice title=Asyncify/wasm-opt heartbeat::pid=%s elapsed=%02d:%02d:%02d etime_cpu_rss_kib=%s\n' \
    "$child_pid" "$((elapsed / 3600))" "$(((elapsed % 3600) / 60))" "$((elapsed % 60))" "$process_stats" >&2
done

set +e
wait "$child_pid"
exit_code="$?"
set -e
finished_epoch="$(date +%s)"
elapsed="$((finished_epoch - started_epoch))"
printf '::notice title=Asyncify/wasm-opt finished::pid=%s exit=%s elapsed=%02d:%02d:%02d\n' \
  "$child_pid" "$exit_code" "$((elapsed / 3600))" "$(((elapsed % 3600) / 60))" "$((elapsed % 60))" >&2
exit "$exit_code"
