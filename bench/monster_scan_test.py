#!/usr/bin/env python3
"""Compare actual 0.I functions with patched functions in one deterministic run.

Usage: python3 bench/monster_scan_test.py /path/to/patched/0.I
The source HEAD must be pristine 0.I. Fixtures model dependencies and count
snapshot entries/visits; these are NOT game timings or a 4GB RAM benchmark.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).resolve()
out = Path(__file__).resolve().parent / 'out'
out.mkdir(exist_ok=True)


def function(text, signature):
    start = text.index(signature)
    opening = text.index('{', start)
    depth, end = 1, opening + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


bodies = []
for filename, signature, marker in [
    ('monmove.cpp', 'void monster::anger_cub_threatened(',
     '    // With neither child definition,'),
    ('character.cpp', 'Character::moncam_cache_t Character::get_active_moncams()',
     '    // No camera definitions means no matches.')
]:
    before = subprocess.check_output(
        ['git', '-C', str(source), 'show', 'HEAD:src/' + filename], text=True)
    after = (source / 'src' / filename).read_text()
    start = after.index(marker)
    end = after.index('    }\n', start) + len('    }\n')
    if filename == 'monmove.cpp':
        end += 1  # blank line after the guard
    # Entire translation units, including AI order/RNG and camera setters,
    # must be unchanged except for the single early-exit block in each.
    assert after[:start] + after[end:] == before, filename
    old = function(before, signature)
    new = function(after, signature)
    method = 'anger_cub_threatened' if filename == 'monmove.cpp' else 'get_active_moncams'
    bodies += [old.replace('::' + method, '::old_' + method, 1), new]

program = r'''
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <map>
#include <set>
#include <string>
#include <utility>
#include <vector>
struct id {
    int value = 0;
    bool is_null() const { return value == 0; }
    bool operator==(const id &b) const { return value == b.value; }
    bool operator<(const id &b) const { return value < b.value; }
};
using mtype_id = id;
struct mtype { ::id id; struct { ::id baby_monster, baby_monster_group; } baby_type; };
struct monster_plan;
static int snapshots, copied_entries, visits, messages;
static std::vector<int> ratings, groups;
namespace debugmode { constexpr int DF_MONSTER = 0; }
template<class... T> void add_msg_debug(T...) { ++messages; }
struct monster {
    mtype *type = nullptr;
    int index = 0, position = 0, friendly = 0, anger = 0, morale = 0;
    float rating = 0;
    bool aggro_character = false, dead = false;
    int pos_abs() const { return position; }
    std::string name() const { return std::to_string(index); }
    float rate_target(const monster &, float dist, bool smart) const {
        ratings.push_back(index);
        return smart ? std::min(dist, rating) : rating;
    }
    void old_anger_cub_threatened(monster_plan &);
    void anger_cub_threatened(monster_plan &);
};
struct monster_plan {
    int angers_cub_threatened = 0;
    float dist = 12;
    bool smart_planning = false;
    monster *target = nullptr;
};
namespace MonsterGroupManager {
    bool IsMonsterInGroup(id group, id kind) {
        groups.push_back(kind.value);
        return group.value == kind.value;
    }
}
// Owning-snapshot creation and dead filtering are modeled here; game AI is
// not executed. The two methods under test are extracted verbatim above.
struct range {
    std::vector<monster *> items;
    struct iterator {
        std::vector<monster *>::const_iterator p;
        monster &operator*() const { ++visits; return **p; }
        iterator &operator++() { ++p; return *this; }
        bool operator!=(const iterator &b) const { return p != b.p; }
    };
    iterator begin() const { return {items.begin()}; }
    iterator end() const { return {items.end()}; }
};
struct game {
    std::vector<monster> monsters;
    range all_monsters() {
        ++snapshots; copied_entries += monsters.size();
        range ret;
        for (auto &m : monsters) if (!m.dead) ret.items.push_back(&m);
        return ret;
    }
};
game world;
game *g = &world;
struct Character {
    using moncam_cache_t = std::set<std::pair<const monster *, int>>;
    std::map<mtype_id, int> cameras;
    int position = 0;
    const auto &get_moncams() const { return cameras; }
    int pos_abs() const { return position; }
    moncam_cache_t old_get_active_moncams() const;
    moncam_cache_t get_active_moncams() const;
};
Character avatar;
Character &get_avatar() { return avatar; }
int rl_dist(int a, int b) { return std::abs(a - b); }
void reset() {
    snapshots = copied_entries = visits = messages = 0;
    ratings.clear(); groups.clear();
}
'''
program += '\n'.join(bodies)
program += r'''
int main() {
    mtype types[3];
    for (int i = 0; i < 3; ++i) types[i].id.value = i + 1;
    for (int i = 0; i < 12; ++i) {
        monster m; m.type = &types[i % 3]; m.index = i; m.position = i - 6;
        m.rating = i % 6; m.dead = i == 11; m.friendly = (i % 3) - 1;
        world.monsters.push_back(m);
    }
    monster target;
    int comparisons = 0;
    for (int child : {0, 1, 2}) for (int group : {0, 1, 3}) {
        mtype parent_type;
        parent_type.baby_type = {{child}, {group}};
        for (int trigger : {-1, 0, 8}) for (bool smart : {false, true}) {
            monster old, now; old.type = now.type = &parent_type;
            monster_plan a{trigger, 12, smart, &target}, b = a;
            reset(); old.old_anger_cub_threatened(a);
            const auto expected_ratings = ratings, expected_groups = groups;
            int expected_messages = messages;
            reset(); now.anger_cub_threatened(b);
            assert(old.anger == now.anger && old.morale == now.morale);
            assert(old.aggro_character == now.aggro_character && a.dist == b.dist);
            assert(a.target == b.target && a.smart_planning == b.smart_planning);
            assert(ratings == expected_ratings && groups == expected_groups);
            assert(messages == expected_messages);
            if ((!child && !group) || trigger < 0) assert(snapshots == 0);
            else assert(snapshots == 1);
            // Zero anger is NOT a no-op when children match.
            if (trigger == 0 && child == 1 && !group) assert(now.aggro_character);
            ++comparisons;
        }
    }
    // Compare camera results across empty, enabled, changed and removed states.
    for (int position : {-8, 0, 7}) for (int radius : {0, 1, 3, 10}) {
        avatar.position = position;
        for (int state = 0; state < 4; ++state) {
            avatar.cameras.clear();
            if (state == 1) avatar.cameras[types[0].id] = radius;
            if (state == 2) { avatar.cameras[types[0].id] = radius; avatar.cameras[types[1].id] = 3; }
            reset(); auto expected = avatar.old_get_active_moncams();
            reset(); assert(avatar.get_active_moncams() == expected);
            assert(snapshots == (avatar.cameras.empty() ? 0 : 1));
            ++comparisons;
        }
    }
    // Strict range boundary; positive AND negative friendliness remain valid.
    world.monsters = {monster{}};
    auto &camera = world.monsters.front();
    camera.type = &types[0]; camera.friendly = -1; camera.position = 3;
    avatar.position = 0; avatar.cameras = {{types[0].id, 3}};
    assert(avatar.get_active_moncams().empty());
    camera.position = 2; assert(avatar.get_active_moncams().size() == 1);
    camera.dead = true; assert(avatar.get_active_moncams().empty());
    world.monsters.clear(); assert(avatar.get_active_moncams().empty());
    // Deterministic scaling example: N parents without child definitions,
    // each evaluated once. This is operation counting, NOT a frame benchmark.
    world.monsters.resize(512);
    for (auto &m : world.monsters) m.type = &types[0];
    reset();
    for (auto &m : world.monsters) {
        monster_plan plan{0, 12, false, &target}; m.old_anger_cub_threatened(plan);
    }
    assert(copied_entries == 512 * 512 && visits == 512 * 512);
    int old_entries = copied_entries, old_visits = visits;
    reset();
    for (auto &m : world.monsters) {
        monster_plan plan{0, 12, false, &target}; m.anger_cub_threatened(plan);
    }
    assert(snapshots == 0 && copied_entries == 0 && visits == 0);
    std::printf("PASS %d state comparisons; child/group precedence, zero anger, order, camera changes/bounds/dead filtering\n", comparisons);
    std::printf("PASS 512 no-child parents: snapshot entries %d -> 0; visits %d -> 0 (not game timings)\n", old_entries, old_visits);
}
'''
with tempfile.TemporaryDirectory(prefix='monster-scan-', dir=out) as directory:
    cpp, binary = Path(directory) / 'test.cpp', Path(directory) / 'test'
    cpp.write_text(program)
    subprocess.run(['g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
                    str(cpp), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('PASS both complete source files unchanged apart from the no-op guards')
