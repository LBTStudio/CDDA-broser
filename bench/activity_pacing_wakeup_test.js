// Verify the exact event list embedded in the real Emscripten patch.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const inputPatch = fs.readFileSync(path.join(root, 'patches/cdda-0I-emscripten-runtime-efficiency.patch'), 'utf8');
const activityPatch = fs.readFileSync(path.join(root, 'patches/cdda-0I-emscripten-browser-runtime.patch'), 'utf8');

assert(inputPatch.includes('+void wait_for_input( int timeout_ms, bool wake_on_mouse_move = true );'));
assert(inputPatch.includes('+EM_ASYNC_JS( void, cata_web_wait_input_impl, ( int timeout_ms, int wake_on_mouse_move ), {'));
assert(inputPatch.includes('+    cata_web_wait_input_impl( timeout_ms, wake_on_mouse_move );'));
assert(activityPatch.includes('+        wait_for_input( static_cast<int>( remaining ) + 1, false );'));

const lines = inputPatch.split('\n');
const start = lines.findIndex(line => line.includes('+        var events = ['));
assert(start >= 0, 'locate actual event list in the patch');
const eventLines = [];
for (let i = start; i < lines.length; ++i) {
  const line = lines[i];
  if (!line.startsWith('+')) break;
  eventLines.push(line.slice(1).trim());
  if (line.trim().endsWith(');')) break;
}
let expression = eventLines.join(' ').replace(/^var events\s*=\s*/, '').replace(/;$/, '');
assert(expression.includes('wake_on_mouse_move ? ["mousemove"] : []'), expression);
const normalMode = vm.runInNewContext(expression, { wake_on_mouse_move: false });
const defaultMode = vm.runInNewContext(expression, { wake_on_mouse_move: true });
assert(!normalMode.includes('mousemove'), 'pacing wait must ignore pointer movement');
assert(defaultMode.includes('mousemove'), 'ordinary UI wait must still wake on pointer movement');
assert.deepEqual(Array.from(normalMode).sort(), Array.from(defaultMode).filter(e => e !== 'mousemove').sort(),
  'mouse move is the only event filtered for pacing');
for (const input of ['keydown', 'keyup', 'mousedown', 'mouseup', 'wheel', 'touchstart', 'touchend', 'input', 'compositionend']) {
  assert(normalMode.includes(input), 'real user input still wakes pacing: ' + input);
}
console.log('PASS activity pacing wakeups: pointer motion ignored, real input retained, regular UI wait unchanged');
