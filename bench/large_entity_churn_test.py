#!/usr/bin/env python3
"""Differential and allocation tests for high-entity-count engine hot paths.

Usage: python3 bench/large_entity_churn_test.py /path/to/patched/0.I
HEAD must be pristine upstream CDDA 0.I. The tests compile extracted real
functions against controlled fixtures; timings are subsystem measurements,
not full-game, Chromebook, or total-RAM claims.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).resolve()
out = Path(__file__).resolve().parent / "out" / "large-entity-churn"
out.mkdir(parents=True, exist_ok=True)


def original(path):
    return subprocess.check_output(
        ["git", "-C", str(source), "show", "HEAD:src/" + path], text=True)


def function(text, signature):
    start = text.index(signature)
    opening = text.index("{", start)
    depth, pos = 1, opening + 1
    while depth:
        depth += (text[pos] == "{") - (text[pos] == "}")
        pos += 1
    return text[start:pos]


old_tracker = original("creature_tracker.cpp")
new_tracker = (source / "src/creature_tracker.cpp").read_text()
old_update = function(old_tracker, "bool creature_tracker::update_pos(")
new_update = function(new_tracker, "bool creature_tracker::update_pos(")
fast_path = """    // Movement normally keeps this position index and monsters_list in sync.
    // Use the index for the common path instead of scanning every monster.
    // Keep the list scan below as recovery for a missing/stale position entry.
    const auto tracked_iter = monsters_by_location.find( old_pos );
    if( tracked_iter != monsters_by_location.end() && tracked_iter->second.get() == &critter ) {
        const shared_ptr_fast<monster> tracked = tracked_iter->second;
        monsters_by_location.erase( tracked_iter );
        monsters_by_location[new_pos] = tracked;
        return true;
    }

