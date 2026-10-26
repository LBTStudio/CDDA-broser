#!/usr/bin/env python3
"""Check and benchmark reserve() for the per-movement vehicle collision vector."""
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(sys.argv[1]).resolve()
new_map = (root / "src/map.cpp").read_text(encoding="utf-8")
old_map = subprocess.check_output(
    ["git", "-C", str(root), "show", "HEAD:src/map.cpp"], text=True)

signature = "vehicle *map::move_vehicle( vehicle &veh, const tripoint_rel_ms &dp, const tileray &facing )"


def function(source, sig, next_sig):
    start = source.index(sig)
    end = source.index(next_sig, start)
    body = source[start:end]
    opening = body.index("\n{\n") + 1
    depth, pos = 1, opening + 1
    while depth:
        depth += (body[pos] == "{") - (body[pos] == "}")
        pos += 1
    return body[:pos]


old_fn = function(old_map, signature, "float map::vehicle_vehicle_collision(")
new_fn = function(new_map, signature, "float map::vehicle_vehicle_collision(")
old_line = "    std::vector<veh_collision> collisions;\n\n    // Find collisions"
new_line = """    std::vector<veh_collision> collisions;
    // A single movement can inspect many vehicle parts and may repeat collision
    // detection after an impact. Reserve once, then reuse capacity across retries.
    collisions.reserve( static_cast<std::size_t>( veh.part_count() ) );

    // Find collisions"""
assert old_line in old_fn, "upstream collision buffer declaration changed"
assert new_line in new_fn, "reserved collision buffer missing"
assert new_fn.replace(new_line, old_line, 1) == old_fn, (
    "move_vehicle changed beyond reserving the collision vector")
assert new_fn.count("collisions.reserve( static_cast<std::size_t>( veh.part_count() ) );") == 1
print("PASS: move_vehicle source guard; collision algorithm and ordering unchanged")

fixture = r'''
#include <cassert>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <new>
#include <vector>

struct Collision { unsigned char data[32]; };
static bool measuring = false;
static std::size_t allocation_count = 0;
static std::size_t allocated_bytes = 0;

void *operator new( std::size_t n ) {
    if( measuring ) { ++allocation_count; allocated_bytes += n; }
    if( void *p = std::malloc( n ? n : 1 ) ) { return p; }
    throw std::bad_alloc();
}
void *operator new[]( std::size_t n ) {
    if( measuring ) { ++allocation_count; allocated_bytes += n; }
    if( void *p = std::malloc( n ? n : 1 ) ) { return p; }
    throw std::bad_alloc();
}
void operator delete( void *p ) noexcept { std::free( p ); }
void operator delete[]( void *p ) noexcept { std::free( p ); }
void operator delete( void *p, std::size_t ) noexcept { std::free( p ); }
void operator delete[]( void *p, std::size_t ) noexcept { std::free( p ); }

static void run_case( std::size_t part_count )
{
    std::size_t baseline_allocs = 0, reserved_allocs = 0;
    std::size_t baseline_bytes = 0, reserved_bytes = 0;
    std::vector<Collision> baseline, reserved;
    allocation_count = allocated_bytes = 0;
    measuring = true;
    for( std::size_t i = 0; i < part_count; ++i ) baseline.push_back( Collision{} );
    measuring = false;
    baseline_allocs = allocation_count;
    baseline_bytes = allocated_bytes;

    allocation_count = allocated_bytes = 0;
    measuring = true;
    reserved.reserve( part_count );
    for( std::size_t i = 0; i < part_count; ++i ) reserved.push_back( Collision{} );
    measuring = false;
    reserved_allocs = allocation_count;
    reserved_bytes = allocated_bytes;

    assert( baseline.size() == reserved.size() );
    assert( reserved_allocs <= baseline_allocs );
    assert( reserved_bytes <= baseline_bytes || part_count == 0 );
    if( part_count >= 8 ) assert( reserved_allocs < baseline_allocs );
    std::printf("collision-candidates n=%zu: allocations %zu -> %zu, bytes %zu -> %zu\n",
        part_count, baseline_allocs, reserved_allocs, baseline_bytes, reserved_bytes);
}

int main()
{
    for( std::size_t n : { 0, 1, 4, 8, 16, 32, 64, 128, 256, 512 } ) run_case( n );
    std::puts("PASS: reserved collision vector avoids repeated capacity growth");
}
'''
with tempfile.TemporaryDirectory(prefix="cdda-vehicle-collision-") as temp:
    temp = Path(temp)
    cpp, binary = temp / "collision.cpp", temp / "collision"
    cpp.write_text(fixture, encoding="utf-8")
    subprocess.run([
        "g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
        "-Wno-mismatched-new-delete", str(cpp), "-o", str(binary)
    ], check=True, timeout=90)
    subprocess.run([str(binary)], check=True, timeout=90)
print("PASS: collision vector allocation benchmark")
