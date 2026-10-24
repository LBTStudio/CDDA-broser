#!/usr/bin/env python3
"""Differential + allocation benchmark for nearby-item grouping.

Usage: nearby_items_hash_test.py PATCHED_0I
HEAD must be pristine upstream CDDA 0.I with the wrapper patch set applied.
Tests actual extracted grouping functions against upstream and the previous
map+name-vector implementation. Timings are subsystem microbenchmarks, not
full-game/Chromebook claims.
"""
from pathlib import Path
import subprocess
import tempfile

source = Path(__import__("sys").argv[1]).resolve()
out = Path(__file__).resolve().parent / "out"
out.mkdir(exist_ok=True)


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


old_game = upstream("game.cpp")
new_game = (source / "src" / "game.cpp").read_text()
old_add = function(old_game, "static void add_item_recursive(")
old_find = function(old_game, "std::vector<map_item_stack> game::find_nearby_items(")
opt_add = function(new_game, "static void add_item_recursive(")
opt_find = function(new_game, "std::vector<map_item_stack> game::find_nearby_items(")

assert "std::vector<map_item_stack *>" in opt_add
assert "std::unordered_map<std::string, map_item_stack>" in opt_add
assert "stack_order.push_back( &entry.first->second );" in opt_add
assert "std::move( *stack )" in opt_find
assert "std::find(" not in opt_add and "try_emplace(" in opt_add

# Reconstruct the existing main-branch implementation prior to this
# optimization: try_emplace + std::map + vector<string> + move-out.
base_add = opt_add
base_add = base_add.replace(
    "std::vector<map_item_stack *> &stack_order",
    "std::vector<std::string> &item_order", 1)
base_add = base_add.replace(
    "std::unordered_map<std::string, map_item_stack> &temp_items",
    "std::map<std::string, map_item_stack> &temp_items", 1)
base_add = base_add.replace(
    "stack_order.push_back( &entry.first->second );",
    "item_order.push_back( name );", 1)
base_add = base_add.replace(
    "add_item_recursive( stack_order, temp_items,",
    "add_item_recursive( item_order, temp_items,")
base_find = opt_find.replace(
    "std::unordered_map<std::string, map_item_stack> temp_items;",
    "std::map<std::string, map_item_stack> temp_items;", 1)
base_find = base_find.replace(
    "std::vector<map_item_stack *> stack_order;",
    "std::vector<std::string> item_order;", 1)
base_find = base_find.replace(
    "add_item_recursive( stack_order, temp_items,",
    "add_item_recursive( item_order, temp_items,", 1)
base_find = base_find.replace("stack_order.size()", "item_order.size()", 1)
base_finish = """    for( map_item_stack *stack : stack_order ) {
        ret.push_back( std::move( *stack ) );
    }"""
old_finish = """    for( auto &elem : item_order ) {
        ret.push_back( std::move( temp_items.at( elem ) ) );
    }"""
assert base_finish in base_find
base_find = base_find.replace(base_finish, old_finish, 1)
assert "std::unordered_map" not in base_add + base_find
assert "std::vector<std::string> &item_order" in base_add

stack_h = (source / "src/map_item_stack.h").read_text()
stack_h = stack_h[
    stack_h.index("class map_item_stack\n"):
    stack_h.index("\nstd::vector<map_item_stack> filter_item_stacks")]
stack_cpp = (source / "src/map_item_stack.cpp").read_text()
stack_cpp = stack_cpp[
    stack_cpp.index("map_item_stack::item_group::item_group()"):
    stack_cpp.index("bool map_item_stack::compare_item_names")]

