#!/usr/bin/env node
"use strict";

// Execute the actual EM_ASM commit bridge from patched src/sdltiles.cpp.
// Each CheckMessages pass may emit at most one SDL_TEXTINPUT event; this keeps
// long pastes from flooding the bounded pending-input queue while preserving
// every byte across successive calls.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const sourceRoot = path.resolve(process.argv[2] || ".");
const wrapperRoot = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(sourceRoot, "src/sdltiles.cpp"), "utf8");
const shell = fs.readFileSync(path.join(wrapperRoot, "shell/index.html"), "utf8");

const bridgeStart = source.indexOf("static void cdda_pump_web_ime()");
assert.notEqual(bridgeStart, -1, "IME bridge missing from patched SDL source");
const bridgeEnd = source.indexOf("//Check for any window messages", bridgeStart);
assert.notEqual(bridgeEnd, -1, "IME bridge boundary missing");
const bridge = source.slice(bridgeStart, bridgeEnd);
assert(bridge.includes("static char ime_buf[SDL_TEXTINPUTEVENT_TEXT_SIZE]"));
assert(bridge.includes("if( !pending_input_queue.empty() )"), "IME must respect pending-input backpressure");
assert(bridge.includes("const int ime_bytes = EM_ASM_INT( {"));
assert(bridge.includes("st.commits[0]"), "the pending commit must not be removed before its tail is emitted");
assert(bridge.includes("st.commitOffset = end;"), "UTF-16 offset must advance after each byte-bounded chunk");
assert(bridge.includes("st.commits.shift();"), "a commit is removed only after it is fully consumed");
assert(!bridge.includes("while( EM_ASM_INT("), "one pump must not drain all text events at once");
assert(!bridge.includes("while( remaining > 0 )"), "one pump must not enqueue dozens of SDL text events");
assert.equal((bridge.match(/SDL_PushEvent\( &commit_event \)/g) || []).length, 1);
assert(bridge.includes("commit_event.text.windowID = SDL_GetWindowID( window.get() );"),
  "committed text events must target the SDL window so ImGui accepts long input");
assert(bridge.includes("edit_event.edit.windowID = SDL_GetWindowID( window.get() );"),
  "composition preview events must target the SDL window");
assert(shell.includes("window.cddaImeState = { commits: [], commitOffset: 0, preview: null };"));
assert(shell.includes("window.cddaImeState.commitOffset = 0;"), "closing a field must discard its partial commit");

const emMarker = "const int ime_bytes = EM_ASM_INT( {";
const emStart = bridge.indexOf(emMarker);
const bodyStart = bridge.indexOf("{", emStart) + 1;
const bodyEnd = bridge.indexOf("}, ime_buf, sizeof( ime_buf ) );", bodyStart);
assert(emStart >= 0 && bodyStart > emStart && bodyEnd > bodyStart, "could not extract actual EM_ASM commit body");
const jsBody = bridge.slice(bodyStart, bodyEnd);
const state = { commits: [], commitOffset: 0, preview: null };
const context = vm.createContext({
  window: { cddaImeState: state },
  HEAPU8: new Uint8Array(32),
  TextEncoder,
  TextDecoder,
});
const consume = vm.runInContext("(function($0, $1) {\n" + jsBody + "\n})", context);
const decoder = new TextDecoder("utf-8", { fatal: true });

function deliver(strings, label) {
  state.commits = strings.slice();
  state.commitOffset = 0;
  const parts = [];
  let calls = 0;
  while (state.commits.length > 0) {
    assert(++calls <= strings.join("").length * 2 + strings.length * 2 + 100,
      label + ": possible no-progress loop");
    const beforeLength = state.commits.length;
    const beforeOffset = state.commitOffset;
    const buffer = new Uint8Array(32);
    context.HEAPU8 = buffer;
    const count = consume(0, buffer.byteLength);
    assert(Number.isInteger(count) && count >= 0 && count <= 31,
      label + ": bridge returned an invalid chunk length");
    if (count === 0) {
      assert(beforeLength !== state.commits.length || beforeOffset !== state.commitOffset,
        label + ": bridge failed to make progress");
      continue;
    }
    assert.equal(buffer[count], 0, label + ": SDL text chunk must be NUL terminated");
    const chunk = decoder.decode(buffer.subarray(0, count));
    assert(Buffer.byteLength(chunk, "utf8") <= 31);
    parts.push(chunk);
  }
  assert.equal(state.commitOffset, 0, label + ": offset must reset after the queue drains");
  assert.equal(parts.join(""), strings.join(""), label + ": committed text was lost or reordered");
  return { chunks: parts.length, calls };
}

const cases = [
  ["empty commit", [""]],
  ["short ASCII", ["short message"]],
  ["exact SDL capacity", ["x".repeat(31)]],
  ["UTF-8 boundary", ["a".repeat(30) + "日本語日本語"]],
  ["past the old 4095-byte limit", ["a".repeat(4094) + "日本語".repeat(1800)]],
  ["long supplementary characters", ["🌌".repeat(3000) + "終"]],
  ["queued commit order", ["first".repeat(50), "", "日本語".repeat(90), "last"]],
];
let totalChunks = 0;
for (const [label, strings] of cases) {
  const result = deliver(strings, label);
  totalChunks += result.chunks;
}
assert(totalChunks > 1000, "long-input cases did not exercise repeated chunk consumption");
console.log("PASS IME commit bridge: UTF-8-safe 31-byte chunks, long-text preservation, commit ordering, one event per pump, and pending-input backpressure");
