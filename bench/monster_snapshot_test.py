#!/usr/bin/env python3
"""Differential test of extracted 0.I ranges and tracker membership mutations.

Usage: monster_snapshot_test.py PATCHED_SOURCE [native|sanitized|wasm]
HEAD of PATCHED_SOURCE must be pristine 0.I. Dependencies (monster, JSON, map)
are controlled fixtures; iterator/cache/mutation bodies and memory_fast.h are
real source. Timings are subsystem measurements, NOT game/Chromebook timings.
Wasm uses CDDA_EMXX and Node, no browser or full game build is needed.
"""
from pathlib import Path
import os
import shlex
import subprocess
import sys

source = Path(sys.argv[1]).resolve()
backend = sys.argv[2] if len(sys.argv) > 2 else 'native'
assert backend in ('native', 'sanitized', 'wasm')
out = Path(__file__).resolve().parent / 'out' / 'monster-snapshot'
out.mkdir(parents=True, exist_ok=True)


def original(name):
    return subprocess.check_output(
        ['git', '-C', str(source), 'show', 'HEAD:src/' + name], text=True)


def body(text, signature):
    start = text.index(signature)
    # A constructor initializer can contain a lambda body before its own body.
    opening = text.index('\n{', start) + 1
    depth, end = 1, opening + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


files = ('game.h', 'game.cpp', 'creature_tracker.h', 'creature_tracker.cpp', 'savegame.cpp')
old = {name: original(name) for name in files}
new = {name: (source / 'src' / name).read_text() for name in files}
# Fail closed if upstream introduces an unreviewed membership mutation.
mutations = []
for path in sorted((source / 'src').glob('*')):
    if path.suffix not in ('.h', '.cpp'):
        continue
    for line in path.read_text().splitlines():
        if 'monsters_list.' in line and any(token in line for token in
                ('emplace', 'push_', 'erase', 'clear', 'insert', 'assign', 'swap', 'resize')):
            mutations.append((path.name, line.strip()))
assert mutations == [
    ('creature_tracker.cpp', 'monsters_list.emplace_back( critter_ptr );'),
    ('creature_tracker.cpp', 'monsters_list.erase( iter );'),
    ('creature_tracker.cpp', 'monsters_list.clear();'),
    ('creature_tracker.cpp', 'iter = monsters_list.erase( iter );'),
    ('savegame.cpp', 'monsters_list.clear();'),
], mutations
# Every membership mutation retains upstream's complete function body except
# the reset. Save serialization and all three liveness predicates stay exact.
methods = ['shared_ptr_fast<monster> creature_tracker::find(',
           'bool creature_tracker::add(', 'void creature_tracker::remove_from_location_map(',
           'void creature_tracker::remove(', 'void creature_tracker::clear(',
           'void creature_tracker::remove_dead(']
for signature in methods:
    before = body(old['creature_tracker.cpp'], signature)
    after = body(new['creature_tracker.cpp'], signature)
    assert '\n'.join(line for line in after.split('\n')
                     if 'monsters_snapshot_.reset();' not in line) == before, signature
for signature in ('void creature_tracker::deserialize(', 'void creature_tracker::serialize('):
    before = body(old['savegame.cpp'], signature)
    after = body(new['savegame.cpp'], signature)
    assert '\n'.join(line for line in after.split('\n')
                     if 'monsters_snapshot_.reset();' not in line) == before, signature
for kind in ('monster', 'npc', 'Creature'):
    signature = 'bool game::non_dead_range<' + kind + '>::iterator::valid()'
    assert body(old['game.cpp'], signature) == body(new['game.cpp'], signature)
assert 'mutable shared_ptr_fast<const monster_snapshot> monsters_snapshot_;' in new['creature_tracker.h']
assert 'using monster_snapshot = std::vector<weak_ptr_fast<monster>>;' in new['creature_tracker.h']

