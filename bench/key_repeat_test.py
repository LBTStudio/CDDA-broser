#!/usr/bin/env python3
"""Compile source-derived input routing, FIFO helpers and actual get_input_event.

python3 bench/key_repeat_test.py /path/to/patched/cdda
SDL/rendering are stubs in the native test. The generated harness.cpp can also
be compiled with -DWEB_BROWSER_TEST -sUSE_SDL=2 for real browser/SDL event tests.
No simulation, whole-game latency or physical Chromebook claims.
"""
from pathlib import Path
import re
import subprocess
import sys

source = Path(sys.argv[1]).resolve()
root = Path(__file__).resolve().parent
out = root / 'out/key-repeat'
out.mkdir(parents=True, exist_ok=True)
sdl = (source / 'src/sdltiles.cpp').read_text()


def function(text, signature):
    start = text.index(signature)
    opening = text.index('{', start)
    depth, pos = 1, opening + 1
    while depth:
        depth += (text[pos] == '{') - (text[pos] == '}')
        pos += 1
    return text[start:pos]


helpers = sdl[sdl.index('static std::deque<input_event> pending_input_queue;'):
              sdl.index('#else // !EMSCRIPTEN')]
conversion = function(sdl, 'static input_event sdl_keysym_to_keycode_evt( const SDL_Keysym &keysym )\n{')
check = function(sdl, 'static void CheckMessages()')
loop = check[check.rindex('while( SDL_PollEvent( &ev ) ) {'):]
prefix = loop[:loop.index('        switch( ev.type ) {')]
normalizer = check[check.index('#if defined(EMSCRIPTEN)\n    // The final SDL event'):
                   check.index('    bool resized = false;')]
get_input = function(sdl, 'input_event input_manager::get_input_event(')
pump = function(sdl, 'void input_manager::pump_events()')
# Guard the actual integration, not just an independently reimplemented queue.
assert prefix.index('stash_pending_input( last_input );') < prefix.index('imclient->process_input')
assert prefix.index('imclient->process_input') < prefix.index('filter_web_key_repeat')
assert 'pending_input_queue.empty()' not in get_input, 'no SDL-bypassing fast path'
assert get_input.index('CATA_WEB_YIELD();') < get_input.index('CheckMessages();')
assert get_input.count('CheckMessages();') == get_input.count('drain_pending_input_if_idle();') == 3
assert 'drain_pending_input_if_idle' not in pump
assert pump.index('CheckMessages();') < pump.index('return_pending_input( last_input );')
# Native source must still follow the original keyup/keydown switches.
assert 'last_input = sdl_keysym_to_keycode_evt( ev.key.keysym );' in check

