# Long IME commits and input-queue backpressure — 2026-10-27

## Finding

The browser IME bridge previously removed a full committed string from
`window.cddaImeState.commits` before copying it into a 4,096-byte buffer.
Any suffix beyond the buffer was silently discarded. The C++ bridge then split
one buffer into many `SDL_TEXTINPUT` events in the same pump call; that could
overflow the separate bounded pending-input queue (64 entries), losing older
text chunks even when the committed text fit in the original buffer.

## Fix

- Keep committed strings in the browser queue and track a UTF-16 string offset.
- Encode only the next UTF-8-safe chunk that fits one SDL text-event payload.
- Inject no more than one committed-text SDL event per event pump.
- Pause injection while the game's pending-input queue is non-empty, so long
  pastes apply backpressure instead of flooding it.
- Reset the partial offset when text input is closed, alongside clearing any
  unconsumed commits.
- Keep composition preview separate; it remains intentionally truncated.

## Regression coverage

- `bench/ime_commit_chunk_test.js` executes the actual embedded JavaScript
  from the patched `src/sdltiles.cpp`, checking 31-byte payload bounds,
  code-point-safe UTF-8 boundaries, queue ordering, commits longer than the
  former 4,095-byte limit, one-event-per-pump behavior, and backpressure.
- `bench/input_utf8_test.py` continues to route bounded SDL text events through
  the real ImGui SDL backend code.
- `bench/ime_bridge_test.js` verifies closing the text field discards an
  incomplete queued commit and resets the offset.

These are source-derived and controlled-fixture tests. They are not a full
browser IME usability test or a full WebAssembly product build.


## 統合再検証：入力FIFO修正との併用

入力FIFOの出所フラグと順序維持は PR #72 で main に統合された。このIME修正はそのFIFOを前提に、未処理入力が残っている間は次のIMEチャンクを投入せず、1回のイベントポンプで1個だけSDLテキストイベントを発行する。

そのため、PR #72 統合前の成功ログだけでは両変更の共存を保証しない。統合後のPR実行では、パッチを上流0.Iへ適用する順序、FIFO回帰、4,095バイト超のASCII/CJK/絵文字、イベント上限・NUL終端・遅延compositionendによるフィールド終了後の再投入防止を一緒に検査する。ソース由来の試験が通っても、実Chromebookの日本語IME操作とフルWebAssemblyビルドは別の検証項目として残す。