program = r'''
#include <algorithm>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <memory>
#include <new>
#include <string>
#include <unordered_set>
#include <vector>
#include "memory_fast.h"
static bool count_allocations = false, fail_allocation = false;
static size_t allocations = 0, allocated_bytes = 0;
void *operator new(std::size_t size) {
    if (fail_allocation) throw std::bad_alloc();
    if (count_allocations) { ++allocations; allocated_bytes += size; }
    if (void *p = std::malloc(size ? size : 1)) return p;
    throw std::bad_alloc();
}
void operator delete(void *p) noexcept { std::free(p); }
void operator delete(void *p, std::size_t) noexcept { std::free(p); }
struct position {
    int n;
    bool operator<(position b) const { return n < b.n; }
    std::string to_string_writable() const { return std::to_string(n); }
};
using tripoint_abs_ms = position;
struct map {};
map terrain;
map &get_map() { return terrain; }
struct type_id { int n = 1; bool is_null() const { return n == 0; } };
struct mtype { type_id id; };
mtype normal, null_type{{0}}, blacklisted{{-1}};
namespace MonsterGroupManager {
    bool monster_is_blacklisted(type_id id) { return id.n == -1; }
}
template<class... Args> void debugmsg(Args...) {}
#define cata_assert assert
struct Creature {
    virtual ~Creature() = default;
    virtual bool is_monster() const { return false; }
    virtual bool is_npc() const { return false; }
};
struct Character : Creature {};
struct npc : Character {
    bool dead = false;
    bool is_npc() const override { return true; }
    bool is_dead() const { return dead; }
};
struct monster : Creature {
    int number = 0, location = 0;
    bool dead = false, hallucination = false;
    mtype *type = &normal;
    static int destroyed;
    ~monster() override { ++destroyed; }
    bool is_monster() const override { return true; }
    bool is_dead() const { return dead; }
    bool is_hallucination() const { return hallucination; }
    position pos_abs() const { return {location}; }
    void die(map *, void *) { dead = true; }
    std::string name() const { return std::to_string(number); }
};
int monster::destroyed = 0;
struct JsonValue {
    int number;
    void read(monster &m) { if (number < 0) throw 7; m.number = m.location = number; }
};
using JsonArray = std::vector<JsonValue>;
struct JsonOut {
    std::vector<int> numbers;
    void start_array() { numbers.clear(); }
    void write(const monster &m) { numbers.push_back(m.number); }
    void end_array() {}
};
'''
for namespace, text in (('upstream', old), ('optimized', new)):
    program += '\nnamespace ' + namespace + ' {\n'
    program += r'''
class creature_tracker {
public:
    std::vector<shared_ptr_fast<monster>> monsters_list;
    std::map<position, shared_ptr_fast<monster>> monsters_by_location;
    std::unordered_set<shared_ptr_fast<monster>> removed_this_turn_;
    std::vector<int> creatures_by_zone_and_faction_;
    std::vector<shared_ptr_fast<npc>> active_npc;
    const auto &get_monsters_list() const { return monsters_list; }
    void invalidate_reachability_cache() {}
    shared_ptr_fast<monster> find(const tripoint_abs_ms &) const;
    bool add(const shared_ptr_fast<monster> &);
    void remove_from_location_map(const monster &);
    void remove(const monster &);
    void clear();
    void remove_dead();
    void deserialize(const JsonArray &);
    void serialize(JsonOut &) const;
'''
    if namespace == 'optimized':
        program += '''
    using monster_snapshot = std::vector<weak_ptr_fast<monster>>;
    mutable shared_ptr_fast<const monster_snapshot> monsters_snapshot_;
    shared_ptr_fast<const monster_snapshot> get_monsters_snapshot() const;
'''
    program += '};\n'
    for signature in methods:
        program += body(text['creature_tracker.cpp'], signature) + '\n'
    for signature in ('void creature_tracker::deserialize(', 'void creature_tracker::serialize('):
        program += body(text['savegame.cpp'], signature) + '\n'
    if namespace == 'optimized':
        program += body(text['creature_tracker.cpp'],
                        'shared_ptr_fast<const creature_tracker::monster_snapshot>') + '\n'
    program += 'class game { public: class creature_tracker *critter_tracker; Character u;\n'
    header = text['game.h']
    start = header.index('        template<typename T>\n        class non_dead_range')
    end = header.index('\n    public:\n', start)
    program += header[start:end] + '\n};\n'
    for kind in ('monster', 'npc', 'Creature'):
        program += 'template<>\n' + body(text['game.cpp'],
                     'bool game::non_dead_range<' + kind + '>::iterator::valid()') + '\n'
    for kind in ('monster_range', 'npc_range', 'Creature_range'):
        program += body(text['game.cpp'], 'game::' + kind + '::' + kind + '(') + '\n'
    program += '}\n'