preamble = r'''
#include <algorithm>
#include <cassert>
#include <cstring>
#include <deque>
#include <iostream>
#include <optional>
#include <stdexcept>
#include "input_enums.h"
#include "catacharset.h"
#ifdef WEB_BROWSER_TEST
#include <SDL.h>
#include <emscripten.h>
#define EXPORT EMSCRIPTEN_KEEPALIVE
#else
#define EXPORT
using Uint32 = unsigned;
struct SDL_Keysym { int scancode = 0, sym = 0, mod = 0; };
struct SDL_Event {
    int type = 0;
    struct { int repeat = 0; SDL_Keysym keysym; } key;
    struct { int event = 0; } window;
    struct { char text[32] = {}; } text;
};
enum { SDL_KEYDOWN = 1, SDL_KEYUP, SDL_WINDOWEVENT, SDL_TEXTINPUT,
       SDL_MOUSEMOTION, SDL_WINDOWEVENT_FOCUS_LOST, SDL_WINDOWEVENT_HIDDEN,
       SDL_WINDOWEVENT_MINIMIZED, SDL_WINDOWEVENT_FOCUS_GAINED,
       SDLK_LCTRL = 1000, SDLK_LSHIFT, SDLK_LALT, SDLK_RCTRL, SDLK_RSHIFT, SDLK_RALT };
enum { KMOD_CTRL = 1, KMOD_ALT = 2, KMOD_SHIFT = 4 };
static std::deque<SDL_Event> events;
static bool text_active = false;
static int SDL_IsTextInputActive() { return text_active; }
static void SDL_StartTextInput() { text_active = true; }
static void SDL_StopTextInput() { text_active = false; }
static bool SDL_PollEvent(SDL_Event *ev) {
    if(events.empty()) return false;
    *ev = events.front(); events.pop_front(); return true;
}
static void SDL_GetMouseState(int *, int *) {}
static unsigned SDL_GetTicks() { static unsigned t = 0; return ++t; }
#endif
static input_event last_input;
static int inputdelay = 0, previously_pressed_key = 0;
static bool test_mode = false, needupdate = false;
static unsigned yields = 0, delivered = 0;
#define CATA_WEB_YIELD() (++yields)
namespace cata_web { void wait_for_input(int) {} }
namespace catacurses { int stdscr = 0; }
bool imgui_visible = false;
int present_calls = 0, periodic_present_calls = 0;
struct ui_adaptor { static bool has_imgui() { return imgui_visible; } };
void wnoutrefresh(int) {}
void refresh_display() { ++present_calls; needupdate = false; }
void try_sdl_update() { ++periodic_present_calls; }
void StartTextInput() { SDL_StartTextInput(); }
void StopTextInput() { SDL_StopTextInput(); }
struct client { void process_input(const SDL_Event *) { ++delivered; } };
static client client_instance;
static client *imclient = &client_instance;
struct input_manager {
    input_event get_input_event(keyboard_mode);
    void pump_events();
    keyboard_mode actual_keyboard_mode(keyboard_mode mode) { return mode; }
};
static input_manager manager;
'''
first_input = function((source / 'src/input.cpp').read_text(), 'int input_event::get_first_input() const')
# Retain the production drain prelude/filter and normalizer. Only the unrelated
# renderer/Android/gamepad branches and text-to-Unicode conversion are stubbed.
check_harness = 'static void CheckMessages() {\nlast_input = input_event();\nSDL_Event ev;\n' + prefix + r'''
        switch(ev.type) {
            case SDL_KEYDOWN:
                if(!SDL_IsTextInputActive()) last_input = sdl_keysym_to_keycode_evt(ev.key.keysym);
                break;
            case SDL_TEXTINPUT:
                last_input = input_event(1, input_event_t::keyboard_char);
                last_input.text = ev.text.text;
                break;
            case SDL_MOUSEMOTION:
                last_input = input_event(MouseInput::Move, input_event_t::mouse);
                break;
        }
    }
''' + normalizer + '\n}\n'
tests = r'''
extern "C" {
EXPORT int consume(int text_mode) {
    auto ev = manager.get_input_event(text_mode ? keyboard_mode::keychar : keyboard_mode::keycode);
    return ev.type == input_event_t::keyboard_code ? ev.get_first_input() : 0;
}
EXPORT void pump() { manager.pump_events(); }
EXPORT int queued() { return pending_input_queue.size(); }
EXPORT int repeat_pending() { return pending_key_repeat.has_value(); }
EXPORT void reset_state() {
    pending_input_queue.clear(); pending_key_repeat.reset();
    web_repeat_focused = true; last_input = input_event();
    SDL_Event ev; while(SDL_PollEvent(&ev)) {}
    SDL_StopTextInput(); inputdelay = 0;
}
}
#ifndef WEB_BROWSER_TEST
static void key(int sym, bool repeat = false, int type = SDL_KEYDOWN, int mod = 0) {
    SDL_Event ev; ev.type = type; ev.key.keysym.sym = sym;
    ev.key.keysym.scancode = sym; ev.key.keysym.mod = mod; ev.key.repeat = repeat;
    events.push_back(ev);
}
static void focus(int event) {
    SDL_Event ev; ev.type = SDL_WINDOWEVENT; ev.window.event = event; events.push_back(ev);
}
int main() {
    unsigned cases = 0;
    // Slow consumer: any repeat burst followed by release leaves one initial tap.
    for(int n : {1, 2, 64, 1000, 10000}) {
        for(bool pumping : {false, true}) {
            reset_state(); key('l');
            for(int i = 0; i < n; ++i) key('l', true);
            if(pumping) pump();
            key('l', false, SDL_KEYUP);
            assert(consume(0) == 'l'); assert(consume(0) == 0);
            assert(queued() == 0 && !repeat_pending()); ++cases;
        }
    }
    // Without release, one repeat is consumed once, never self-generated.
    reset_state(); key('l'); for(int i = 0; i < 1000; ++i) key('l', true);
    pump(); assert(queued() == 1 && repeat_pending());
    assert(consume(0) == 'l'); assert(consume(0) == 'l'); assert(consume(0) == 0); ++cases;
    // All three inputdelay branches and interleaved non-consuming pumps preserve FIFO.
    for(int delay : {-1, 0, 10}) for(int n = 1; n <= 60; ++n) {
        reset_state(); inputdelay = delay;
        for(int i = 0; i < n; ++i) { key(100 + i); key(100 + i, false, SDL_KEYUP); }
        for(int i = 0; i < n; ++i) { if(i % 2 == 0) pump(); assert(consume(0) == 100 + i); }
        inputdelay = 0; assert(consume(0) == 0); ++cases;
    }
    // The newest SDL keydown must not overtake older queued taps.
    reset_state(); key('a'); key('b'); key('c');
    assert(consume(0) == 'a'); key('d');
    assert(consume(0) == 'b'); assert(consume(0) == 'c'); assert(consume(0) == 'd'); ++cases;
    // New direction wins; two held directions still have only one repeat intent.
    reset_state(); key('h'); key('h', true); key('l'); key('l', true);
    assert(consume(0) == 'h'); assert(consume(0) == 'l'); assert(consume(0) == 'l');
    assert(consume(0) == 0); ++cases;
    // Releasing an unrelated key cancels old intent, not the other key's taps.
    reset_state(); key('h'); key('l'); key('h', true); key('l', false, SDL_KEYUP);
    assert(consume(0) == 'h'); assert(consume(0) == 'l'); assert(consume(0) == 0);
    key('h', true); assert(consume(0) == 'h'); ++cases;
    for(int event : {SDL_WINDOWEVENT_FOCUS_LOST, SDL_WINDOWEVENT_HIDDEN, SDL_WINDOWEVENT_MINIMIZED}) {
        reset_state(); key('l'); key('l', true); pump(); focus(event); key('l', true);
        assert(consume(0) == 'l'); assert(consume(0) == 0);
        focus(SDL_WINDOWEVENT_FOCUS_GAINED); key('h'); key('h', true);
        assert(consume(0) == 'h'); assert(consume(0) == 'h'); ++cases;
    }
    reset_state(); key('l', true, SDL_KEYDOWN, KMOD_SHIFT | KMOD_CTRL); pump();
    auto modified = manager.get_input_event(keyboard_mode::keycode);
    assert(modified.modifiers.count(keymod_t::shift) && modified.modifiers.count(keymod_t::ctrl));
    key('l', true, SDL_KEYDOWN, KMOD_SHIFT); pump(); key(SDLK_LSHIFT, false, SDL_KEYUP);
    assert(consume(0) == 0); ++cases;
    // Japanese commits are not collapsed, including repeated equal strings.
    reset_state(); key('l', true); pump(); SDL_StartTextInput();
    for(const char *text : {"日本", "日本", "包帯"}) {
        SDL_Event ev; ev.type = SDL_TEXTINPUT; std::strcpy(ev.text.text, text); events.push_back(ev);
    }
    for(const char *text : {"日本", "日本", "包帯"}) {
        assert(manager.get_input_event(keyboard_mode::keychar).text == text);
    }
    assert(consume(0) == 0); ++cases;
    // Raw filter does not consume text-mode keydown/keyup, including repeats.
    reset_state(); SDL_Event ev; ev.type = SDL_KEYDOWN; ev.key.repeat = 1;
    assert(!filter_web_key_repeat(ev, false)); ev.type = SDL_KEYUP;
    assert(!filter_web_key_repeat(ev, false)); ++cases;
    // Mouse storms do not fill the deliberate-press FIFO.
    reset_state(); key('l');
    for(int i=0;i<1000;++i) { SDL_Event ev; ev.type=SDL_MOUSEMOTION; events.push_back(ev); }
    assert(consume(0) == 'l'); assert(queued() == 0); ++cases;
    reset_state(); imgui_visible = true; inputdelay = 0; needupdate = false;
    for (int i = 0; i < 10000; ++i) assert(consume(0) == 0);
    assert(periodic_present_calls == 0 && present_calls == 0);
    needupdate = true; consume(0); assert(present_calls == 1);
    inputdelay = -1; key('l'); consume(0); assert(periodic_present_calls == 1);
    inputdelay = 10; key('l'); consume(0); assert(periodic_present_calls == 2);
    inputdelay = 0; imgui_visible = false;
    std::cout << "PASS 10000 idle activity polls: no unchanged frame copies; dirty/blocking updates preserved\n";
    assert(yields > 0 && delivered > 10000);
    std::cout << "PASS " << cases << " source-derived repeat/FIFO scenarios\n";
}
#else
int main() {
    SDL_Init(SDL_INIT_VIDEO);
    SDL_CreateWindow("input test", 0, 0, 320, 240, 0);
    SDL_StopTextInput();
    EM_ASM({ window.ready = true; });
}
#endif
'''
harness = preamble + first_input + '\n' + helpers + conversion + check_harness + pump + get_input + tests
(out / 'harness.cpp').write_text(harness)
subprocess.run(['g++', '-std=c++17', '-O2', '-DEMSCRIPTEN', '-I' + str(source / 'src'),
                '-isystem', str(source / 'src/third-party'), str(out / 'harness.cpp'),
                '-o', str(out / 'native')], check=True)
subprocess.run([str(out / 'native')], check=True)
print('PASS integration guards: poll before consume, consumer-only repeat, FIFO normalization')
