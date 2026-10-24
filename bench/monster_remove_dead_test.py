#!/usr/bin/env python3
"""Differential tests for stable, linear-time creature_tracker::remove_dead().

Usage: python3 bench/monster_remove_dead_test.py /path/to/patched/0.I
Compiles the actual upstream and transformed method bodies against controlled
tracker fixtures. Benchmarks are subsystem-only, not full-game timings.
"""
from pathlib import Path
import subprocess
import tempfile
import sys

source = Path(sys.argv[1]).resolve()
out = Path(__file__).resolve().parent / "out" / "monster-remove-dead"
out.mkdir(parents=True, exist_ok=True)


def original(path: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(source), "show", "HEAD:src/" + path], text=True
    )


def function(text: str, signature: str) -> str:
    assert text.count(signature) == 1, f"expected exactly one: {signature}"
    start = text.index(signature)
    opening = text.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


old_tracker = original("creature_tracker.cpp")
new_tracker = (source / "src/creature_tracker.cpp").read_text(encoding="utf-8")
old_method = function(old_tracker, "void creature_tracker::remove_dead()")
new_method = function(new_tracker, "void creature_tracker::remove_dead()")

assert "iter = monsters_list.erase( iter );" in old_method
assert "remove_from_location_map( *critter );" in old_method
assert "removed_this_turn_.clear();" in old_method
assert new_method.count("monsters_snapshot_.reset();") == 1
assert "monsters_list.erase( write, monsters_list.end() );" in new_method
assert "monsters_list.erase( iter );" not in new_method
assert "if( write != read )" in new_method
assert "removed_this_turn_.clear();" in new_method

old_method = old_method.replace(
    "void creature_tracker::remove_dead()", "void tracker::remove_dead()", 1
)
new_method = new_method.replace(
    "void creature_tracker::remove_dead()", "void tracker::remove_dead()", 1
)

