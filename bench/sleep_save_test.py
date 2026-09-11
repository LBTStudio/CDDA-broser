#!/usr/bin/env python3
"""Execute source-extracted sleep polling/save bookkeeping with controlled clocks.

Usage: python3 bench/sleep_save_test.py /path/to/patched/0.I
This measures work counts, not full-game performance. All generated files stay
under bench/out. HEAD of the supplied checkout must be unmodified upstream 0.I.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).resolve()
out = Path(__file__).resolve().parent / 'out'
out.mkdir(exist_ok=True)


def upstream(name):
    return subprocess.check_output(
        ['git', '-C', str(source), 'show', 'HEAD:src/' + name], text=True)


def function(text, signature):
    start = text.index(signature)
    pos = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[pos] == '{') - (text[pos] == '}')
        pos += 1
    return text[start:pos]


def tokens(text):
    return re.sub(r'\s+', '', re.sub(r'//[^\n]*|/\*.*?\*/', '', text, flags=re.S))


def build_run(program, defines=()):
    with tempfile.TemporaryDirectory(prefix='sleep-save-', dir=out) as tmp:
        cpp, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
        cpp.write_text(program)
        subprocess.run(['g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
                        *defines, '-I', str(source / 'src'), str(cpp), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], cwd=tmp, check=True)


effects = (source / 'src/player_hardcoded_effects.cpp').read_text()
sig = 'static void eff_fun_sleep('
old_sleep, new_sleep = function(upstream('player_hardcoded_effects.cpp'), sig), function(effects, sig)
start = new_sleep.index('    u.set_moves( 0 );')
end = new_sleep.index('    if( intense < 1 )')
# All sleep effects, random draws, messages, recovery and wake conditions are verbatim.
assert old_sleep[:old_sleep.index('    u.set_moves( 0 );')] == new_sleep[:start]
assert old_sleep[old_sleep.index('    if( intense < 1 )'):] == new_sleep[end:]
for name in ['character_body.cpp', 'messages.cpp']:
    assert upstream(name) == (source / 'src' / name).read_text(), name
# The rest of hardcoded effects is untouched (including heat illness and alarms).
assert tokens(effects.replace(new_sleep, '').replace('#include "cata_web_yield.h"', '')) == tokens(
    upstream('player_hardcoded_effects.cpp').replace(old_sleep, ''))

program = r'''
#include <cassert>
#include <cstdio>
namespace cata_web {
constexpr double default_budget_ms = 16.0;
double clock_ms = 0;
double now_ms() { return clock_ms; }
}
struct Character {
    bool avatar = true;
    int moves = 100;
    bool is_avatar() const { return avatar; }
    void set_moves(int n) { moves = n; }
};
struct Input {
    int calls = 0;
    double cost_ms = 0;
    void pump_events() { ++calls; cata_web::clock_ms += cost_ms; }
} inp_mngr;
void sleep_poll(Character &u) {
''' + new_sleep[start:end] + r'''
}
int main() {
    Character u;
    // Eight game-hours of sleep, deterministic 1ms work/turn, not a CPU benchmark.
    for (int turn = 0; turn < 28800; ++turn) {
        cata_web::clock_ms = turn;
        u.moves = 100;
        sleep_poll(u);
        assert(u.moves == 0);
    }
#if defined(EMSCRIPTEN)
    assert(inp_mngr.calls == 1800);
    puts("PASS sleep polling: 28800 -> 1800 calls at simulated 1ms/turn; all moves reset");
    int calls = inp_mngr.calls;
    // Boundary, post-resume clock and no catch-up burst after long browser delay.
    cata_web::clock_ms = 28799; sleep_poll(u); assert(inp_mngr.calls == calls);
    cata_web::clock_ms = 28800; inp_mngr.cost_ms = 100; sleep_poll(u);
    assert(inp_mngr.calls == ++calls);
    sleep_poll(u); assert(inp_mngr.calls == calls);
    inp_mngr.cost_ms = 0;
    cata_web::clock_ms = 28915; sleep_poll(u); assert(inp_mngr.calls == calls);
    cata_web::clock_ms = 28916; sleep_poll(u); assert(inp_mngr.calls == ++calls);
    cata_web::clock_ms += 600000; sleep_poll(u); assert(inp_mngr.calls == ++calls);
    sleep_poll(u); assert(inp_mngr.calls == calls);
    for (int t = 0; t < 100; ++t) {
        cata_web::clock_ms += 20;
        sleep_poll(u); assert(inp_mngr.calls == ++calls);
    }
#else
    assert(inp_mngr.calls == 28800);
    puts("PASS native sleep: upstream per-turn polling retained");
#endif
    int before = inp_mngr.calls;
    u.avatar = false;
    for (int i = 0; i < 100; ++i) { cata_web::clock_ms += 100; sleep_poll(u); }
    assert(inp_mngr.calls == before);
    puts("PASS slow turns, delayed resume, first call and NPC exclusion");
}
'''
for defines in [[], ['-DEMSCRIPTEN']]:
    build_run(program, defines)


game = (source / 'src/game.cpp').read_text()
save = function(game, 'bool game::save()')
old_save = function(upstream('game.cpp'), 'bool game::save()')
guard = save[save.index('#if defined(EMSCRIPTEN)'):save.index('    std::chrono::seconds')]
assert save.replace(guard, '') == old_save, 'serialization and error paths must not change'
quick = function(game, 'void game::quicksave()')
assert quick.count('std::time( nullptr )') == 1
quick = quick.replace('std::time( nullptr )', 'fake_now')
program = r'''
#include <cassert>
#include <cstdio>
#include <stdexcept>
#include "cata_scope_helpers.h"
long fake_now = 0;
struct Window {
    int depth = 0;
    void beginFsSyncBatch() { ++depth; }
    void endFsSyncBatch() { --depth; assert(depth >= 0); }
} window;
#define EM_ASM(...) __VA_ARGS__
bool scope_test(int mode) {
''' + guard + r'''
    assert(window.depth == 1);
    if (mode == 2) throw std::runtime_error("serialization failure");
    return mode == 0;
}
const int m_info = 1;
const char *_(const char *s) { return s; }
void add_msg(int, const char *) {}
struct static_popup { void message(const char *, const char *) {} };
namespace ui_manager { void redraw() {} }
void refresh_display() {}
struct game {
    int moves_since_last_save = 5, calls = 0;
    long last_save_timestamp = 10;
    bool success = false, fail_throw = false;
    bool save() {
        ++calls; fake_now += 400;
        if (fail_throw) throw std::runtime_error("save event");
        return success;
    }
    void quicksave();
};
''' + quick + r'''
int main() {
    assert(scope_test(0)); assert(window.depth == 0);
    assert(!scope_test(1)); assert(window.depth == 0);
    try { scope_test(2); assert(false); } catch (const std::runtime_error &) {}
    assert(window.depth == 0);
    game g;
    g.quicksave(); assert(g.calls == 1 && g.moves_since_last_save == 5 && g.last_save_timestamp == 10);
    g.success = true;
    g.quicksave(); assert(g.calls == 2 && g.moves_since_last_save == 0 && g.last_save_timestamp == 800);
    g.quicksave(); assert(g.calls == 2);
    g.moves_since_last_save = 3; g.fail_throw = true;
    try { g.quicksave(); assert(false); } catch (const std::runtime_error &) {}
    assert(g.moves_since_last_save == 3 && g.last_save_timestamp == 800);
    puts("PASS real save scope: normal/failed/exception exits; quicksave retry and completion timestamp");
}
'''
build_run(program, ['-DEMSCRIPTEN'])

fs_source = (source / 'src/filesystem.cpp').read_text()
assure = function(fs_source, 'bool assure_dir_exist( const std::filesystem::path &path )')
old_assure = function(upstream('filesystem.cpp'), 'bool assure_dir_exist( const std::filesystem::path &path )')
assert tokens(assure.replace('setFsNeedsSync();', '')) == tokens(old_assure.replace('setFsNeedsSync();', ''))
program = r'''
#include <cassert>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <string>
int dirty = 0;
void setFsNeedsSync() { ++dirty; }
std::string as_norm_dir(const std::filesystem::path &p) { return p.generic_string(); }
bool is_lexically_valid(const std::filesystem::path &) { return true; }
''' + assure + r'''
int main() {
    const std::filesystem::path dir("save/world/maps");
    assert(assure_dir_exist(dir)); assert(dirty == 1);
    for (int i = 0; i < 10000; ++i) assert(assure_dir_exist(dir));
    assert(dirty == 1);
    std::ofstream("file") << "not a directory";
    assert(!assure_dir_exist(std::filesystem::path("file"))); assert(dirty == 1);
    assert(!assure_dir_exist(std::filesystem::path("file/child"))); assert(dirty == 2);
    puts("PASS filesystem: 10000 existing-directory checks -> zero dirty notifications; creation/failure preserved");
}
'''
build_run(program)
print('PASS sleep/thermal/log semantics, save serialization and filesystem return semantics unchanged')