program = r'''
#include <algorithm>
#include <cassert>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <memory>
#include <new>
#include <random>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

static bool count_allocations = false;
static std::size_t allocations = 0, allocated_bytes = 0;
static bool verify_buffers = false;
[[gnu::noinline]] void *operator new(std::size_t n) {
    if (count_allocations) { ++allocations; allocated_bytes += n; }
    if (void *p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
[[gnu::noinline]] void *operator new[](std::size_t n) {
    if (count_allocations) { ++allocations; allocated_bytes += n; }
    if (void *p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
[[gnu::noinline]] void operator delete(void *p) noexcept { std::free(p); }
[[gnu::noinline]] void operator delete[](void *p) noexcept { std::free(p); }
[[gnu::noinline]] void operator delete(void *p, std::size_t) noexcept { std::free(p); }
[[gnu::noinline]] void operator delete[](void *p, std::size_t) noexcept { std::free(p); }

using tripoint_rel_ms = int;
struct item {
    std::string name;
    int amount = 1;
    std::vector<const item *> contents;
    std::string tname() const { return name; }
    int count() const { return amount; }
    const std::vector<const item *> &all_known_contents() const { return contents; }
};
static std::size_t linear_comparisons = 0;
template<class It> It counted_find(It first, It last, const std::string &value) {
    while (first != last) {
        ++linear_comparisons;
        if (*first == value) break;
        ++first;
    }
    return first;
}
'''.replace("std::find(", "counted_find(")

program += stack_h + "\n" + stack_cpp

variants = [
    ("legacy", old_add, old_find, "std::map<std::string, map_item_stack>",
     "std::vector<std::string>", "item_order", False),
    ("current", base_add, base_find, "std::map<std::string, map_item_stack>",
     "std::vector<std::string>", "item_order", True),
    ("optimized", opt_add, opt_find, "std::unordered_map<std::string, map_item_stack>",
     "std::vector<map_item_stack *>", "stack_order", True),
]
for ns, add, find, map_type, order_type, order_name, moved in variants:
    program += f"\nnamespace {ns} {{\n"
    # Count legacy std::find comparisons; candidate and current use try_emplace.
    program += add.replace("std::find(", "counted_find(") + "\n"
    program += f"""
std::vector<map_item_stack> collect(const std::vector<item> &items) {{
    {map_type} temp_items;
    std::vector<map_item_stack> ret;
    {order_type} {order_name};
    for (std::size_t i = 0; i < items.size(); ++i) {{
        add_item_recursive({order_name}, temp_items, &items[i], int(i / 4));
    }}
"""
    if ns == "current":
        program += r'''
    std::vector<const void *> buffers;
    if (verify_buffers) {
        buffers.reserve(item_order.size());
        for (const auto &name : item_order) buffers.push_back(temp_items.at(name).vIG.data());
    }
'''
    elif ns == "optimized":
        program += r'''
    std::vector<const void *> buffers;
    if (verify_buffers) {
        buffers.reserve(stack_order.size());
        for (const map_item_stack *stack : stack_order) buffers.push_back(stack->vIG.data());
    }
'''
    finish = find[find.index("    ret.reserve("):]
    if ns == "current":
        finish = finish.replace(
            "    return ret;",
            "    if (verify_buffers) for (std::size_t i = 0; i < ret.size(); ++i) assert(ret[i].vIG.data() == buffers[i]);\n    return ret;")
    elif ns == "optimized":
        finish = finish.replace(
            "    return ret;",
            "    if (verify_buffers) for (std::size_t i = 0; i < ret.size(); ++i) assert(ret[i].vIG.data() == buffers[i]);\n    return ret;")
    program += finish + "\n}\n} // namespace\n"

