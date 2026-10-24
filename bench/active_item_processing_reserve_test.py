#!/usr/bin/env python3
"""Differential test for active-item processing result capacity.

Extracts the actual get_for_processing() implementation from pristine CDDA 0.I
and patched source, compiles both against the same lightweight item-cache
fixture, and compares returned items, queue rotation and expired-reference
cleanup. Allocation measurements isolate this method; they are not whole-game
or physical-device measurements.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).resolve()
out = Path(__file__).resolve().parent / "out" / "active-item-reserve"
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


signature = "std::vector<item_reference> active_item_cache::get_for_processing()"
old = function(original("active_item_cache.cpp"), signature)
new = function((source / "src/active_item_cache.cpp").read_text(encoding="utf-8"),
               signature)
old_reserve = """items_to_process.reserve( std::accumulate( active_items.begin(), active_items.end(), std::size_t{ 0 },
    []( size_t prev, const auto & kv ) {
        return prev + kv.second.size() / static_cast<size_t>( kv.first );
    } ) );"""
new_reserve = """items_to_process.reserve( std::accumulate( active_items.begin(), active_items.end(), std::size_t{ 0 },
    []( size_t prev, const auto & kv ) {
        const size_t list_size = kv.second.size();
        const size_t max_this_queue = list_size == 0 ? 0 :
                                      std::min( list_size,
                                                list_size / static_cast<size_t>( kv.first ) + 1 );
        return prev + max_this_queue;
    } ) );"""
expected = old.replace(old_reserve, new_reserve, 1)
assert expected != old, "upstream reserve expression not found"
assert expected == new, "processing behavior changed outside the reserve expression"
assert "num_to_process >= 0" in old and "items_to_process.push_back( *it );" in old
assert "kv.second.splice( kv.second.end(), kv.second, kv.second.begin(), it );" in old

program = r'''
#include <algorithm>
#include <cassert>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <memory>
#include <new>
#include <numeric>
#include <list>
#include <unordered_map>
#include <utility>
#include <vector>

using std::size_t;
static bool count_allocations = false;
static size_t allocation_count = 0;
static size_t allocation_bytes = 0;
[[gnu::noinline]] void *operator new( std::size_t n ) {
    if( count_allocations ) { ++allocation_count; allocation_bytes += n; }
    if( void *p = std::malloc( n ? n : 1 ) ) { return p; }
    throw std::bad_alloc();
}
[[gnu::noinline]] void operator delete( void *p ) noexcept { std::free( p ); }
[[gnu::noinline]] void operator delete( void *p, std::size_t ) noexcept { std::free( p ); }

struct item {
    explicit item( int v ) : id( v ) {}
    int id;
};
struct item_reference {
    std::shared_ptr<item> item_ref;
    int id = -1;
};

namespace baseline {
class active_item_cache {
public:
    std::map<int, std::list<item_reference>> active_items;
    std::map<int, std::map<int, std::shared_ptr<item>>> active_items_index;
    std::vector<item_reference> get_for_processing();
};
}
namespace optimized {
class active_item_cache {
public:
    std::map<int, std::list<item_reference>> active_items;
    std::map<int, std::map<int, std::shared_ptr<item>>> active_items_index;
    std::vector<item_reference> get_for_processing();
};
}
''';
program += old.replace(
    "std::vector<item_reference> active_item_cache::get_for_processing()",
    "std::vector<item_reference> baseline::active_item_cache::get_for_processing()", 1
) + "\n";
program += new.replace(
    "std::vector<item_reference> active_item_cache::get_for_processing()",
    "std::vector<item_reference> optimized::active_item_cache::get_for_processing()", 1
) + "\n";
program += r'''
template<class Cache>
void populate(Cache &cache, int speed, int count, int null_every, int id_base) {
    auto &queue = cache.active_items[speed];
    auto &index = cache.active_items_index[speed];
    for (int i = 0; i < count; ++i) {
        item_reference ref;
        ref.id = id_base + i;
        if (null_every == 0 || (i + 1) % null_every != 0) {
            ref.item_ref = std::make_shared<item>( ref.id );
        }
        queue.push_back( ref );
        index.emplace( ref.id, ref.item_ref );
    }
}
template<class Cache>
std::vector<int> item_ids( const Cache &cache ) {
    std::vector<int> ids;
    for (const auto &group : cache.active_items) {
        for (const auto &ref : group.second) ids.push_back(ref.item_ref ? ref.id : -1);
        ids.push_back(-100000 - group.first);
    }
    return ids;
}
template<class Cache>
std::vector<int> index_sizes( const Cache &cache ) {
    std::vector<int> ret;
    for (const auto &group : cache.active_items_index) {
        ret.push_back(group.first);
        ret.push_back(static_cast<int>(group.second.size()));
    }
    return ret;
}
template<class Cache>
std::vector<int> returned_ids( Cache &cache ) {
    const auto result = cache.get_for_processing();
    std::vector<int> ids;
    for (const auto &ref : result) ids.push_back(ref.item_ref ? ref.id : -1);
    return ids;
}
template<class A, class B>
void compare_case(const A &seed_a, const B &seed_b, int cycles) {
    A before = seed_a;
    B after = seed_b;
    for (int turn = 0; turn < cycles; ++turn) {
        const auto a = returned_ids(before);
        const auto b = returned_ids(after);
        assert(a == b);
        assert(item_ids(before) == item_ids(after));
        assert(index_sizes(before) == index_sizes(after));
    }
}
int main() {
    // All queue sizes around exact divisors, with different speeds and broken
    // references. Check both returned sequence and rotation across repeated calls.
    for (int speed : {1, 2, 3, 4, 5, 7, 11}) {
        for (int count = 0; count <= 100; ++count) {
            for (int null_every : {0, 3, 7}) {
                baseline::active_item_cache before;
                optimized::active_item_cache after;
                populate(before, speed, count, null_every, 1000);
                populate(after, speed, count, null_every, 1000);
                // A second cadence bucket tests combined reserve estimates.
                populate(before, 3, (count * 7) % 37, null_every, 5000);
                populate(after, 3, (count * 7) % 37, null_every, 5000);
                compare_case(before, after, 4);
            }
        }
    }
    std::puts("PASS active-item reserve differential: returned references, expired-reference removal, index invalidation, and queue rotation match upstream");

    baseline::active_item_cache before;
    optimized::active_item_cache after;
    for (const auto &[speed, count, id] :
         std::vector<std::tuple<int, int, int>>{
             {2, 20000, 100000}, {3, 20001, 200000},
             {5, 20003, 300000}, {7, 20005, 400000}, {1, 20000, 500000} }) {
        populate(before, speed, count, 0, id);
        populate(after, speed, count, 0, id);
    }

    constexpr int repetitions = 500;
    allocation_count = allocation_bytes = 0;
    count_allocations = true;
    for (int i = 0; i < repetitions; ++i) {
        const auto values = before.get_for_processing();
        assert(!values.empty());
    }
    count_allocations = false;
    const size_t old_allocations = allocation_count;
    const size_t old_bytes = allocation_bytes;

    allocation_count = allocation_bytes = 0;
    count_allocations = true;
    for (int i = 0; i < repetitions; ++i) {
        const auto values = after.get_for_processing();
        assert(!values.empty());
    }
    count_allocations = false;
    const size_t new_allocations = allocation_count;
    const size_t new_bytes = allocation_bytes;

    assert(old_allocations == static_cast<size_t>(repetitions) * 2);
    assert(new_allocations == static_cast<size_t>(repetitions));
    assert(old_bytes > new_bytes);
    std::printf("MEASURE active-item processing (%d calls): allocations %zu -> %zu; requested bytes %zu -> %zu\n",
                repetitions, old_allocations, new_allocations, old_bytes, new_bytes);
    std::puts("PASS active-item reserve benchmark: one result-vector allocation per call, no reallocation");
}
''';

# The fixture uses std::tuple in its workload table.
program = program.replace("#include <utility>\n", "#include <utility>\n#include <tuple>\n");

with tempfile.TemporaryDirectory(prefix="active-item-reserve-", dir=out) as tmp:
    cpp = Path(tmp) / "test.cpp"
    binary = Path(tmp) / "test"
    cpp.write_text(program, encoding="utf-8")
    subprocess.run(
        ["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
         str(cpp), "-o", str(binary)],
        check=True, timeout=90
    )
    subprocess.run([str(binary)], check=True, timeout=90)
print("PASS active-item source guard, differential queue semantics, and allocation benchmark")
