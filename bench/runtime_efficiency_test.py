#!/usr/bin/env python3
"""Compile actual patched wait-loop/eligibility code against counting stubs.

Usage: python3 bench/runtime_efficiency_test.py /path/to/patched/cdda
The source checkout's HEAD must be unpatched 0.I. This is a regression test
of control flow and work counts, NOT an actual-game/Chromebook benchmark.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).resolve()
root = Path(__file__).resolve().parent
out = root / 'out'
out.mkdir(exist_ok=True)


def upstream(name):
    return subprocess.check_output(['git', '-C', str(source), 'show', 'HEAD:src/' + name], text=True)


def function(text, signature):
    start = text.index(signature)
    opening = text.index('{', start)
    depth = 1
    pos = opening + 1
    while depth:
        depth += (text[pos] == '{') - (text[pos] == '}')
        pos += 1
    return text[start:pos]


def tokens(text):
    text = re.sub(r'//[^\n]*|/\*.*?\*/', '', text, flags=re.S)
    return re.sub(r'\s+', '', text)


old_turn = upstream('do_turn.cpp')
new_turn = (source / 'src/do_turn.cpp').read_text()
old_activity = upstream('player_activity.cpp')
new_activity = (source / 'src/player_activity.cpp').read_text()
# AI must be byte-equivalent modulo comments/whitespace and cooperative yields.
old_ai = function(old_turn, 'void monmove()')
new_ai = function(new_turn, 'void monmove()')
assert new_ai.count('CATA_WEB_YIELD();') == 4
assert tokens(old_ai) == tokens(new_ai.replace('CATA_WEB_YIELD();', ''))
assert 'mon_info_update_throttled' not in new_turn
assert len(re.findall(r'^\s*g->mon_info_update\(\);', new_turn, re.M)) == 3
# Validate their surrounding simulation statements, not merely call counts.
for before, after in [('g->cleanup_dead();', 'for( npc &guy'),
                      ('start = now;', 'if( u.activity )'),
                      ('}', 'u.process_turn();')]:
    pattern = re.escape(before) + r'.*?g->mon_info_update\(\);.*?' + re.escape(after)
    assert re.search(pattern, new_turn, re.S)
# Progress details stay verbatim; eligibility is the same upstream predicate.
old_progress = function(old_activity, 'std::optional<std::string> player_activity::get_progress_message(')
new_progress = function(new_activity, 'std::optional<std::string> player_activity::get_progress_message(')
assert old_progress[old_progress.index('    std::string extra_info;'):] == new_progress[new_progress.index('    std::string extra_info;'):]
assert 'completed * 1000' not in new_progress and '"Progress"' not in new_progress
# Display additions must not change reading work, XP, RNG or chapter restart.
for signature in ['void read_activity_actor::start(', 'void read_activity_actor::do_turn(',
                  'void read_activity_actor::finish(', 'std::string read_activity_actor::get_progress_message(']:
    assert function(upstream('activity_actor.cpp'), signature) == function(
        (source / 'src/activity_actor.cpp').read_text(), signature)

eligibility = function(new_activity, 'bool player_activity::has_progress_message() const')
old_checks = old_progress[old_progress.index('{') + 1:old_progress.index('    std::string extra_info;')]
old_checks = old_checks.replace('return std::optional<std::string>();', 'return false;').replace('return std::nullopt;', 'return false;')
assert tokens(eligibility[eligibility.index('{') + 1:]) == tokens(old_checks + 'return true;\n}')


def wait_block(text):
    start = text.index('    const bool player_is_sleeping = u.has_effect( effect_sleep );')
    return text[start:text.index('    m.invalidate_visibility_cache();', start)]


old_wait, new_wait = wait_block(old_turn), wait_block(new_turn)
# Optional before-source is a checkout of the previously deployed patched game.
# This compares actual control flow with identical modeled work/drawing costs,
# NOT full-game elapsed time or an assertion about the user's device.
previous_wait = None
if len(sys.argv) > 2:
    previous_wait = wait_block((Path(sys.argv[2]) / 'src/do_turn.cpp').read_text())
ids = sorted(set(re.findall(r'ACT_\w+', eligibility + old_wait + new_wait)) | {'ACT_READ', 'ACT_PULP', 'ACT_CRAFT'})
program = r'''
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <memory>
#include <optional>
#include <string>
#include <vector>
using time_duration = int;
using activity_id = int;
template<typename T> T to_seconds(int n) { return n; }
constexpr int operator""_minutes(unsigned long long n) { return n * 60; }
constexpr int operator""_turns(unsigned long long n) { return n; }
namespace cata_web {
double clock_ms = 0; double now_ms() { return clock_ms; }
bool fast = true; int paced_turns = 0;
bool activity_fast() { return fast; }
void pace_activity(bool enabled) { if (enabled) ++paced_turns; }
}
namespace calendar {
int turn = 0;
bool once_every(int n) { return turn % n == 0; }
}
static int progress_calls, key_calls, yields, redraws, full_redraws, presents, paints;
static double paint_cost = 0;
static int popup_depth = 0, progress_step = 1;
static std::string pause_label = "pause";
#define CATA_WEB_YIELD() (++yields)
#define CATA_WEB_YIELD_PAINT() (++paints, cata_web::clock_ms += paint_cost)
const int effect_sleep = 1, ACTION_PAUSE = 2;
const char *_(const char *s) { return s; }
std::string press_x(int) { ++key_calls; return pause_label; }
std::string string_format(const char *format, const std::string &s) {
    return std::string(format) == "\n+%s" ? "\n+" + s : "\n" + s + " to interrupt";
}
std::string string_format(const char *fmt, int h, int m, int s) {
    char buf[80]; std::snprintf(buf, sizeof(buf), fmt, h, m, s); return buf;
}
namespace ui_manager {
void redraw() { ++redraws; if (!popup_depth) ++full_redraws; }
void redraw_invalidated() { ++redraws; assert(popup_depth == 0); }
}
void refresh_display() { ++presents; }
struct ui_adaptor {
    struct disable_uis_below {};
    explicit ui_adaptor(disable_uis_below) { ++popup_depth; }
    ~ui_adaptor() { --popup_depth; }
};
struct static_popup {
    std::string message;
    static_popup &on_top(bool) { return *this; }
    void wait_message(const char *, const std::string &s) { message = s; }
};
struct game {
    bool first_redraw_since_waiting_started = true;
    std::unique_ptr<static_popup> wait_popup;
    int resets = 0;
    void wait_popup_reset() { wait_popup.reset(); ++resets; }
};
game *g;
struct avatar;
'''
program += 'enum Activity { ' + ', '.join(ids) + ' };\n'
program += r'''
struct player_activity {
    int type = ACT_NULL;
    bool interruptable_with_kb = true;
    bool interruptible = true;
    std::string verb = "working";
    int id() const { return type; }
    bool is_interruptible() const { return interruptible; }
    const std::string &get_verb() const { return verb; }
    bool has_progress_message() const;
    std::optional<std::string> get_progress_message(const avatar &) const {
        ++progress_calls;
        if (!has_progress_message()) return std::nullopt;
        return verb + ": " + std::to_string(calendar::turn / progress_step);
    }
};
struct avatar {
    player_activity activity;
    struct effect { int start = 0; int get_start_time() const { return start; } } sleep_effect;
    const effect &get_effect(int) const { return sleep_effect; }
    bool sleeping = false;
    bool has_effect(int) const { return sleeping; }
};
'''
program += eligibility + '\n'
program += 'void old_wait(avatar &u) {\n' + old_wait + '}\n'
program += 'void new_wait(avatar &u) {\n' + new_wait + '}\n'
if previous_wait is not None:
    program += '#define HAS_PREVIOUS_WAIT\nvoid previous_wait(avatar &u) {\n' + previous_wait + '}\n'
program += r'''
int main() {
#if !defined(EMSCRIPTEN)
    (void)paints; (void)paint_cost;
    int scenarios = 0;
    for (int type = 0; type < TYPE_COUNT; ++type) {
        for (int flags = 0; flags < 16; ++flags) {
            avatar u;
            u.activity.type = type;
            u.sleeping = flags & 1;
            u.activity.verb = (flags & 2) ? "" : "working";
            u.activity.interruptible = flags & 4;
            u.activity.interruptable_with_kb = flags & 8;
            game old_game, new_game;
            int old_calls = 0, new_calls = 0;
            int old_keys = 0, new_keys = 0;
            for (int turn = 1; turn <= 1800; ++turn) {
                calendar::turn = turn;
                g = &old_game; progress_calls = key_calls = redraws = yields = full_redraws = presents = 0;
                old_wait(u);
                old_calls += progress_calls; old_keys += key_calls;
                const int expected_redraws = redraws, expected_full_redraws = full_redraws;
                g = &new_game; progress_calls = key_calls = redraws = yields = full_redraws = presents = 0;
                new_wait(u);
                new_calls += progress_calls; new_keys += key_calls;
                assert(yields >= 1); // includes sleep and non-display turns
#if defined(EMSCRIPTEN)
                assert(redraws <= expected_redraws); // only identical popup draws may disappear
#else
                assert(redraws == expected_redraws);
#endif
                assert(full_redraws == expected_full_redraws);
                assert(old_game.resets == new_game.resets);
                assert(old_game.first_redraw_since_waiting_started == new_game.first_redraw_since_waiting_started);
                assert(bool(old_game.wait_popup) == bool(new_game.wait_popup));
                if (old_game.wait_popup) assert(old_game.wait_popup->message == new_game.wait_popup->message);
            }
            assert(new_calls <= old_calls && new_keys <= old_keys);
            if (type == ACT_READ && flags == 12) {
                assert(old_calls == 1800 && new_calls == 31);
                assert(old_keys == 1800 && new_keys == 31);
                printf("progress construction (1800 turns): %d -> %d calls; display unchanged\n", old_calls, new_calls);
            }
            // Activity completion resets the popup immediately, then restarting
            // displays immediately even away from a calendar refresh boundary.
            calendar::turn = 1801;
            for (int next : {ACT_NULL, ACT_READ, ACT_AIM, ACT_PULP}) {
                u.activity.type = next; u.sleeping = false; u.activity.verb = "new";
                g = &old_game; old_wait(u);
                g = &new_game; new_wait(u);
                assert(bool(old_game.wait_popup) == bool(new_game.wait_popup));
                assert(old_game.resets == new_game.resets);
                if (old_game.wait_popup) assert(old_game.wait_popup->message == new_game.wait_popup->message);
            }
            ++scenarios;
        }
    }
    printf("PASS: %d scenarios, upstream full-UI cadence/visible text/reset; AI order preserved\n", scenarios);
#else
    // All eligibility/interruptibility combinations are still respected on web.
    for (int type = 0; type < TYPE_COUNT; ++type) {
        for (int flags = 0; flags < 16; ++flags) {
            avatar u; game state; g = &state;
            u.activity.type = type; u.sleeping = flags & 1;
            u.activity.verb = flags & 2 ? "" : "working";
            u.activity.interruptible = flags & 4; u.activity.interruptable_with_kb = flags & 8;
            calendar::turn = 1; cata_web::clock_ms += 1000;
            new_wait(u);
            assert(bool(g->wait_popup) == (u.sleeping || u.activity.has_progress_message()));
            if (g->wait_popup && !u.sleeping) {
                assert((g->wait_popup->message.find("to interrupt") != std::string::npos) ==
                       (u.activity.interruptible && u.activity.interruptable_with_kb));
            }
        }
    }
    avatar u; u.activity.type = ACT_READ;
    game state; g = &state;
    calendar::turn = 1; cata_web::clock_ms += 1000;
    new_wait(u); const double started = cata_web::clock_ms;
    paints = progress_calls = 0;
    calendar::turn = 60; cata_web::clock_ms = started + 99;
    new_wait(u); assert(progress_calls == 0 && paints == 0); // calendar boundary must not bypass cap
    calendar::turn = 61; cata_web::clock_ms = started + 100;
    new_wait(u); assert(progress_calls == 1 && paints == 1);
    assert(g->wait_popup->message.find("working: 61") == 0);
    // At the reported five turns/second, time alone must never trigger frames.
    for (int t = 62; t < 90; ++t) {
        calendar::turn = t; cata_web::clock_ms += 200;
        progress_calls = paints = 0; new_wait(u);
        assert(progress_calls == 0 && paints == 0);
    }
    // Full refresh requested inside the cap is latched, not lost forever.
    calendar::turn = 120; new_wait(u);
    const double capped = cata_web::clock_ms;
    calendar::turn = 300; cata_web::clock_ms = capped + 50;
    full_redraws = paints = 0; new_wait(u); assert(full_redraws == 0 && paints == 0);
    calendar::turn = 301; cata_web::clock_ms = capped + 100;
    new_wait(u); assert(full_redraws == 1 && paints == 1);
    // Type changes, sleep entry/exit, completion, external popup close and restart are immediate.
    u.activity.type = ACT_PULP; paints = 0; new_wait(u); assert(paints == 1);
    u.sleep_effect.start = calendar::turn - 3600; u.sleeping = true;
    new_wait(u); assert(g->wait_popup->message == "Wait till you wake up…\n+01:00:00");
    calendar::turn += 5; cata_web::clock_ms += 100;
    paints = progress_calls = 0; new_wait(u);
    assert(paints == 0 && g->wait_popup->message.find("+01:00:00") != std::string::npos);
    calendar::turn = 360; cata_web::clock_ms += 100;
    new_wait(u); assert(g->wait_popup->message.find("+01:00:59") != std::string::npos);
    assert(g->wait_popup->message.find('%') == std::string::npos);
    calendar::turn = 420; u.sleep_effect.start = calendar::turn + 1; cata_web::clock_ms += 100;
    new_wait(u); assert(g->wait_popup->message.find("+00:00:00") != std::string::npos);
    u.sleeping = false; new_wait(u); assert(g->wait_popup->message.find("working:") == 0);
    u.activity.type = ACT_NULL; new_wait(u); assert(!g->wait_popup);
    u.activity.type = ACT_READ; new_wait(u); assert(g->wait_popup);
    g->wait_popup.reset(); paints = 0; new_wait(u); assert(paints == 1);
    pause_label = "new key"; calendar::turn += 60; cata_web::clock_ms += 100; new_wait(u);
    assert(g->wait_popup->message.find("new key") != std::string::npos);
    // An unchanged sample causes no transfer, but consumes the pending request.
    progress_step = 1000000; calendar::turn += 60; cata_web::clock_ms += 100; new_wait(u);
    calendar::turn += 120; cata_web::clock_ms += 100; paints = progress_calls = 0; new_wait(u);
    assert(paints == 0 && progress_calls == 1);
    ++calendar::turn; cata_web::clock_ms += 1; new_wait(u); assert(progress_calls == 1);
    // A costly frame must not trigger a catch-up burst on later slow turns.
    paint_cost = 250; g->wait_popup.reset(); new_wait(u);
    paints = progress_calls = 0;
    calendar::turn = 720; new_wait(u); assert(paints == 0 && progress_calls == 0);
    cata_web::clock_ms += 99; new_wait(u); assert(progress_calls == 0);
    ++calendar::turn; cata_web::clock_ms += 1; new_wait(u); assert(progress_calls == 1);
    paint_cost = 0; progress_step = 1;
    // Fast simulation: 8h sleep must not mean 481 transfers or 17 full frames
    // when all 28800 turns fit in 288ms. Compare upstream calls, NOT CPU speed.
    for (bool sleeping : {false, true}) {
        avatar active; active.sleeping = sleeping; active.activity.type = ACT_READ;
        game before, after;
        int old_presents = 0, new_presents = 0, old_full = 0, new_full = 0;
        cata_web::clock_ms += 10000; const double begin = cata_web::clock_ms;
        for (int t = 1; t <= 28800; ++t) {
            calendar::turn = t; cata_web::clock_ms = begin + t / 100.0;
            g = &before; presents = full_redraws = 0; old_wait(active);
            old_presents += presents; old_full += full_redraws;
            g = &after; presents = full_redraws = 0; new_wait(active);
            new_presents += presents; new_full += full_redraws;
        }
        assert(old_presents == 481 && new_presents == 3 && new_full == 1);
        printf("PASS fast %s model: %d -> %d transfers; %d -> %d full draws (not elapsed-time benchmark)\n",
               sleeping ? "sleep" : "reading", old_presents, new_presents, old_full, new_full);
    }
    // Check calendar requests separately from their bounded delivery time.
    // First aid at turn 5 is only 800ms after the initial frame at turn 1;
    // its 1s ceiling defers that request to turn 6. Include turn 1801 to
    // observe the last deferred request instead of declaring it lost.
    for (int kind : {ACT_READ, ACT_CRAFT, ACT_FIRSTAID, ACT_AUTODRIVE}) {
        for (bool sleeping : {false, true}) {
            avatar active; active.sleeping = sleeping; active.activity.type = kind;
            game before, after; int upstream_full = 0, candidate_full = 0;
            cata_web::clock_ms += 10000;
            for (int t = 1; t <= 1801; ++t) {
                calendar::turn = t; cata_web::clock_ms += 200;
                g = &before; full_redraws = 0; old_wait(active); upstream_full += full_redraws;
                g = &after; full_redraws = 0; new_wait(active); candidate_full += full_redraws;
            }
            printf("PASS cadence delivery: kind=%d sleeping=%d upstream=%d candidate=%d\n",
                   kind, sleeping, upstream_full, candidate_full);
            assert(upstream_full == candidate_full);
        }
    }
#ifdef HAS_PREVIOUS_WAIT
    for (int work_ms : {1, 20, 200}) {
        double elapsed[2]; unsigned long long checksums[2];
        for (int candidate = 0; candidate < 2; ++candidate) {
            avatar active; active.activity.type = ACT_READ; game state; g = &state;
            cata_web::clock_ms += 10000; const double begin = cata_web::clock_ms;
            // Declared cost model: 30ms per presentation, identical simulated work.
            // Progress stays constant in BOTH versions, isolating redraw cadence.
            paint_cost = 30; progress_step = 1000000; paints = full_redraws = 0;
            unsigned long long sum = 0;
            for (int t = 1; t <= 7200; ++t) {
                calendar::turn = t; cata_web::clock_ms += work_ms;
                sum = sum * 33 + t;
                if (candidate) new_wait(active); else previous_wait(active);
            }
            elapsed[candidate] = cata_web::clock_ms - begin; checksums[candidate] = sum;
            printf("MODEL reading 7200 turns: version=%s work_ms=%d paint_ms=30 elapsed_ms=%.0f frames=%d full=%d checksum=%llu\n",
                   candidate ? "candidate" : "deployed", work_ms, elapsed[candidate], paints, full_redraws, sum);
        }
        assert(checksums[0] == checksums[1]); assert(elapsed[1] <= elapsed[0]);
    }
#endif
    // Toggle during any progress activity, not only reading. A mode switch
    // refreshes immediately even without crossing the calendar boundary.
    for (int kind : {ACT_READ, ACT_CRAFT, ACT_PULP, ACT_FIRSTAID, ACT_AUTODRIVE, ACT_NULL}) {
        avatar active; active.activity.type = kind; game state; g = &state;
        cata_web::fast = true; cata_web::paced_turns = 0;
        calendar::turn = 77; cata_web::clock_ms += 10000; new_wait(active);
        assert(cata_web::paced_turns == 0);
        cata_web::fast = false; paints = 0; new_wait(active);
        assert(cata_web::paced_turns == (kind != ACT_AUTODRIVE && kind != ACT_NULL));
        if (kind != ACT_NULL) assert(paints == 1);
        cata_web::fast = true; paints = 0; new_wait(active);
        if (kind != ACT_NULL) assert(paints == 1);
        active.sleeping = true; cata_web::fast = false;
        cata_web::paced_turns = 0; new_wait(active);
        assert(cata_web::paced_turns == (kind != ACT_AUTODRIVE));
    }
    cata_web::fast = true;
    puts("PASS web: cadence, immediate mode toggles, no blocker above persistent popup, pacing eligibility");
#endif
}
'''.replace('TYPE_COUNT', str(len(ids)))
(out / 'progress-harness.cpp').write_text(program)
with tempfile.TemporaryDirectory(prefix='runtime-', dir=out) as tmp:
    cpp, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
    cpp.write_text(program)
    for defines in [[], ['-DEMSCRIPTEN']]:
        subprocess.run(['g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', *defines, str(cpp), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)

# The entire progress-detail suffix now matches upstream byte-for-byte. Its
# skill XP getter and reading start/do_turn/finish are checked unchanged above.
print('PASS reading display: upstream XP only; no interval percentage or reading logic changes')

# Exercise the actual scheduler functions against a deterministic clock.
yield_source = (source / 'src/cata_web_yield.cpp').read_text()
program = r'''
#include <cassert>
#include <cstdio>
#include <functional>
double clock_ms = 0, last_browser_yield_ms = 0;
bool yield_in_progress = false;
int calls = 0;
std::function<void()> hook;
double emscripten_get_now() { return clock_ms; }
void cata_web_yield_impl() { ++calls; if (hook) hook(); }
void cata_web_yield_paint_impl() { ++calls; }
int waited_ms = 0;
void cata_web_wait_input_impl(int ms) { ++calls; waited_ms += ms; clock_ms += ms; }
namespace cata_web {
'''
program += 'double now_ms() { return emscripten_get_now(); }\n'
for sig in ['void yield_now()', 'void wait_for_input(', 'void yield_paint()', 'bool yield_if_due(', 'void pace_activity(']:
    program += function(yield_source, sig) + '\n'
program += r'''
}
int main() {
    double a = 0, b = 0;
    clock_ms = 100;
    assert(cata_web::yield_if_due(a, 16));
    assert(!cata_web::yield_if_due(b, 16)); assert(calls == 1);
    clock_ms = 115; assert(!cata_web::yield_if_due(b, 16));
    clock_ms = 116; assert(cata_web::yield_if_due(b, 16));
    clock_ms = 200;
    hook = [&] {
        cata_web::yield_now(); cata_web::yield_paint(); cata_web::wait_for_input(16);
        assert(!cata_web::yield_if_due(b, 16));
        clock_ms += 50; // resume clock excludes time spent yielded
    };
    cata_web::yield_now(); assert(calls == 3 && !yield_in_progress);
    assert(last_browser_yield_ms == 250); hook = {};
    clock_ms = 265; assert(!cata_web::yield_if_due(b, 16));
    clock_ms = 266; assert(cata_web::yield_if_due(b, 16));
    cata_web::yield_paint(); assert(!cata_web::yield_if_due(a, 16));
    cata_web::wait_for_input(16); assert(!cata_web::yield_if_due(a, 16));
    clock_ms += 4; assert(cata_web::yield_if_due(a, 4));
    cata_web::pace_activity(false); waited_ms = 0;
    clock_ms = 1000; cata_web::pace_activity(true); assert(waited_ms == 0);
    clock_ms += 10; cata_web::pace_activity(true); assert(waited_ms == 41);
    clock_ms += 100; cata_web::pace_activity(true); assert(waited_ms == 41);
    cata_web::pace_activity(false); clock_ms += 1;
    cata_web::pace_activity(true); assert(waited_ms == 41); // no old pacing debt
    puts("PASS shared yield and pacing: bounded waits, slow-turn no-op, reset, reentry, post-resume clock");
}
'''
with tempfile.TemporaryDirectory(prefix='scheduler-', dir=out) as tmp:
    cpp, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
    cpp.write_text(program)
    subprocess.run(['g++', '-std=c++17', '-O2', str(cpp), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)

# Execute actual recursive grouping and stack constructors against item/position
# fixtures; all visibility and simulation code must remain upstream-equivalent.
old_game, new_game = upstream('game.cpp'), (source / 'src/game.cpp').read_text()
old_add = function(old_game, 'static void add_item_recursive(')
new_add = function(new_game, 'static void add_item_recursive(')
assert 'std::find(' not in new_add and 'try_emplace(' in new_add
old_find = function(old_game, 'std::vector<map_item_stack> game::find_nearby_items(')
new_find = function(new_game, 'std::vector<map_item_stack> game::find_nearby_items(')
assert old_find[:old_find.index('    ret.reserve(')] == new_find[:new_find.index('    ret.reserve(')]
for file, sig in [('item.cpp', 'std::string item::durability_indicator('),
                  ('item_tname.cpp', 'std::string food_status(')]:
    assert function(upstream(file), sig) == function((source / 'src' / file).read_text(), sig)
actor = (source / 'src/activity_actor.cpp').read_text()
for sig in ['void pulp_activity_actor::start(', 'void pulp_activity_actor::do_turn(',
            'bool pulp_activity_actor::can_pulp(', 'bool pulp_activity_actor::punch_corpse_once(',
            'void pulp_activity_actor::finish(']:
    assert tokens(function(upstream('activity_actor.cpp'), sig)) == tokens(function(actor, sig))
# Compile the actual percentage getter; no RNG, movement or actor state stubs
# are exposed to it, and the five simulation methods above remain upstream.
pulp_getter = function(actor, 'std::string pulp_activity_actor::get_progress_message(')
assert 'n_gettext' not in pulp_getter and 'remaining' not in pulp_getter
pulp_program = r'''
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <string>
#include <vector>
struct player_activity {};
struct item {
    int d = 0, maxd = 4000;
    int damage() const { return d; }
    int max_damage() const { return maxd; }
};
static int visits = 0;
struct item_location {
    const item *ptr;
    const item *get_item() const { ++visits; return ptr; }
};
struct pulp_activity_actor {
    std::vector<item_location> corpses;
    std::string get_progress_message(const player_activity &) const;
};
std::string string_format(const char *fmt, int pct) {
    char buffer[32]; std::snprintf(buffer, sizeof(buffer), fmt, pct); return buffer;
}
'''
pulp_program += pulp_getter + r'''
int main() {
    player_activity act; pulp_activity_actor actor;
    item current, done{4000,4000}, old{1000,4000};
    actor.corpses = {{&old}, {&current}, {nullptr}, {&done}};
    for (int damage = -1000; damage < 4000; ++damage) {
        current.d = damage;
        const auto expected = std::to_string(std::clamp(damage * 100 / 4000, 0, 100)) + "%";
        assert(actor.get_progress_message(act) == expected);
        assert(current.d == damage && old.d == 1000 && done.d == 4000);
    }
    current.d = 4000; assert(actor.get_progress_message(act) == "25%");
    old.d = 4000; assert(actor.get_progress_message(act).empty());
    actor.corpses = {{nullptr}}; assert(actor.get_progress_message(act).empty());
    item invalid{-1,0}; actor.corpses = {{&invalid}};
    assert(actor.get_progress_message(act).empty());
    current.d = 2000; actor.corpses.assign(1000, {&current}); visits = 0;
    assert(actor.get_progress_message(act) == "50%"); assert(visits == 1);
    std::puts("PASS pulping: 5000 damage boundaries, skipped targets, percent-only text, one lookup for 1000 live corpses");
}
'''
with tempfile.TemporaryDirectory(prefix='pulp-progress-', dir=out) as tmp:
    cpp, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
    cpp.write_text(pulp_program)
    subprocess.run(['g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', str(cpp), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
stack_h = (source / 'src/map_item_stack.h').read_text()
stack_h = stack_h[stack_h.index('class map_item_stack\n'):stack_h.index('\nstd::vector<map_item_stack> filter_item_stacks')]
stack_cpp = (source / 'src/map_item_stack.cpp').read_text()
stack_cpp = stack_cpp[stack_cpp.index('map_item_stack::item_group::item_group()'):stack_cpp.index('bool map_item_stack::compare_item_names')]
program = r'''
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <map>
#include <random>
#include <string>
#include <utility>
#include <vector>
using tripoint_rel_ms = int;
struct item {
    std::string name;
    int amount = 1;
    std::vector<const item *> contents;
    std::string tname() const { return name; }
    int count() const { return amount; }
    const std::vector<const item *> &all_known_contents() const { return contents; }
};
static size_t linear_comparisons = 0;
template<class It> It counted_find(It first, It last, const std::string &value) {
    while (first != last) { ++linear_comparisons; if (*first == value) break; ++first; }
    return first;
}
'''
program += stack_h + '\n' + stack_cpp
for ns, add, find in [('legacy', old_add, old_find), ('current', new_add, new_find)]:
    program += '\nnamespace ' + ns + ' {\n' + add.replace('std::find(', 'counted_find(') + '\n'
    program += r'''
std::vector<map_item_stack> collect(const std::vector<item> &items) {
    std::map<std::string, map_item_stack> temp_items;
    std::vector<map_item_stack> ret;
    std::vector<std::string> item_order;
    for (size_t i = 0; i < items.size(); ++i)
        add_item_recursive(item_order, temp_items, &items[i], int(i / 4));
'''
    if ns == 'current':
        program += '    std::vector<const void *> buffers;\n    for (const auto &name : item_order) buffers.push_back(temp_items.at(name).vIG.data());\n'
    finish = find[find.index('    ret.reserve('):]
    if ns == 'current':
        finish = finish.replace('    return ret;', '    for (size_t i = 0; i < ret.size(); ++i) assert(ret[i].vIG.data() == buffers[i]);\n    return ret;')
    program += finish + '\n}\n'
program += r'''
static void compare(const std::vector<item> &items) {
    auto old_items = legacy::collect(items), new_items = current::collect(items);
    assert(old_items.size() == new_items.size());
    for (size_t i = 0; i < old_items.size(); ++i) {
        const auto &a = old_items[i], &b = new_items[i];
        assert(a.example == b.example && a.totalcount == b.totalcount);
        assert(a.vIG.size() == b.vIG.size());
        for (size_t j = 0; j < a.vIG.size(); ++j) {
            assert(a.vIG[j].it == b.vIG[j].it && a.vIG[j].pos == b.vIG[j].pos);
            assert(a.vIG[j].count == b.vIG[j].count);
        }
    }
}
int main() {
    std::mt19937 rng(1234);
    const std::vector<std::string> names = {"", "water", "包帯", "死体(新鮮)", "ドロドロの死体(新鮮)", "死体(腐敗)"};
    for (int n = 0; n < 200; ++n) {
        std::vector<item> nested(12), items(n);
        for (size_t i = 0; i < nested.size(); ++i) {
            nested[i].name = names[rng() % names.size()];
            nested[i].amount = 1 + rng() % 50;
            if (i) nested[i].contents.push_back(&nested[i - 1]);
        }
        for (auto &it : items) {
            it.name = names[rng() % names.size()]; it.amount = 1 + rng() % 50;
            if (rng() % 3 == 0) it.contents.push_back(&nested[rng() % nested.size()]);
        }
        compare(items);
    }
    std::vector<item> corpses(2);
    corpses[0].name = corpses[1].name = "死体(新鮮)";
    assert(current::collect(corpses).size() == 1);
    corpses[0].name = "ドロドロの死体(新鮮)";
    compare(corpses); assert(current::collect(corpses).size() == 2);
    corpses[1].name = "ドロドロの死体(新鮮)";
    compare(corpses); assert(current::collect(corpses).size() == 1);
    std::vector<item> many(2000);
    for (size_t i = 0; i < many.size(); ++i) many[i].name = std::to_string(i);
    linear_comparisons = 0; compare(many); assert(linear_comparisons == 1999000);
    puts("PASS nearby items: 203 fixtures, same order/counts/positions/contents/pointers; result buffers moved not copied");
    puts("2000 distinct names: 1,999,000 linear comparisons removed; map lookups remain (NOT game speedup)");
}
'''
with tempfile.TemporaryDirectory(prefix='nearby-items-', dir=out) as tmp:
    cpp, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
    cpp.write_text(program)
    subprocess.run(['g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', str(cpp), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)

# The any-bodypart effect query must still test the OUTER map, even when an
# entry's inner map is empty. Exercise both real overloads before/after writes.
old_creature = upstream('creature.cpp')
new_creature = (source / 'src/creature.cpp').read_text()
any_effect = 'bool Creature::has_effect( const efftype_id &eff_id ) const'
part_effect = 'bool Creature::has_effect( const efftype_id &eff_id, const bodypart_id &bp ) const'
assert function(old_creature, part_effect) == function(new_creature, part_effect)
program = r'''
#include <cassert>
#include <cstdio>
#include <map>
#include <string>
using efftype_id = std::string;
static int conversions = 0;
struct bodypart_str_id {
    static const std::string &NULL_ID() { static const std::string s = "null"; return s; }
};
struct bodypart_id {
    std::string value;
    bodypart_id(const std::string &s) : value(s) { ++conversions; }
    const std::string &id() const { ++conversions; return value; }
};
using effect_map = std::map<std::string, std::map<std::string, int>>;
'''
for namespace, text in [('original', old_creature), ('current', new_creature)]:
    program += 'namespace ' + namespace + r''' {
struct Creature {
    effect_map *effects;
    bool has_effect(const efftype_id &) const;
    bool has_effect(const efftype_id &, const bodypart_id &) const;
};
'''
    program += function(text, part_effect) + '\n' + function(text, any_effect) + '\n}\n'
program += r'''
int main() {
    effect_map state;
    original::Creature old{&state}; current::Creature now{&state};
    const std::string ids[] = {"sleep", "deaf", "stunned", "missing"};
    const std::string parts[] = {"null", "head", "torso", "unknown"};
    unsigned checks = 0;
    for(int mask = 0; mask < 8; ++mask) {
        for(int shape = 0; shape < 8; ++shape) {
            state.clear();
            for(int e = 0; e < 3; ++e) if(mask & (1 << e)) {
                auto &inner = state[ids[e]];
                for(int p = 0; p < 3; ++p) if(shape & (1 << p)) inner[parts[p]] = p + 1;
            }
            for(const auto &id : ids) {
                assert(old.has_effect(id) == now.has_effect(id)); ++checks;
                for(const auto &part : parts) {
                    const bodypart_id bp(part);
                    assert(old.has_effect(id, bp) == now.has_effect(id, bp)); ++checks;
                }
                // No cached answers survive adding/removing an effect.
                state[id]["head"] = 10;
                assert(old.has_effect(id) == now.has_effect(id)); ++checks;
                state.erase(id);
                assert(old.has_effect(id) == now.has_effect(id)); ++checks;
            }
        }
    }
    state["sleep"]; // An empty inner map still means presence in upstream.
    assert(now.has_effect("sleep"));
    conversions = 0;
    for(int i = 0; i < 100000; ++i) assert(old.has_effect("sleep"));
    const int before = conversions;
    conversions = 0;
    for(int i = 0; i < 100000; ++i) assert(now.has_effect("sleep"));
    assert(before == 200000 && conversions == 0);
    std::printf("PASS effect presence: %u differential checks; 100000 queries: %d -> %d bodypart conversions (not game speedup)\n", checks, before, conversions);
}
'''
with tempfile.TemporaryDirectory(prefix='effect-presence-', dir=out) as tmp:
    cpp, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
    cpp.write_text(program)
    subprocess.run(['g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', str(cpp), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)

# Cooperative map boundaries must preserve upstream simulation and persistence.
for name, signatures in [
    ('map.cpp', ['void map::load(', 'void map::shift(']),
    ('mapbuffer.cpp', ['void mapbuffer::save(', 'void mapbuffer::save_quad(']),
    ('overmapbuffer.cpp', ['void overmapbuffer::save()'])
]:
    current = (source / 'src' / name).read_text()
    for signature in signatures:
        candidate = function(current, signature)
        assert tokens(candidate.replace('CATA_WEB_YIELD();', '')) == tokens(function(upstream(name), signature))
print('PASS map load/shift/save: all original statements and order retained; cooperative yields only')

# Compile actual UI dispatch against real bundled ImGui. Host SDL, geometry and
# the popup wrapper are fixtures, so this is NOT a full-game screenshot test.
popup_program = (out / 'progress-harness.cpp').read_text().split('int main() {')[0]
popup_program = '#include "imgui/imgui.h"\n#include "imgui/imgui_internal.h"\n#include <functional>\n#include "cata_scope_helpers.h"\n' + popup_program
popup_program = popup_program.replace('''namespace ui_manager {
void redraw() { ++redraws; if (!popup_depth) ++full_redraws; }
void redraw_invalidated() { ++redraws; assert(popup_depth == 0); }
}''', 'namespace ui_manager { void redraw(); void redraw_invalidated(); }')
ui_start = popup_program.index('struct ui_adaptor {')
ui_end = popup_program.index('struct game {', ui_start)
popup_program = popup_program[:ui_start] + r'''
#define cata_assert(condition) assert(condition)
struct point {};
void restore_cursor(point) {}
struct ui_adaptor;
using ui_stack_t = std::vector<std::reference_wrapper<ui_adaptor>>;
static ui_stack_t ui_stack;
static bool test_mode = false, imgui_frame_started = false;
static bool redraw_in_progress = false, restart_redrawing = false;
struct client {
    void new_frame() { ImGui::NewFrame(); }
    void end_frame() { ImGui::Render(); }
};
client client_instance; client *imclient = &client_instance;
struct ui_adaptor {
    struct disable_uis_below {};
    enum class cursor { none, last, custom };
    bool disabling_uis_below = false, invalidated = true, deferred_resize = false;
    bool is_imgui = false, is_on_top = false;
    cursor cursor_type = cursor::none;
    point cursor_pos;
    std::function<void(ui_adaptor&)> screen_resized_cb, redraw_cb;
    ui_adaptor() { ui_stack.emplace_back(*this); }
    explicit ui_adaptor(disable_uis_below) : ui_adaptor() { disabling_uis_below = true; }
    ~ui_adaptor() {
        auto it = std::find_if(ui_stack.begin(), ui_stack.end(), [&](auto &entry) { return &entry.get() == this; });
        ui_stack.erase(it);
    }
    void default_cursor() {}
    void record_term_cursor() {}
    static void redraw_invalidated();
};
struct static_popup {
    ui_adaptor ui;
    std::string message;
    static_popup() {
        ui.is_imgui = true;
        ui.redraw_cb = [&](ui_adaptor&) {
            ImGui::SetNextWindowPos({320, 0}, ImGuiCond_Always, {0.5f, 0});
            ImGui::Begin("QUERY_POPUP", nullptr, ImGuiWindowFlags_NoTitleBar |
                         ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_AlwaysAutoResize);
            ImGui::TextUnformatted(message.c_str());
            ImGui::End();
        };
    }
    static_popup &on_top(bool) { return *this; }
    void wait_message(const char *, const std::string &s) { message = s; ui.invalidated = true; }
};
''' + popup_program[ui_end:]
popup_program += function((source / 'src/ui_manager.cpp').read_text(), 'void ui_adaptor::redraw_invalidated(')
popup_program += r'''
namespace ui_manager {
void redraw() {
    if (!ui_stack.empty()) ui_stack.back().get().invalidated = true;
    ui_adaptor::redraw_invalidated();
}
void redraw_invalidated() { ui_adaptor::redraw_invalidated(); }
}
int main() {
    ImGui::CreateContext();
    auto &io = ImGui::GetIO(); io.IniFilename = nullptr; io.DisplaySize = {640, 480};
    io.DeltaTime = 1.0f / 60.0f;
    unsigned char *pixels; int w, h;
    io.Fonts->GetTexDataAsRGBA32(&pixels, &w, &h);
    {
        ui_adaptor main_ui;
        avatar u; u.activity.type = ACT_READ; game state; g = &state;
        // Negative control: the reused popup lies below the NEXT blocker.
        for (int turn : {1, 60}) {
            calendar::turn = turn; cata_web::clock_ms += 1000; old_wait(u);
        }
        assert(!ImGui::FindWindowByName("QUERY_POPUP")->Active);
        g->wait_popup_reset(); g->first_redraw_since_waiting_started = true;
        for (int turn : {1, 60, 120, 180, 300, 360}) {
            calendar::turn = turn; cata_web::clock_ms += 1000; new_wait(u);
            assert(ImGui::FindWindowByName("QUERY_POPUP")->Active);
            if (turn > 1) assert(ImGui::GetDrawData()->TotalVtxCount > 0);
        }
        ui_manager::redraw(); assert(ImGui::FindWindowByName("QUERY_POPUP")->Active);
        io.DisplaySize = {800, 600}; ui_manager::redraw_invalidated();
        assert(ImGui::FindWindowByName("QUERY_POPUP")->Active);
        u.activity.type = ACT_NULL; new_wait(u); ui_manager::redraw();
        assert(!ImGui::FindWindowByName("QUERY_POPUP")->Active);
        u.activity.type = ACT_CRAFT; new_wait(u); ui_manager::redraw_invalidated();
        assert(ImGui::FindWindowByName("QUERY_POPUP")->Active);
    }
    ImGui::DestroyContext();
    puts("PASS actual UI dispatcher + ImGui: old blocker hides popup; fixed reading/craft persists, resizes and resets");
}
'''
with tempfile.TemporaryDirectory(prefix='popup-imgui-', dir=out) as tmp:
    cpp, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
    cpp.write_text(popup_program)
    imgui = source / 'src/third-party/imgui'
    subprocess.run(['g++', '-std=c++17', '-O0', '-g0', '-DEMSCRIPTEN', '-I', str(source / 'src'),
                    '-I', str(source / 'src/third-party'), str(cpp),
                    *[str(imgui / name) for name in ['imgui.cpp', 'imgui_draw.cpp', 'imgui_tables.cpp', 'imgui_widgets.cpp']],
                    '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
