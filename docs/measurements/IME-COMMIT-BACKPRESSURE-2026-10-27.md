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
