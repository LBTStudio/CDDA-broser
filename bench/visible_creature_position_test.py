#!/usr/bin/env python3
"""Differential test of the real get_visible_creatures predicate.

The source checkout is pristine upstream CDDA 0.I at HEAD with wrapper patches
applied. The real function bodies are compiled against counting fixtures.
This measures coordinate conversions and a controlled microbenchmark, not a
full-game or physical-device speedup.
"""
from pathlib import Path
import subprocess
import tempfile

source = Path(__file__).resolve().parent.parent / "cdda"
source = Path(__import__("sys").argv[1]).resolve()
bench_dir = Path(__file__).resolve().parent
out = bench_dir / "out" / "visible-creature-position-cache"
out.mkdir(parents=True, exist_ok=True)


def upstream(name):
    return subprocess.check_output(
        ["git", "-C", str(source), "show", "HEAD:src/" + name], text=True)


def function(text, signature):
    start = text.index(signature)
    opening = text.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


old_source = upstream("character.cpp")
new_source = (source / "src" / "character.cpp").read_text()
signature = "std::vector<Creature *> Character::get_visible_creatures("
old_body = function(old_source, signature)
new_body = function(new_source, signature)
assert "observer_pos_initialized" not in old_body
assert "observer_pos_initialized" in new_body
assert "sees( here, critter )" in old_body and "sees( here, critter )" in new_body
assert "rl_dist( pos_abs(), critter.pos_abs() )" in old_body
assert "rl_dist( observer_pos, critter_pos )" in new_body

common = r'''
#include <algorithm>
#include <cassert>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <functional>
#include <memory>
#include <vector>

struct Spec {
    int id, x, y, z;
    bool seen, dead;
};
struct Outcome {
    std::vector<int> ids;
    int observer_positions = 0, target_positions = 0;
    int distance_calls = 0, los_calls = 0;
};
'''

program = common
for namespace, body in (("baseline", old_body), ("optimized", new_body)):
    program += f"\nnamespace {namespace} {{\n"
    program += r'''
struct tripoint_abs_ms {
    int x = 0, y = 0, z = 0;
    bool operator!=(const tripoint_abs_ms &other) const {
        return x != other.x || y != other.y || z != other.z;
    }
};
struct map {};
static map world;
map &get_map() { return world; }
static int distance_calls = 0;
int rl_dist(const tripoint_abs_ms &a, const tripoint_abs_ms &b) {
    ++distance_calls;
    return std::max({std::abs(a.x-b.x), std::abs(a.y-b.y), std::abs(a.z-b.z)});
}
struct Creature {
    int id = 0;
    tripoint_abs_ms position;
    bool seen = true, dead = false;
    mutable int position_calls = 0;
    virtual ~Creature() = default;
    virtual tripoint_abs_ms pos_abs() const {
        ++position_calls;
        return position;
    }
};
struct Character : Creature {
    mutable int los_calls = 0;
    bool sees(const map &, const Creature &critter) const {
        ++los_calls;
        return critter.seen;
    }
    std::vector<Creature *> get_visible_creatures(const int range) const;
};
struct game {
    std::vector<Creature *> creatures;
    std::vector<Creature *> get_creatures_if(
        const std::function<bool(const Creature &)> &predicate) {
        std::vector<Creature *> result;
        for (Creature *creature : creatures) {
            if (!creature->dead && predicate(*creature)) result.push_back(creature);
        }
        return result;
    }
};
static game *g = nullptr;
'''
    program += body + "\n"
    program += r'''
Outcome run_case(const std::vector<Spec> &specs, int range) {
    Character observer;
    observer.id = -1;
    observer.position = {0, 0, 0};
    game current;
    current.creatures.push_back(&observer);
    std::vector<std::unique_ptr<Creature>> targets;
    targets.reserve(specs.size());
    for (const Spec &spec : specs) {
        auto target = std::make_unique<Creature>();
        target->id = spec.id;
        target->position = {spec.x, spec.y, spec.z};
        target->seen = spec.seen;
        target->dead = spec.dead;
        current.creatures.push_back(target.get());
        targets.push_back(std::move(target));
    }
    g = &current;
    distance_calls = 0;
    const auto visible = observer.get_visible_creatures(range);
    Outcome result;
    for (const Creature *creature : visible) result.ids.push_back(creature->id);
    result.observer_positions = observer.position_calls;
    for (const auto &target : targets) result.target_positions += target->position_calls;
    result.distance_calls = distance_calls;
    result.los_calls = observer.los_calls;
    return result;
}
struct Benchmark {
    unsigned long long checksum = 0;
    int observer_positions = 0, target_positions = 0;
    double ms = 0;
};
Benchmark benchmark(int count, int repeats) {
    Character observer;
    observer.id = -1;
    observer.position = {0, 0, 0};
    game current;
    current.creatures.push_back(&observer);
    std::vector<std::unique_ptr<Creature>> targets;
    targets.reserve(count);
    for (int i = 0; i < count; ++i) {
        auto target = std::make_unique<Creature>();
        target->id = i + 1;
        target->position = {(i % 101) - 50, ((i * 7) % 97) - 48, (i % 5) - 2};
        target->seen = (i % 3) != 0;
        target->dead = (i % 11) == 0;
        current.creatures.push_back(target.get());
        targets.push_back(std::move(target));
    }
    g = &current;
    distance_calls = 0;
    const auto start = std::chrono::steady_clock::now();
    Benchmark result;
    for (int repeat = 0; repeat < repeats; ++repeat) {
        const auto visible = observer.get_visible_creatures(40);
        result.checksum = result.checksum * 33 + visible.size();
        for (const Creature *creature : visible) result.checksum += creature->id;
    }
    result.ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count();
    result.observer_positions = observer.position_calls;
    for (const auto &target : targets) result.target_positions += target->position_calls;
    return result;
}
} // namespace
'''
program += r'''
int main() {
    for (int count : {0, 1, 2, 16, 128, 512}) {
        for (int range : {0, 1, 3, 20, 100}) {
            std::vector<Spec> specs;
            specs.reserve(count);
            for (int i = 0; i < count; ++i) {
                specs.push_back({i + 1, (i % 31) - 15, ((i * 7) % 29) - 14,
                                 (i % 5) - 2, (i % 3) != 0, (i % 11) == 0});
            }
            const Outcome before = baseline::run_case(specs, range);
            const Outcome after = optimized::run_case(specs, range);
            assert(before.ids == after.ids);
            assert(before.distance_calls == after.distance_calls);
            assert(before.los_calls == after.los_calls);
        }
    }
    const auto before = baseline::benchmark(512, 400);
    const auto after = optimized::benchmark(512, 400);
    assert(before.checksum == after.checksum);
    assert(before.observer_positions > after.observer_positions);
    assert(before.target_positions > after.target_positions);
    std::printf("MEASURE visible-creature filter (512 targets x 400): observer pos_abs calls %d -> %d; target pos_abs calls %d -> %d; elapsed %.2f -> %.2f ms; checksum=%llu\n",
                before.observer_positions, after.observer_positions,
                before.target_positions, after.target_positions,
                before.ms, after.ms, before.checksum);
    std::puts("PASS visible-creature filter: identical ordered results, distance/LOS calls, and lazy empty-case behavior");
}
'''
cpp = out / "test.cpp"
binary = out / "test"
cpp.write_text(program)
subprocess.run(["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                str(cpp), "-o", str(binary)], check=True, timeout=60)
subprocess.run([str(binary)], check=True, timeout=30)
print("PASS visible-creature source contract and differential scenarios")
