#!/usr/bin/env python3
"""Differential test for skipping empty owning snapshots in kill_marked_for_death().

Compiles the real upstream and transformed method bodies against controlled
fixtures. The no-death benchmark counts tracker-list copies; it is not a
full-game timing or physical-device benchmark.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).resolve()
out = Path(__file__).resolve().parent / "out" / "monster-death-sweep"
out.mkdir(parents=True, exist_ok=True)


def original(path: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(source), "show", "HEAD:src/" + path], text=True
    )


def function(text: str, signature: str) -> str:
    assert text.count(signature) == 1, f"expected exactly one {signature}"
    start = text.index(signature)
    opening = text.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


old = function(original("creature_tracker.cpp"),
               "bool creature_tracker::kill_marked_for_death()")
new = function((source / "src/creature_tracker.cpp").read_text(encoding="utf-8"),
               "bool creature_tracker::kill_marked_for_death()")
copy_statement = "    const auto copy = monsters_list;"
guard = """    // Most turns have no dead monsters; avoid copying every shared_ptr in that case.
    bool has_dead_monster = false;
    for( const shared_ptr_fast<monster> &mon_ptr : monsters_list ) {
        cata_assert( mon_ptr );
        if( mon_ptr->is_dead() ) {
            has_dead_monster = true;
            break;
        }
    }
    if( !has_dead_monster ) {
        return false;
    }