program = r'''
#include <algorithm>
#include <cassert>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <memory>
#include <random>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

struct monster {
    int id = -1;
    bool dead = false;
    bool is_dead() const { return dead; }
};
struct snapshot_state {
    bool cached = false;
    int reset_count = 0;
    void reset() { ++reset_count; cached = false; }
};

template<class Tracker>
void populate(Tracker &tracker, const std::vector<int> &dead_flags) {
    for (std::size_t i = 0; i < dead_flags.size(); ++i) {
        auto ptr = std::make_shared<monster>();
        ptr->id = static_cast<int>(i);
        ptr->dead = dead_flags[i] != 0;
        tracker.monsters_list.push_back(ptr);
        tracker.location_index.emplace(ptr->id, ptr);
    }
    tracker.removed_this_turn_ = {11, 22, 33};
    tracker.monsters_snapshot_.cached = true;
}

template<class Tracker>
std::vector<int> list_ids(const Tracker &tracker) {
    std::vector<int> result;
    result.reserve(tracker.monsters_list.size());
    for (const auto &ptr : tracker.monsters_list) {
        assert(ptr);
        result.push_back(ptr->id);
    }
    return result;
}

template<class Tracker>
std::vector<int> sorted_location_ids(const Tracker &tracker) {
    std::vector<int> result;
    result.reserve(tracker.location_index.size());
    for (const auto &entry : tracker.location_index) {
        assert(entry.second);
        assert(entry.first == entry.second->id);
        result.push_back(entry.first);
    }
    std::sort(result.begin(), result.end());
    return result;
}

template<class Tracker>
void validate_tracker(const Tracker &tracker, const std::vector<int> &dead_flags) {
    std::vector<int> expected;
    for (std::size_t i = 0; i < dead_flags.size(); ++i) {
        if (!dead_flags[i]) expected.push_back(static_cast<int>(i));
    }
    assert(list_ids(tracker) == expected);
    assert(sorted_location_ids(tracker) == expected);
    assert(tracker.location_index.size() == expected.size());
    for (const auto &ptr : tracker.monsters_list) {
        assert(!ptr->dead);
        const auto found = tracker.location_index.find(ptr->id);
        assert(found != tracker.location_index.end());
        assert(found->second.get() == ptr.get());
    }
    assert(tracker.removed_this_turn_.empty());
}

namespace baseline {
class tracker {
public:
    std::vector<std::shared_ptr<monster>> monsters_list;
    std::unordered_map<int, std::shared_ptr<monster>> location_index;
    std::vector<int> removed_this_turn_;
    snapshot_state monsters_snapshot_;

    void remove_from_location_map(const monster &critter) {
        const auto pos_iter = location_index.find(critter.id);
        if (pos_iter != location_index.end() && pos_iter->second.get() == &critter) {
            location_index.erase(pos_iter);
            return;
        }
        const auto iter = std::find_if(location_index.begin(), location_index.end(),
            [&](const auto &entry) { return entry.second.get() == &critter; });
        if (iter != location_index.end()) location_index.erase(iter);
    }
    void remove_dead();
};
}
namespace optimized {
class tracker {
public:
    std::vector<std::shared_ptr<monster>> monsters_list;
    std::unordered_map<int, std::shared_ptr<monster>> location_index;
    std::vector<int> removed_this_turn_;
    snapshot_state monsters_snapshot_;

    void remove_from_location_map(const monster &critter) {
        const auto pos_iter = location_index.find(critter.id);
        if (pos_iter != location_index.end() && pos_iter->second.get() == &critter) {
            location_index.erase(pos_iter);
            return;
        }
        const auto iter = std::find_if(location_index.begin(), location_index.end(),
            [&](const auto &entry) { return entry.second.get() == &critter; });
        if (iter != location_index.end()) location_index.erase(iter);
    }
    void remove_dead();
};
}
''';
program += "namespace baseline {\n" + old_method + "\n}\n"
program += "namespace optimized {\n" + new_method + "\n}\n"
program += r'''
template<class BaselineTracker, class OptimizedTracker>
void compare_case(const std::vector<int> &dead_flags) {
    BaselineTracker before;
    OptimizedTracker after;
    populate(before, dead_flags);
    populate(after, dead_flags);
    before.remove_dead();
    after.remove_dead();
    validate_tracker(before, dead_flags);
    validate_tracker(after, dead_flags);
    assert(list_ids(before) == list_ids(after));
    assert(sorted_location_ids(before) == sorted_location_ids(after));

    const bool any_dead = std::any_of(dead_flags.begin(), dead_flags.end(),
                                      [](int v) { return v != 0; });
    assert(before.monsters_snapshot_.cached);
    assert(before.monsters_snapshot_.reset_count == 0);
    assert(after.monsters_snapshot_.cached == !any_dead);
    assert(after.monsters_snapshot_.reset_count == (any_dead ? 1 : 0));
}

int main() {
    compare_case<baseline::tracker, optimized::tracker>({});
    for (int n : {1, 2, 3, 10, 100}) {
        compare_case<baseline::tracker, optimized::tracker>(std::vector<int>(n, 0));
        compare_case<baseline::tracker, optimized::tracker>(std::vector<int>(n, 1));
        std::vector<int> first(n, 0); if (n) first[0] = 1;
        compare_case<baseline::tracker, optimized::tracker>(first);
        std::vector<int> last(n, 0); if (n) last[n - 1] = 1;
        compare_case<baseline::tracker, optimized::tracker>(last);
        std::vector<int> alternating(n, 0);
        for (int i = 0; i < n; ++i) alternating[i] = (i % 2 == 0);
        compare_case<baseline::tracker, optimized::tracker>(alternating);
        std::vector<int> clustered(n, 0);
        for (int i = 0; i < n / 3; ++i) clustered[i] = 1;
        compare_case<baseline::tracker, optimized::tracker>(clustered);
    }

    std::mt19937 rng(0xC0FFEE);
    for (int trial = 0; trial < 400; ++trial) {
        const int n = static_cast<int>(rng() % 401);
        std::vector<int> flags(n);
        for (int &flag : flags) flag = static_cast<int>(rng() % 3 == 0);
        compare_case<baseline::tracker, optimized::tracker>(flags);
    }
    std::puts("PASS remove_dead differential: 400 randomized and directed cases preserve survivor order, location index, removed-this-turn clearing and single snapshot invalidation");

    // Stress a representative wave of deaths. The baseline std::vector::erase
    // shifts every remaining element each time; stable compaction writes each
    // surviving pointer at most once and performs one tail erase.
    constexpr int n = 12000;
    std::vector<int> flags(n);
    std::uint64_t erase_shift_equivalents = 0;
    std::uint64_t compaction_writes = 0;
    bool saw_dead = false;
    for (int i = 0; i < n; ++i) {
        flags[i] = (i % 2 == 0);
        if (flags[i]) {
            erase_shift_equivalents += static_cast<std::uint64_t>(n - i - 1);
            saw_dead = true;
        } else if (saw_dead) {
            ++compaction_writes;
        }
    }
    baseline::tracker before;
    optimized::tracker after;
    populate(before, flags);
    populate(after, flags);
    const auto a = std::chrono::steady_clock::now();
    before.remove_dead();
    const auto b = std::chrono::steady_clock::now();
    after.remove_dead();
    const auto c = std::chrono::steady_clock::now();
    validate_tracker(before, flags);
    validate_tracker(after, flags);
    assert(list_ids(before) == list_ids(after));
    const double old_ms = std::chrono::duration<double, std::milli>(b - a).count();
    const double new_ms = std::chrono::duration<double, std::milli>(c - b).count();
    std::printf("MEASURE remove_dead (N=%d, alternating deaths): erase shift-equivalents=%llu -> compaction pointer writes=%llu; fixture elapsed %.2f -> %.2f ms\n",
                n, static_cast<unsigned long long>(erase_shift_equivalents),
                static_cast<unsigned long long>(compaction_writes), old_ms, new_ms);
    assert(erase_shift_equivalents >= static_cast<std::uint64_t>(n) * n / 4);
    assert(compaction_writes < static_cast<std::uint64_t>(n));
    std::puts("PASS remove_dead stress: large death wave retains identical surviving monsters and map entries");
}
'''
with tempfile.TemporaryDirectory(prefix="monster-remove-dead-", dir=out) as tmp:
    cpp = Path(tmp) / "test.cpp"
    binary = Path(tmp) / "test"
    cpp.write_text(program, encoding="utf-8")
    subprocess.run(
        ["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
         str(cpp), "-o", str(binary)],
        check=True, timeout=60
    )
    subprocess.run([str(binary)], check=True, timeout=60)
print("PASS remove_dead source contract and differential/complexity benchmark")
