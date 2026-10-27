#!/usr/bin/env python3
"""Differential and allocation regression test for process_items vehicle grouping.

The fixture compares std::set<tripoint>'s x/y/z order against vector sort/unique.
Reported timings and allocations are for this grouping fixture only, not a
full CDDA turn or a physical-device benchmark.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).resolve()
root = Path(__file__).resolve().parent.parent
transform = root / "ci" / "optimize_vehicle_item_submap_grouping.py"


def upstream(name):
    return subprocess.check_output(
        ["git", "-C", str(source), "show", "HEAD:src/" + name], text=True
    )


def function(text, signature, next_signature):
    start = text.index(signature)
    end = text.index(next_signature, start)
    block = text[start:end]
    opening = block.index("{")
    depth, pos = 1, opening + 1
    while depth:
        depth += (block[pos] == "{") - (block[pos] == "}")
        pos += 1
    return block[:pos]


def tokens(text):
    text = re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"\s+", "", text)


signature = "void map::process_items()"
next_signature = "void map::process_items_in_submap"
original_source = upstream("map.cpp")
patched_source = (source / "src" / "map.cpp").read_text(encoding="utf-8")
original = function(original_source, signature, next_signature)
optimized = function(patched_source, signature, next_signature)

old_block = """        std::set<tripoint> submaps_with_vehicles;
        for( vehicle *this_vehicle : cache.vehicle_list ) {
            tripoint_bub_ms pos = this_vehicle->pos_bub( *this );
            submaps_with_vehicles.emplace( pos.x() / SEEX, pos.y() / SEEY, pos.z() );
        }
        for( const tripoint &pos : submaps_with_vehicles ) {"""
new_block = """        // Keep std::set's x/y/z traversal order and de-duplication while
        // avoiding a tree-node allocation for every distinct vehicle submap.
        std::vector<tripoint> submaps_with_vehicles;
        submaps_with_vehicles.reserve( cache.vehicle_list.size() );
        for( vehicle *this_vehicle : cache.vehicle_list ) {
            tripoint_bub_ms pos = this_vehicle->pos_bub( *this );
            submaps_with_vehicles.emplace_back( pos.x() / SEEX, pos.y() / SEEY, pos.z() );
        }
        std::sort( submaps_with_vehicles.begin(), submaps_with_vehicles.end() );
        submaps_with_vehicles.erase(
            std::unique( submaps_with_vehicles.begin(), submaps_with_vehicles.end() ),
            submaps_with_vehicles.end() );
        for( const tripoint &pos : submaps_with_vehicles ) {"""
assert old_block in original, "upstream vehicle item-submap grouping changed"
assert new_block in optimized, "vector/sort/unique grouping block missing"
assert optimized.count(new_block) == 1, "vehicle grouping transform must be scoped once"
assert "process_items_in_vehicles( *current_submap );" in optimized
assert "update_submaps_with_active_items();" in optimized
assert "process_items_in_submap( *current_submap, local_pos );" in optimized
assert tokens(optimized.replace(new_block, old_block, 1)) == tokens(original), (
    "process_items changed outside the vehicle-submap grouping transform"
)

# Verify the actual script recreates the checked-in transformation from pristine
# upstream source, rather than relying only on static expected-string checks.
with tempfile.TemporaryDirectory(prefix="cdda-vehicle-item-grouping-") as tmp:
    temp_map = Path(tmp) / "map.cpp"
    temp_map.write_text(original_source, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(transform), str(temp_map)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
    )
    assert result.returncode == 0, result.stdout
    simulated = function(temp_map.read_text(encoding="utf-8"), signature, next_signature)
    assert simulated == optimized, "production transform output differs from checked-in source"

program = r'''
#include <algorithm>
#include <cassert>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <new>
#include <set>
#include <tuple>
#include <vector>

static std::size_t alloc_count = 0;
static std::size_t alloc_bytes = 0;
static bool count_allocations = false;

void *operator new( std::size_t n ) {
    if( void *p = std::malloc( n ) ) {
        if( count_allocations ) {
            ++alloc_count;
            alloc_bytes += n;
        }
        return p;
    }
    throw std::bad_alloc();
}
void operator delete( void *p ) noexcept { std::free( p ); }
void operator delete( void *p, std::size_t ) noexcept { std::free( p ); }
void *operator new[]( std::size_t n ) {
    if( void *p = std::malloc( n ) ) {
        if( count_allocations ) {
            ++alloc_count;
            alloc_bytes += n;
        }
        return p;
    }
    throw std::bad_alloc();
}
void operator delete[]( void *p ) noexcept { std::free( p ); }
void operator delete[]( void *p, std::size_t ) noexcept { std::free( p ); }

struct tripoint {
    int x, y, z;
    tripoint( int x_in, int y_in, int z_in ) : x( x_in ), y( y_in ), z( z_in ) {}
    bool operator<( const tripoint &rhs ) const {
        return std::tie( x, y, z ) < std::tie( rhs.x, rhs.y, rhs.z );
    }
    bool operator==( const tripoint &rhs ) const {
        return x == rhs.x && y == rhs.y && z == rhs.z;
    }
};
struct stats {
    std::size_t allocations = 0, bytes = 0;
    std::uint64_t checksum = 0;
    double ms = 0;
};
static std::uint64_t hash_point( std::uint64_t h, const tripoint &p ) {
    h = ( h ^ static_cast<std::uint32_t>( p.x ) ) * 1099511628211ULL;
    h = ( h ^ static_cast<std::uint32_t>( p.y ) ) * 1099511628211ULL;
    h = ( h ^ static_cast<std::uint32_t>( p.z ) ) * 1099511628211ULL;
    return h;
}
static stats run_baseline( const std::vector<tripoint> &input, int repeats ) {
    stats result;
    const auto start = std::chrono::steady_clock::now();
    alloc_count = alloc_bytes = 0;
    count_allocations = true;
    for( int r = 0; r < repeats; ++r ) {
        std::set<tripoint> grouped;
        for( const tripoint &p : input ) grouped.emplace( p );
        result.checksum = 1469598103934665603ULL;
        for( const tripoint &p : grouped ) result.checksum = hash_point( result.checksum, p );
    }
    count_allocations = false;
    result.ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start ).count();
    result.allocations = alloc_count;
    result.bytes = alloc_bytes;
    return result;
}
static stats run_optimized( const std::vector<tripoint> &input, int repeats ) {
    stats result;
    const auto start = std::chrono::steady_clock::now();
    alloc_count = alloc_bytes = 0;
    count_allocations = true;
    for( int r = 0; r < repeats; ++r ) {
        std::vector<tripoint> grouped;
        grouped.reserve( input.size() );
        for( const tripoint &p : input ) grouped.emplace_back( p );
        std::sort( grouped.begin(), grouped.end() );
        grouped.erase( std::unique( grouped.begin(), grouped.end() ), grouped.end() );
        result.checksum = 1469598103934665603ULL;
        for( const tripoint &p : grouped ) result.checksum = hash_point( result.checksum, p );
    }
    count_allocations = false;
    result.ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start ).count();
    result.allocations = alloc_count;
    result.bytes = alloc_bytes;
    return result;
}

static void exercise( const char *label, const std::vector<tripoint> &input ) {
    constexpr int repeats = 25;
    const stats old_stats = run_baseline( input, repeats );
    const stats new_stats = run_optimized( input, repeats );
    assert( old_stats.checksum == new_stats.checksum );
    assert( new_stats.allocations <= old_stats.allocations );
    // Byte counts are diagnostic: a duplicate-heavy set may hold fewer unique nodes than raw vector keys.
    std::printf(
        "MEASURE %s n=%zu x%d: allocations %zu->%zu bytes %zu->%zu time %.3f->%.3f ms checksum=%llu\n",
        label, input.size(), repeats,
        old_stats.allocations, new_stats.allocations,
        old_stats.bytes, new_stats.bytes, old_stats.ms, new_stats.ms,
        static_cast<unsigned long long>( new_stats.checksum ) );
}
int main() {
    for( std::size_t n : { 0U, 1U, 16U, 128U, 512U, 1024U, 2048U } ) {
        std::vector<tripoint> unique;
        unique.reserve( n );
        for( std::size_t i = 0; i < n; ++i ) {
            unique.emplace_back( static_cast<int>( i % 36 ),
                                 static_cast<int>( ( i / 36 ) % 36 ), -2 );
        }
        exercise( "vehicle-submaps-mostly-unique", unique );

        std::vector<tripoint> duplicate_heavy;
        duplicate_heavy.reserve( n );
        for( std::size_t i = 0; i < n; ++i ) {
            duplicate_heavy.emplace_back( static_cast<int>( i % 12 ),
                                          static_cast<int>( ( i * 7 ) % 9 ),
                                          static_cast<int>( i % 5 ) - 2 );
        }
        exercise( "vehicle-submaps-duplicate-heavy", duplicate_heavy );
    }
    std::puts( "PASS vehicle item submaps: identical sorted unique order, allocation fixture, and checksum" );
}
'''

with tempfile.TemporaryDirectory(prefix="cdda-vehicle-item-fixture-") as tmp:
    tmpdir = Path(tmp)
    cpp = tmpdir / "vehicle_item_submap_fixture.cpp"
    exe = tmpdir / "fixture"
    cpp.write_text(program, encoding="utf-8")
    compile_result = subprocess.run(
        ["g++", "-std=c++17", "-O2", str(cpp), "-o", str(exe)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
    )
    assert compile_result.returncode == 0, compile_result.stdout
    result = subprocess.run([str(exe)], text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, check=False)
    assert result.returncode == 0, result.stdout
    print(result.stdout, end="")

print("PASS vehicle item-submap source contract and exact std::set-equivalent order")