program += r'''
auto spawn(int n) {
    auto p = make_shared_fast<monster>(); p->number = p->location = n; return p;
}
template<class Range> std::vector<int> ids(Range &range) {
    std::vector<int> result;
    for (auto &m : range) result.push_back(m.number);
    return result;
}
template<class Game, class Tracker> std::vector<int> scenario(int mode) {
    Tracker tracker; Game game; game.critter_tracker = &tracker;
    auto a = spawn(1), b = spawn(2), c = spawn(3), d = spawn(4);
    assert(tracker.add(a) && tracker.add(b) && tracker.add(c));
    std::vector<int> trace;
    {
        typename Game::monster_range outer(game);
        if (mode == 8) { b->dead = true; }
        for (monster &m : outer) {
            trace.push_back(m.number);
            if (m.number != 1) continue;
            switch(mode) {
                case 0: assert(tracker.add(d)); break;
                case 1: b->dead = true; break;
                case 2: tracker.remove(*b); break;
                case 3: tracker.remove(*b); b.reset(); tracker.remove_dead(); break;
                case 4: tracker.clear(); b.reset(); c.reset(); break;
                case 5: tracker.deserialize({{7}, {8}}); b.reset(); c.reset(); break;
                case 6: tracker.deserialize({}); b.reset(); c.reset(); break;
                case 7: tracker.remove(*b); assert(tracker.add(b)); break;
                case 8: b->dead = false; break;
                case 9: b->dead = true; tracker.remove_dead(); break;
                case 10: b->location = 20; break;
                case 11: b->hallucination = true; { auto replacement = spawn(2);
                         assert(tracker.add(replacement)); } break;
                case 12: try { tracker.deserialize({{9}, {-1}}); assert(false); }
                         catch(int) {} b.reset(); c.reset(); break;
            }
            typename Game::monster_range inner(game);
            trace.push_back(-10);
            for (monster &n : inner) trace.push_back(n.number);
            trace.push_back(-20);
        }
    }
    typename Game::monster_range final(game);
    auto copied = final;
    auto moved = std::move(copied);
    auto after = ids(final);
    assert(ids(moved) == after);
    trace.push_back(-30); trace.insert(trace.end(), after.begin(), after.end());
    JsonOut saved; tracker.serialize(saved);
    trace.push_back(-40); trace.insert(trace.end(), saved.numbers.begin(), saved.numbers.end());
    return trace;
}
template<class Game, class Tracker> void lifetime() {
    Tracker tracker; Game game; game.critter_tracker = &tracker;
    auto a = spawn(1), b = spawn(2);
    assert(tracker.add(a) && tracker.add(b));
    weak_ptr_fast<monster> wa = a, wb = b;
    typename Game::monster_range range(game);
    a.reset(); b.reset();
    {
        auto iter = range.begin();
        tracker.clear();
        assert(!wa.expired() && wb.expired()); // only current iterator owns a monster
        ++iter; assert(iter == range.end() && wa.expired());
    }
    assert(ids(range).empty());
    typename Game::npc_range npcs(game);
    assert(npcs.begin() == npcs.end());
    auto n = make_shared_fast<npc>(); tracker.active_npc.push_back(n);
    typename Game::npc_range live_npcs(game);
    assert(&*live_npcs.begin() == n.get()); n->dead = true;
    assert(live_npcs.begin() == live_npcs.end());
    typename Game::Creature_range creatures(game);
    assert(&*creatures.begin() == &game.u);
}
void cache_contracts() {
    optimized::creature_tracker tracker;
    auto empty = tracker.get_monsters_snapshot();
    assert(empty && empty->empty() && empty == tracker.get_monsters_snapshot());
    auto a = spawn(1); assert(tracker.add(a));
    auto first = tracker.get_monsters_snapshot();
    assert(first != empty && empty->empty() && first->size() == 1);
    // No invalidation for failed adds/removes or dead cleanup without deaths.
    auto occupied = spawn(1); assert(!tracker.add(occupied));
    auto null = spawn(2); null->type = &null_type; assert(!tracker.add(null));
    auto banned = spawn(3); banned->type = &blacklisted; assert(!tracker.add(banned));
    tracker.remove(*null); tracker.remove_dead();
    assert(first == tracker.get_monsters_snapshot());
    a->dead = true; assert(first == tracker.get_monsters_snapshot());
    a->dead = false; a->location = 8;
    assert(first == tracker.get_monsters_snapshot());
    // Movement/state is NOT cached; weak targets reflect it immediately.
    assert(first->front().lock()->location == 8);
    a->dead = true; tracker.remove_dead();
    auto removed = tracker.get_monsters_snapshot();
    assert(removed != first && removed->empty() && first->size() == 1);
    tracker.clear();
    fail_allocation = true;
    try { tracker.get_monsters_snapshot(); assert(false); }
    catch(const std::bad_alloc &) {}
    fail_allocation = false;
    assert(!tracker.monsters_snapshot_);
    auto retry = tracker.get_monsters_snapshot(); assert(retry->empty());
    weak_ptr_fast<const optimized::creature_tracker::monster_snapshot> old_generation = retry;
    retry.reset(); assert(!old_generation.expired());
    tracker.clear(); assert(old_generation.expired());
    // Rejected mutations cannot keep obsolete worlds/monster objects alive.
    a.reset(); assert(first->front().expired());
}
volatile unsigned long long sink = 0;
template<class Game, class Tracker> void measure(const char *label, int n, bool traverse) {
    Tracker tracker; Game game; game.critter_tracker = &tracker;
    for (int i = 1; i <= n; ++i) assert(tracker.add(spawn(i)));
    constexpr int acquisitions = 2048;
    allocations = allocated_bytes = 0;
    count_allocations = true;
    const auto start = std::chrono::steady_clock::now();
    unsigned long long checksum = 0;
    for (int i = 0; i < acquisitions; ++i) {
        typename Game::monster_range range(game);
        if (traverse) {
            for (monster &m : range) checksum += m.number;
        } else {
            auto it = range.begin();
            if (it != range.end()) checksum += (*it).number;
        }
    }
    const auto finish = std::chrono::steady_clock::now();
    count_allocations = false;
    sink = checksum;
    const auto expected = static_cast<unsigned long long>(acquisitions) *
                          (traverse ? n * (n + 1ULL) / 2 : (n ? 1 : 0));
    assert(checksum == expected);
    if (std::string(label) == "optimized") assert(allocations <= 2);
    else assert(allocations == (n ? acquisitions : 0));
    std::printf("MEASURE %s N=%d mode=%s acquisitions=%d allocs=%zu bytes=%zu us=%.1f checksum=%llu\n",
                label, n, traverse ? "traversal" : "acquisition", acquisitions,
                allocations, allocated_bytes,
                std::chrono::duration<double, std::micro>(finish-start).count(), checksum);
}
int main() {
    for (int i = 0; i <= 12; ++i) {
        auto before = scenario<upstream::game, upstream::creature_tracker>(i);
        auto after = scenario<optimized::game, optimized::creature_tracker>(i);
        assert(before == after);
    }
    lifetime<upstream::game, upstream::creature_tracker>();
    lifetime<optimized::game, optimized::creature_tracker>();
    cache_contracts();
    std::puts("PASS 13 mutation traces; ordering, nested ranges, deaths/revival, expiry, copying, serialization, load failure, allocation failure, cache release, NPC/avatar paths");
    for (int n : {0, 32, 512, 2048}) for (bool traverse : {false, true}) {
        measure<upstream::game, upstream::creature_tracker>("upstream", n, traverse);
        measure<optimized::game, optimized::creature_tracker>("optimized", n, traverse);
    }
    std::puts("PASS real range allocations/checksums; NOT a full-game performance benchmark");
}
'''
cpp = out / 'test.cpp'
cpp.write_text(program)
common = ['-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
          '-I' + str(source / 'src'), str(cpp)]
if backend == 'wasm':
    binary = out / 'test.js'
    command = [os.environ['CDDA_EMXX'], *common, '-fwasm-exceptions',
               '-sENVIRONMENT=node', '-sINITIAL_MEMORY=33554432', '-o', str(binary)]
    execute = ['node', str(binary)]
else:
    binary = out / ('test-' + backend)
    extra = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if backend == 'sanitized' else []
    command = [os.environ.get('CXX', 'g++'), *common, *extra, '-o', str(binary)]
    execute = [str(binary)]
print('COMPILE ' + shlex.join(command), flush=True)
subprocess.run(command, check=True, timeout=120)
subprocess.run(execute, check=True, timeout=60)
print('PASS source preservation and membership-mutation inventory (' + backend + ')')