"""
assert old.count(copy_statement) == 1
assert new.count(copy_statement) == 1
assert guard + copy_statement in new
assert new.replace(guard, "", 1) == old, (
    "kill_marked_for_death changed outside the guarded snapshot")
old = old.replace("bool creature_tracker::kill_marked_for_death()",
                  "bool tracker::kill_marked_for_death()", 1)
new = new.replace("bool creature_tracker::kill_marked_for_death()",
                  "bool tracker::kill_marked_for_death()", 1)

program = r'''
#include <algorithm>
#include <cassert>
#include <chrono>
#include <cstddef>
#include <cstdio>
#include <functional>
#include <memory>
#include <random>
#include <string>
#include <utility>
#include <vector>

template<class T> using shared_ptr_fast = std::shared_ptr<T>;
struct map {};
struct tripoint_abs_ms {
    int value = 0;
    std::string to_string_writable() const { return std::to_string(value); }
};
struct monster;
static std::vector<int> death_order;
static std::function<void(monster &)> death_hook;
static constexpr int D_INFO = 0;
struct log_sink {
    template<class T> log_sink &operator<<( const T & ) { return *this; }
};
log_sink dbg( int ) { return {}; }
template<class... T> std::string string_format( const char *, T&&... ) { return {}; }
map global_map;
map &get_map() { return global_map; }

struct monster {
    int id = -1;
    int hp = 100;
    bool dead = false;
    tripoint_abs_ms pos_abs() const { return {id}; }
    int get_hp() const { return hp; }
    std::string name() const { return "monster-" + std::to_string(id); }
    bool is_dead() const { return dead; }
    void die( map *, void * ) {
        death_order.push_back(id);
        if (death_hook) death_hook(*this);
    }
};
#define cata_assert(value) assert(value)

// A vector-like fixture that counts exactly when the real function makes its
// owning snapshot. Copies preserve the counter pointer of their source.
template<class T>
class counted_vector {
public:
    using storage_type = std::vector<T>;
    using const_iterator = typename storage_type::const_iterator;
    explicit counted_vector(int *copies = nullptr) : copies_(copies) {}
    counted_vector(const counted_vector &other)
        : values_(other.values_), copies_(other.copies_) {
        if (copies_ != nullptr) ++*copies_;
    }
    const_iterator begin() const { return values_.begin(); }
    const_iterator end() const { return values_.end(); }
    std::size_t size() const { return values_.size(); }
    void push_back(const T &value) { values_.push_back(value); }
private:
    storage_type values_;
    int *copies_ = nullptr;
};

namespace baseline {
class tracker {
public:
    int snapshot_copies = 0;
    counted_vector<shared_ptr_fast<monster>> monsters_list;
    tracker() : monsters_list(&snapshot_copies) {}
    bool kill_marked_for_death();
};
}
namespace optimized {
class tracker {
public:
    int snapshot_copies = 0;
    counted_vector<shared_ptr_fast<monster>> monsters_list;
    tracker() : monsters_list(&snapshot_copies) {}
    bool kill_marked_for_death();
};
}
''';
program += "namespace baseline {\n" + old + "\n}\n"
program += "namespace optimized {\n" + new + "\n}\n"
program += r'''
struct outcome {
    bool returned = false;
    bool spawned = false;
    int snapshot_copies = 0;
    std::vector<int> deaths;
    std::vector<int> ids;
};
template<class Tracker>
void populate(Tracker &tracker, const std::vector<int> &flags) {
    for (std::size_t i = 0; i < flags.size(); ++i) {
        auto ptr = std::make_shared<monster>();
        ptr->id = static_cast<int>(i);
        ptr->dead = flags[i] != 0;
        tracker.monsters_list.push_back(ptr);
    }
}
template<class Tracker>
outcome run_case(const std::vector<int> &flags, bool spawn_on_death) {
    Tracker tracker;
    populate(tracker, flags);
    death_order.clear();
    bool spawned = false;
    death_hook = [&tracker, spawn_on_death, &spawned](monster &) {
        if (spawn_on_death && !spawned) {
            auto child = std::make_shared<monster>();
            child->id = 900000;
            child->dead = true;
            tracker.monsters_list.push_back(child);
            spawned = true;
        }
    };
    const bool returned = tracker.kill_marked_for_death();
    death_hook = {};
    outcome result;
    result.returned = returned;
    result.spawned = spawned;
    result.snapshot_copies = tracker.snapshot_copies;
    result.deaths = death_order;
    for (const auto &ptr : tracker.monsters_list) result.ids.push_back(ptr->id);
    return result;
}
template<class Baseline, class Optimized>
void compare_case(const std::vector<int> &flags, bool spawn_on_death = false) {
    const outcome before = run_case<Baseline>(flags, spawn_on_death);
    const outcome after = run_case<Optimized>(flags, spawn_on_death);
    const bool any_dead = std::any_of(flags.begin(), flags.end(),
                                      [](int f) { return f != 0; });
    std::vector<int> expected_deaths;
    std::vector<int> expected_ids;
    for (std::size_t i = 0; i < flags.size(); ++i) {
        expected_ids.push_back(static_cast<int>(i));
        if (flags[i]) expected_deaths.push_back(static_cast<int>(i));
    }
    if (spawn_on_death && any_dead) expected_ids.push_back(900000);
    assert(before.returned == any_dead && after.returned == any_dead);
    assert(before.deaths == expected_deaths && after.deaths == expected_deaths);
    assert(before.ids == expected_ids && after.ids == expected_ids);
    assert(before.ids == after.ids && before.deaths == after.deaths);
    assert(before.spawned == (spawn_on_death && any_dead));
    assert(after.spawned == (spawn_on_death && any_dead));
    assert(before.snapshot_copies == 1);
    assert(after.snapshot_copies == (any_dead ? 1 : 0));
}

int main() {
    compare_case<baseline::tracker, optimized::tracker>({});
    for (int n : {1, 2, 3, 12, 128}) {
        compare_case<baseline::tracker, optimized::tracker>(std::vector<int>(n, 0));
        compare_case<baseline::tracker, optimized::tracker>(std::vector<int>(n, 1), true);
        std::vector<int> first(n, 0); if (n) first[0] = 1;
        compare_case<baseline::tracker, optimized::tracker>(first, true);
        std::vector<int> last(n, 0); if (n) last[n - 1] = 1;
        compare_case<baseline::tracker, optimized::tracker>(last, true);
        std::vector<int> alternating(n, 0);
        for (int i = 0; i < n; ++i) alternating[i] = (i % 2 == 0);
        compare_case<baseline::tracker, optimized::tracker>(alternating, true);
    }
    std::mt19937 rng(0x51EE9);
    for (int trial = 0; trial < 400; ++trial) {
        const int n = static_cast<int>(rng() % 401);
        std::vector<int> flags(n);
        for (int &flag : flags) flag = static_cast<int>(rng() % 5 == 0);
        compare_case<baseline::tracker, optimized::tracker>(flags, true);
    }
    std::puts("PASS death sweep differential: 400 randomized and directed cases preserve return value, die order and newly spawned monster behavior");

    // Large no-death turn: an upstream snapshot copies every shared_ptr even
    // when no monster is dead. Candidate must return without taking that copy.
    constexpr int n = 50000;
    std::vector<int> live(n, 0);
    baseline::tracker before;
    optimized::tracker after;
    populate(before, live);
    populate(after, live);
    death_hook = {};
    death_order.clear();
    const auto a = std::chrono::steady_clock::now();
    const bool old_return = before.kill_marked_for_death();
    const auto b = std::chrono::steady_clock::now();
    const bool new_return = after.kill_marked_for_death();
    const auto c = std::chrono::steady_clock::now();
    assert(!old_return && !new_return);
    assert(before.snapshot_copies == 1 && after.snapshot_copies == 0);
    assert(before.monsters_list.size() == static_cast<std::size_t>(n));
    assert(after.monsters_list.size() == static_cast<std::size_t>(n));
    const double old_ms = std::chrono::duration<double, std::milli>(b - a).count();
    const double new_ms = std::chrono::duration<double, std::milli>(c - b).count();
    std::printf("MEASURE empty death sweep (N=%d live monsters): owning list snapshots %d -> %d; fixture elapsed %.2f -> %.2f ms\n",
                n, before.snapshot_copies, after.snapshot_copies, old_ms, new_ms);
    std::puts("PASS empty death sweep: no-death path skips snapshot allocation; death path retains snapshot for safe monster spawning");
}
'''
with tempfile.TemporaryDirectory(prefix="monster-death-sweep-", dir=out) as tmp:
    cpp = Path(tmp) / "test.cpp"
    binary = Path(tmp) / "test"
    cpp.write_text(program, encoding="utf-8")
    subprocess.run(
        ["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
         str(cpp), "-o", str(binary)],
        check=True, timeout=60
    )
    subprocess.run([str(binary)], check=True, timeout=60)
print("PASS kill_marked_for_death source contract and snapshot benchmark")