program += r'''
static void compare_results(const std::vector<map_item_stack> &a,
                            const std::vector<map_item_stack> &b) {
    assert(a.size() == b.size());
    for (std::size_t i = 0; i < a.size(); ++i) {
        assert(a[i].example == b[i].example && a[i].totalcount == b[i].totalcount);
        assert(a[i].vIG.size() == b[i].vIG.size());
        for (std::size_t j = 0; j < a[i].vIG.size(); ++j) {
            assert(a[i].vIG[j].it == b[i].vIG[j].it);
            assert(a[i].vIG[j].pos == b[i].vIG[j].pos);
            assert(a[i].vIG[j].count == b[i].vIG[j].count);
        }
    }
}
static unsigned long long checksum(const std::vector<map_item_stack> &items) {
    unsigned long long value = items.size();
    for (const auto &stack : items) {
        value = value * 33 + static_cast<unsigned>(stack.totalcount);
        value = value * 33 + stack.vIG.size();
        for (const auto &entry : stack.vIG) {
            value = value * 33 + reinterpret_cast<std::uintptr_t>(entry.it);
            value = value * 33 + static_cast<unsigned>(entry.pos);
            value = value * 33 + static_cast<unsigned>(entry.count);
        }
    }
    return value;
}
static void compare(const std::vector<item> &items) {
    verify_buffers = true;
    const auto old_result = legacy::collect(items);
    const auto current_result = current::collect(items);
    const auto optimized_result = optimized::collect(items);
    verify_buffers = false;
    compare_results(old_result, current_result);
    compare_results(current_result, optimized_result);
}
struct Metrics {
    std::size_t allocation_count = 0;
    std::size_t bytes = 0;
    double elapsed_ms = 0;
    unsigned long long sum = 0;
};
template<class Collect>
Metrics measure(Collect collect, const std::vector<item> &items, int repetitions) {
    Metrics metrics;
    for (int i = 0; i < repetitions; ++i) {
        allocations = allocated_bytes = 0;
        count_allocations = true;
        const auto start = std::chrono::steady_clock::now();
        auto groups = collect(items);
        const auto finish = std::chrono::steady_clock::now();
        count_allocations = false;
        metrics.allocation_count += allocations;
        metrics.bytes += allocated_bytes;
        metrics.elapsed_ms += std::chrono::duration<double, std::milli>(finish-start).count();
        metrics.sum = metrics.sum * 33 + checksum(groups);
    }
    return metrics;
}
int main() {
    std::mt19937 rng(1234);
    const std::vector<std::string> names = {
        "", "water", "包帯", "死体(新鮮)", "ドロドロの死体(新鮮)", "死体(腐敗)"
    };
    for (int n = 0; n < 200; ++n) {
        std::vector<item> nested(12), items(n);
        for (std::size_t i = 0; i < nested.size(); ++i) {
            nested[i].name = names[rng() % names.size()];
            nested[i].amount = 1 + rng() % 50;
            if (i) nested[i].contents.push_back(&nested[i - 1]);
        }
        for (auto &it : items) {
            it.name = names[rng() % names.size()];
            it.amount = 1 + rng() % 50;
            if (rng() % 3 == 0) it.contents.push_back(&nested[rng() % nested.size()]);
        }
        compare(items);
    }
    std::vector<item> corpses(2);
    corpses[0].name = corpses[1].name = "死体(新鮮)";
    assert(optimized::collect(corpses).size() == 1);
    corpses[0].name = "ドロドロの死体(新鮮)";
    compare(corpses); assert(optimized::collect(corpses).size() == 2);
    corpses[1].name = "ドロドロの死体(新鮮)";
    compare(corpses); assert(optimized::collect(corpses).size() == 1);

    // Stress hash-table rehash and long names that force std::string heap storage.
    std::vector<item> many(1600);
    for (std::size_t i = 0; i < many.size(); ++i) {
        many[i].name = "distinct-nearby-item-display-name-" + std::to_string(i) + "-long";
        many[i].amount = 1 + static_cast<int>(i % 9);
    }
    compare(many);
    const Metrics before = measure(current::collect, many, 3);
    const Metrics after = measure(optimized::collect, many, 3);
    assert(before.sum == after.sum);
    assert(after.allocation_count < before.allocation_count);
    assert(after.bytes < before.bytes);
    std::printf("MEASURE nearby-item grouping (1600 unique long names x3): allocations %zu -> %zu; cumulative bytes %zu -> %zu; elapsed %.2f -> %.2f ms; checksum=%llu\n",
                before.allocation_count, after.allocation_count,
                before.bytes, after.bytes, before.elapsed_ms, after.elapsed_ms, after.sum);
    std::puts("PASS nearby items: upstream/current/optimized group order, counts, positions and item pointers match; unordered-map rehash preserves mapped-stack references");
}
'''
with tempfile.TemporaryDirectory(prefix="nearby-item-hash-", dir=out) as tmp:
    cpp = Path(tmp) / "test.cpp"
    binary = Path(tmp) / "test"
    cpp.write_text(program)
    subprocess.run(["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                    str(cpp), "-o", str(binary)], check=True, timeout=60)
    subprocess.run([str(binary)], check=True, timeout=60)
print("PASS nearby-item source contract and differential/memory benchmark")
