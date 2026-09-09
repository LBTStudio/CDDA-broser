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
eligibility = function(new_activity, 'bool player_activity::has_progress_message() const')
old_checks = old_progress[old_progress.index('{') + 1:old_progress.index('    std::string extra_info;')]
old_checks = old_checks.replace('return std::optional<std::string>();', 'return false;').replace('return std::nullopt;', 'return false;')
assert tokens(eligibility[eligibility.index('{') + 1:]) == tokens(old_checks + 'return true;\n}')


def wait_block(text):
    start = text.index('    const bool player_is_sleeping = u.has_effect( effect_sleep );')
    return text[start:text.index('    m.invalidate_visibility_cache();', start)]


old_wait, new_wait = wait_block(old_turn), wait_block(new_turn)
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
constexpr int operator""_minutes(unsigned long long n) { return n * 60; }
constexpr int operator""_turns(unsigned long long n) { return n; }
namespace cata_web { double clock_ms = 0; double now_ms() { return clock_ms; } }
namespace calendar {
int turn = 0;
bool once_every(int n) { return turn % n == 0; }
}
static int progress_calls, key_calls, yields, redraws;
#define CATA_WEB_YIELD() (++yields)
const int effect_sleep = 1, ACTION_PAUSE = 2;
const char *_(const char *s) { return s; }
std::string press_x(int) { ++key_calls; return "pause"; }
std::string string_format(const char *, const std::string &s) { return "\n" + s + " to interrupt"; }
namespace ui_manager { void redraw() { ++redraws; } }
void refresh_display() {}
struct ui_adaptor {
    struct disable_uis_below {};
    explicit ui_adaptor(disable_uis_below) {}
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
        return verb + ": " + std::to_string(calendar::turn);
    }
};
struct avatar {
    player_activity activity;
    bool sleeping = false;
    bool has_effect(int) const { return sleeping; }
};
'''
program += eligibility + '\n'
program += 'void old_wait(avatar &u) {\n' + old_wait + '}\n'
program += 'void new_wait(avatar &u) {\n' + new_wait + '}\n'
program += r'''
int main() {
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
                g = &old_game; progress_calls = key_calls = redraws = yields = 0;
                old_wait(u);
                old_calls += progress_calls; old_keys += key_calls;
                const int expected_redraws = redraws;
                g = &new_game; progress_calls = key_calls = redraws = yields = 0;
                new_wait(u);
                new_calls += progress_calls; new_keys += key_calls;
                assert(yields >= 1); // includes sleep and non-display turns
                assert(redraws == expected_redraws);
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
    printf("PASS: %d scenarios, upstream turn-based cadence/text/reset; AI order preserved\n", scenarios);
#if defined(EMSCRIPTEN)
    avatar u; u.activity.type = ACT_PULP;
    game slow_game; g = &slow_game;
    calendar::turn = 1; cata_web::clock_ms = 1000;
    new_wait(u);
    assert(g->wait_popup->message.find("working: 1") == 0);
    calendar::turn = 2; cata_web::clock_ms = 1099;
    progress_calls = 0; new_wait(u); assert(progress_calls == 0);
    calendar::turn = 3; cata_web::clock_ms = 1100;
    new_wait(u); assert(progress_calls == 1);
    assert(g->wait_popup->message.find("working: 3") == 0);
    // Slow turns refresh actual state without waiting for turn 60.
    for (int turn = 4; turn < 60; ++turn) {
        calendar::turn = turn; cata_web::clock_ms += 100;
        progress_calls = 0; new_wait(u); assert(progress_calls == 1);
        assert(g->wait_popup->message.find("working: " + std::to_string(turn)) == 0);
    }
    u.activity.type = ACT_NULL; new_wait(u); assert(!g->wait_popup);
    u.activity.type = ACT_READ; new_wait(u); assert(g->wait_popup);
    u.sleeping = true; cata_web::clock_ms += 100;
    progress_calls = 0; new_wait(u); assert(progress_calls == 0);
    assert(g->wait_popup->message == "Wait till you wake up…");
    puts("PASS real-time popup: 99ms no refresh, 100ms actual progress refresh, reset/restart/sleep");
#endif
}
'''.replace('TYPE_COUNT', str(len(ids)))
with tempfile.TemporaryDirectory(prefix='runtime-', dir=out) as tmp:
    cpp, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
    cpp.write_text(program)
    for defines in [[], ['-DEMSCRIPTEN']]:
        subprocess.run(['g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', *defines, str(cpp), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)

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
void cata_web_wait_input_impl(int) { ++calls; }
namespace cata_web {
'''
for sig in ['void yield_now()', 'void wait_for_input(', 'void yield_paint()', 'bool yield_if_due(']:
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
    puts("PASS shared yield: cross-site budget, no starvation, post-resume clock, paint/idle, real reentry");
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