"""
assert fast_path in new_update, "indexed movement fast path missing"
assert new_update.replace(fast_path, "", 1) == old_update, (
    "update_pos changed beyond its indexed fast path; review upstream changes")

old_map = original("map.cpp")
new_map = (source / "src/map.cpp").read_text()
old_process = function(old_map, "void map::process_items()")
new_process = function(new_map, "void map::process_items()")
old_loop_start = "        std::set<tripoint> submaps_with_vehicles;"
new_loop_start = "        const auto process_vehicle_submap = [this]( const tripoint &pos ) {"
tail_marker = "\n    }\n    update_submaps_with_active_items();"
old_block_start = old_process.index(old_loop_start)
new_block_start = new_process.index(new_loop_start)
old_block_end = old_process.index(tail_marker, old_block_start)
new_block_end = new_process.index(tail_marker, new_block_start)
normalized_process = (new_process[:new_block_start] +
                     old_process[old_block_start:old_block_end] +
                     new_process[new_block_end:])
assert normalized_process == old_process, (
    "map::process_items changed outside the vehicle-submap grouping block")
assert "for_each_unique_vehicle_submap( cache.vehicle_list, *this, process_vehicle_submap );" in new_process

helper_signature = "static void for_each_unique_vehicle_submap("
helper_start = new_map.rfind("template<typename VehicleRange, typename MapRef, typename ProcessFn>",
                             0, new_map.index(helper_signature))
assert helper_start >= 0, "vehicle-submap helper template not found"
helper_body = function(new_map[helper_start:], "template<typename VehicleRange, typename MapRef, typename ProcessFn>")
assert "inline_capacity = 32" in helper_body
assert "unique_capacity = 256" in helper_body
assert "table_capacity = 512" in helper_body
assert "std::array<int, table_capacity> indices{};" in helper_body
assert "indices.fill( -1 );" in helper_body
assert "std::sort( positions.begin(), last );" in helper_body
assert "fallback.reserve( vehicles.size() );" in helper_body
assert "std::set<tripoint> positions;" not in helper_body


def cxx_function(body):
    body = re.sub(r"bool creature_tracker::update_pos\s*\(", "bool update_pos(", body, count=1)
    old_scan = "std::find_if( monsters_list.begin(), monsters_list.end(),"
    assert old_scan in body, "expected upstream recovery/list scan"
    return body.replace(old_scan, "counted_find_if( monsters_list.begin(), monsters_list.end(),", 1)


old_update_cxx = cxx_function(old_update)
new_update_cxx = cxx_function(new_update)

common = r'''
#include <algorithm>
#include <array>
#include <cassert>
#include <chrono>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <memory>
#include <new>
#include <set>
#include <string>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>

static bool count_allocations = false;
static std::size_t allocation_count = 0;
static std::size_t allocation_bytes = 0;
[[gnu::noinline]] void *operator new( std::size_t n ) {
    if( count_allocations ) { ++allocation_count; allocation_bytes += n; }
    if( void *p = std::malloc( n ? n : 1 ) ) { return p; }
    throw std::bad_alloc();
}
[[gnu::noinline]] void *operator new[]( std::size_t n ) {
    if( count_allocations ) { ++allocation_count; allocation_bytes += n; }
    if( void *p = std::malloc( n ? n : 1 ) ) { return p; }
    throw std::bad_alloc();
}
[[gnu::noinline]] void operator delete( void *p ) noexcept { std::free( p ); }
[[gnu::noinline]] void operator delete[]( void *p ) noexcept { std::free( p ); }
[[gnu::noinline]] void operator delete( void *p, std::size_t ) noexcept { std::free( p ); }
[[gnu::noinline]] void operator delete[]( void *p, std::size_t ) noexcept { std::free( p ); }

template<class T> using shared_ptr_fast = std::shared_ptr<T>;
struct map {};
static map global_map;
map &get_map() { return global_map; }
struct tripoint_abs_ms {
    int x = 0, y = 0, z = 0;
    bool operator==( const tripoint_abs_ms &o ) const {
        return x == o.x && y == o.y && z == o.z;
    }
    std::string to_string_writable() const { return std::to_string(x) + "," + std::to_string(y) + "," + std::to_string(z); }
};
struct AbsHash {
    std::size_t operator()( const tripoint_abs_ms &p ) const {
        std::size_t h = std::hash<int>{}( p.x );
        h ^= std::hash<int>{}( p.y ) + 0x9e3779b9 + ( h << 6 ) + ( h >> 2 );
        h ^= std::hash<int>{}( p.z ) + 0x9e3779b9 + ( h << 6 ) + ( h >> 2 );
        return h;
    }
};
struct monster {
    int id = 0;
    tripoint_abs_ms position;
    bool dead = false;
    bool hallucination = false;
    bool is_dead() const { return dead; }
    bool is_hallucination() const { return hallucination; }
    tripoint_abs_ms pos_abs() const { return position; }
    const char *disp_name() const { return "fixture"; }
    void die( map *, void * ) { dead = true; }
};
template<class... T> void debugmsg( const char *, T... ) {}
namespace baseline {
static std::size_t linear_visits = 0;
template<class It, class Pred> It counted_find_if( It first, It last, Pred pred ) {
    for( ; first != last; ++first ) {
        ++linear_visits;
        if( pred( *first ) ) { break; }
    }
    return first;
}
class creature_tracker {
public:
    std::vector<shared_ptr_fast<monster>> monsters_list;
    std::unordered_map<tripoint_abs_ms, shared_ptr_fast<monster>, AbsHash> monsters_by_location;
    shared_ptr_fast<monster> find( const tripoint_abs_ms &p ) {
        const auto it = monsters_by_location.find( p );
        return it != monsters_by_location.end() && !it->second->is_dead() ? it->second : nullptr;
    }
    void remove_from_location_map( const monster &critter ) {
        const auto pos_iter = monsters_by_location.find( critter.pos_abs() );
        if( pos_iter != monsters_by_location.end() && pos_iter->second.get() == &critter ) {
            monsters_by_location.erase( pos_iter );
            return;
        }
        const auto it = std::find_if( monsters_by_location.begin(), monsters_by_location.end(),
        [&]( const auto &v ) { return v.second.get() == &critter; } );
        if( it != monsters_by_location.end() ) { monsters_by_location.erase( it ); }
    }
    void rebuild_cache() {
        monsters_by_location.clear();
        for( const auto &ptr : monsters_list ) { monsters_by_location[ptr->pos_abs()] = ptr; }
    }
    void add( const shared_ptr_fast<monster> &ptr ) {
        monsters_list.push_back( ptr );
        monsters_by_location[ptr->pos_abs()] = ptr;
    }
    __UPDATE_FUNCTION__
};
}
namespace optimized {
static std::size_t linear_visits = 0;
template<class It, class Pred> It counted_find_if( It first, It last, Pred pred ) {
    for( ; first != last; ++first ) {
        ++linear_visits;
        if( pred( *first ) ) { break; }
    }
    return first;
}
class creature_tracker {
public:
    std::vector<shared_ptr_fast<monster>> monsters_list;
    std::unordered_map<tripoint_abs_ms, shared_ptr_fast<monster>, AbsHash> monsters_by_location;
    shared_ptr_fast<monster> find( const tripoint_abs_ms &p ) {
        const auto it = monsters_by_location.find( p );
        return it != monsters_by_location.end() && !it->second->is_dead() ? it->second : nullptr;
    }
    void remove_from_location_map( const monster &critter ) {
        const auto pos_iter = monsters_by_location.find( critter.pos_abs() );
        if( pos_iter != monsters_by_location.end() && pos_iter->second.get() == &critter ) {
            monsters_by_location.erase( pos_iter );
            return;
        }
        const auto it = std::find_if( monsters_by_location.begin(), monsters_by_location.end(),
        [&]( const auto &v ) { return v.second.get() == &critter; } );
        if( it != monsters_by_location.end() ) { monsters_by_location.erase( it ); }
    }
    void rebuild_cache() {
        monsters_by_location.clear();
        for( const auto &ptr : monsters_list ) { monsters_by_location[ptr->pos_abs()] = ptr; }
    }
    void add( const shared_ptr_fast<monster> &ptr ) {
        monsters_list.push_back( ptr );
        monsters_by_location[ptr->pos_abs()] = ptr;
    }
    __UPDATE_FUNCTION__
};
}

struct tripoint {
    int x = 0, y = 0, z = 0;
    tripoint() = default;
    tripoint( int px, int py, int pz ) : x(px), y(py), z(pz) {}
    bool operator<( const tripoint &o ) const { return std::tie(x,y,z) < std::tie(o.x,o.y,o.z); }
    bool operator==( const tripoint &o ) const { return x == o.x && y == o.y && z == o.z; }
};
struct tripoint_bub_ms {
    int px = 0, py = 0, pz = 0;
    tripoint_bub_ms() = default;
    tripoint_bub_ms( int x, int y, int z ) : px(x), py(y), pz(z) {}
    int x() const { return px; } int y() const { return py; } int z() const { return pz; }
};
constexpr int SEEX = 12;
constexpr int SEEY = 12;
struct vehicle {
    tripoint_bub_ms position;
    tripoint_bub_ms pos_bub( const map & ) const { return position; }
};
namespace vehicle_baseline {
template<typename VehicleRange, typename MapRef, typename ProcessFn>
static void for_each_unique_vehicle_submap( const VehicleRange &vehicles, const MapRef &here,
        ProcessFn process )
{
    std::set<tripoint> positions;
    for( vehicle *this_vehicle : vehicles ) {
        const tripoint_bub_ms pos = this_vehicle->pos_bub( here );
        positions.emplace( pos.x() / SEEX, pos.y() / SEEY, pos.z() );
    }
    for( const tripoint &pos : positions ) { process( pos ); }
}
}
namespace vehicle_optimized {
__VEHICLE_HELPER__
}
template<class Tracker> void add_monsters( Tracker &tracker, int count ) {
    for( int i = 0; i < count; ++i ) {
        auto ptr = std::make_shared<monster>();
        ptr->id = i;
        ptr->position = tripoint_abs_ms{ 0, i * 3 + 1, 0 };
        tracker.add( ptr );
    }
}
template<class Tracker> void move_pass( Tracker &tracker ) {
    for( const auto &ptr : tracker.monsters_list ) {
        const tripoint_abs_ms old_pos = ptr->pos_abs();
        const tripoint_abs_ms new_pos{ old_pos.x + 1, old_pos.y, old_pos.z };
        ptr->position = new_pos;
        assert( tracker.update_pos( *ptr, old_pos, new_pos ) );
    }
}
template<class A, class B> void compare_trackers( const A &a, const B &b ) {
    assert( a.monsters_list.size() == b.monsters_list.size() );
    assert( a.monsters_by_location.size() == b.monsters_by_location.size() );
    for( std::size_t i = 0; i < a.monsters_list.size(); ++i ) {
        assert( a.monsters_list[i]->id == b.monsters_list[i]->id );
        assert( a.monsters_list[i]->pos_abs() == b.monsters_list[i]->pos_abs() );
    }
    for( const auto &entry : a.monsters_by_location ) {
        const auto it = b.monsters_by_location.find( entry.first );
        assert( it != b.monsters_by_location.end() );
        assert( it->second->id == entry.second->id );
    }
}
static bool same_points( const std::vector<tripoint> &a, const std::vector<tripoint> &b ) {
    return a == b;
}
static void benchmark_vehicle_groups( const std::size_t count, const bool many_z_levels = false ) {
    std::vector<vehicle> storage( count );
    std::vector<vehicle *> vehicles;
    vehicles.reserve( count );
    for( std::size_t i = 0; i < count; ++i ) { vehicles.push_back( &storage[i] ); }
    std::vector<tripoint> old_result, new_result;
    old_result.reserve( count ); new_result.reserve( count );
    std::size_t old_allocs = 0, new_allocs = 0, old_bytes = 0, new_bytes = 0;
    double old_ms = 0, new_ms = 0;
    constexpr int repetitions = 25;
    for( int rep = 0; rep < repetitions; ++rep ) {
        for( std::size_t i = 0; i < count; ++i ) {
            // A normal per-level map has at most 121 submaps. A separate
            // synthetic multi-z case exercises the defensive overflow path.
            const int submap_x = static_cast<int>( i % 11 );
            const int submap_y = static_cast<int>( ( i / 11 ) % 11 );
            const int z = many_z_levels ?
                          static_cast<int>( i / 121 ) - static_cast<int>( count / 242 ) : 0;
            storage[i].position = tripoint_bub_ms(
                submap_x * SEEX + rep % SEEX,
                submap_y * SEEY + ( rep * 7 ) % SEEY,
                z );
        }
        old_result.clear();
        allocation_count = allocation_bytes = 0;
        count_allocations = true;
        auto start = std::chrono::steady_clock::now();
        vehicle_baseline::for_each_unique_vehicle_submap( vehicles, global_map,
            [&]( const tripoint &p ) { old_result.push_back( p ); } );
        auto finish = std::chrono::steady_clock::now();
        count_allocations = false;
        old_allocs += allocation_count; old_bytes += allocation_bytes;
        old_ms += std::chrono::duration<double, std::milli>( finish - start ).count();

        new_result.clear();
        allocation_count = allocation_bytes = 0;
        count_allocations = true;
        start = std::chrono::steady_clock::now();
        vehicle_optimized::for_each_unique_vehicle_submap( vehicles, global_map,
            [&]( const tripoint &p ) { new_result.push_back( p ); } );
        finish = std::chrono::steady_clock::now();
        count_allocations = false;
        new_allocs += allocation_count; new_bytes += allocation_bytes;
        new_ms += std::chrono::duration<double, std::milli>( finish - start ).count();
        assert( same_points( old_result, new_result ) );
    }
    if( count <= 32 ) {
        assert( new_allocs == 0 );
        if( count > 0 ) { assert( old_allocs > 0 ); }
    } else if( many_z_levels ) {
        // More than the bounded unique slots falls back to one reserved vector.
        assert( new_allocs == repetitions );
        assert( new_allocs < old_allocs );
        assert( new_bytes < old_bytes );
    } else {
        // Normal single-level maps deduplicate in stack storage only.
        assert( new_allocs == 0 );
        assert( new_bytes == 0 );
        assert( old_allocs > 0 );
        assert( old_bytes > 0 );
    }
    std::printf( "vehicle-submaps n=%zu x%d: allocations %zu -> %zu, bytes %zu -> %zu, time %.3f -> %.3f ms\n",
                 count, repetitions, old_allocs, new_allocs, old_bytes, new_bytes, old_ms, new_ms );

    // Dense cluster: all vehicles can resolve to one submap. The output must
    // still deduplicate to one coordinate with the same visitation order.
    for( vehicle &v : storage ) {
        v.position = tripoint_bub_ms( 1, 1, 0 );
    }
    old_result.clear();
    new_result.clear();
    vehicle_baseline::for_each_unique_vehicle_submap( vehicles, global_map,
        [&]( const tripoint &p ) { old_result.push_back( p ); } );
    vehicle_optimized::for_each_unique_vehicle_submap( vehicles, global_map,
        [&]( const tripoint &p ) { new_result.push_back( p ); } );
    assert( same_points( old_result, new_result ) );
    if( count > 0 ) {
        assert( old_result.size() == 1 );
    }
    allocation_count = allocation_bytes = 0;
    count_allocations = true;
    vehicle_optimized::for_each_unique_vehicle_submap( vehicles, global_map,
        [&]( const tripoint & ) {} );
    count_allocations = false;
    assert( allocation_count == 0 && allocation_bytes == 0 );
}
int main() {
    for( int count : { 32, 256, 1024, 4096 } ) {
        baseline::creature_tracker before;
        optimized::creature_tracker after;
        add_monsters( before, count );
        add_monsters( after, count );
        baseline::linear_visits = optimized::linear_visits = 0;
        const auto start_old = std::chrono::steady_clock::now();
        for( int turn = 0; turn < 3; ++turn ) { move_pass( before ); }
        const auto end_old = std::chrono::steady_clock::now();
        const auto start_new = std::chrono::steady_clock::now();
        for( int turn = 0; turn < 3; ++turn ) { move_pass( after ); }
        const auto end_new = std::chrono::steady_clock::now();
        compare_trackers( before, after );
        const std::size_t expected_old = static_cast<std::size_t>( 3 ) * count * ( count + 1 ) / 2;
        assert( baseline::linear_visits == expected_old );
        assert( optimized::linear_visits == 0 );
        const double old_ms = std::chrono::duration<double, std::milli>( end_old - start_old ).count();
        const double new_ms = std::chrono::duration<double, std::milli>( end_new - start_new ).count();
        std::printf( "monster-moves n=%d x3 passes: linear visits %zu -> %zu, time %.3f -> %.3f ms; checksum=%zu\n",
                     count, baseline::linear_visits, optimized::linear_visits,
                     old_ms, new_ms, after.monsters_by_location.size() );
    }
    {
        baseline::creature_tracker before;
        optimized::creature_tracker after;
        add_monsters( before, 96 ); add_monsters( after, 96 );
        const auto old_pos = before.monsters_list[41]->pos_abs();
        const tripoint_abs_ms next{ old_pos.x + 1, old_pos.y, old_pos.z };
        before.monsters_by_location.erase( old_pos );
        after.monsters_by_location.erase( old_pos );
        before.monsters_list[41]->position = next;
        after.monsters_list[41]->position = next;
        baseline::linear_visits = optimized::linear_visits = 0;
        assert( before.update_pos( *before.monsters_list[41], old_pos, next ) );
        assert( after.update_pos( *after.monsters_list[41], old_pos, next ) );
        compare_trackers( before, after );
        assert( baseline::linear_visits > 0 && optimized::linear_visits > 0 );
        std::puts( "PASS stale/missing location-index fallback retains the upstream list search" );
    }
    for( std::size_t count : { 0U, 1U, 4U, 16U, 32U, 33U, 128U, 1024U } ) {
        benchmark_vehicle_groups( count );
    }
    benchmark_vehicle_groups( 300, true );
    std::puts( "PASS large entity movement and dynamic vehicle-submap grouping differential checks" );
}
'''

program = common.replace("__UPDATE_FUNCTION__", old_update_cxx, 1)
# Replace the second placeholder independently so the baseline and optimized
# tracker compile the corresponding real function implementations.
marker = "namespace optimized {"
idx = program.index(marker)
before_optimized = program[:idx]
after_optimized = program[idx:]
assert "__UPDATE_FUNCTION__" in after_optimized
after_optimized = after_optimized.replace("__UPDATE_FUNCTION__", new_update_cxx, 1)
program = before_optimized + after_optimized
program = program.replace("__VEHICLE_HELPER__", helper_body, 1)

with tempfile.TemporaryDirectory(prefix="large-entity-churn-", dir=out) as tmp:
    cpp = Path(tmp) / "test.cpp"
    binary = Path(tmp) / "test"
    cpp.write_text(program)
    subprocess.run(
        ["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
         str(cpp), "-o", str(binary)],
        check=True, timeout=90)
    subprocess.run([str(binary)], check=True, timeout=90)

print("PASS large-entity churn source guard, differential movement test, and vehicle-submap allocation benchmark")
