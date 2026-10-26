#!/usr/bin/env python3
"""Check and benchmark lazy, bounded collision candidate capacity reservation."""
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(sys.argv[1]).resolve()
new_move = (root / "src/vehicle_move.cpp").read_text(encoding="utf-8")
old_move = subprocess.check_output(
    ["git", "-C", str(root), "show", "HEAD:src/vehicle_move.cpp"], text=True)

signature = """bool vehicle::collision( map &here, std::vector<veh_collision> &colls,
                         const tripoint_rel_ms &dp,"""


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


old_fn = function(old_move, signature, "veh_collision vehicle::part_collision(")
new_fn = function(new_move, signature, "veh_collision vehicle::part_collision(")
rotor_old = """                    } else {
                        colls.push_back( rotor_coll );
                    }"""
rotor_new = """                    } else {
                        if( colls.empty() ) {
                            colls.reserve( std::min<std::size_t>(
                                               static_cast<std::size_t>( part_count() ), 4 ) );
                        }
                        colls.push_back( rotor_coll );
                    }"""
main_old = """        colls.push_back( coll );

        if( just_detect ) {"""
main_new = """        if( !just_detect && colls.empty() ) {
            // Reserve only after a real collision is found. Clear-road driving
            // keeps the upstream zero-allocation fast path.
            colls.reserve( std::min<std::size_t>(
                               static_cast<std::size_t>( part_count() ), 4 ) );
        }
        colls.push_back( coll );

        if( just_detect ) {"""
assert rotor_old in old_fn and rotor_new in new_fn, "rotor collision anchor changed"
assert main_old in old_fn and main_new in new_fn, "main collision anchor changed"
normalized = new_fn.replace(rotor_new, rotor_old, 1).replace(main_new, main_old, 1)
assert normalized == old_fn, "vehicle::collision changed outside lazy capacity reservation"
assert "if( !just_detect && colls.empty() ) {" in new_fn
assert new_fn.count("colls.reserve( std::min<std::size_t>(") == 2
assert "colls.reserve( static_cast<std::size_t>( veh.part_count() ) );" not in (
    root / "src/map.cpp").read_text(encoding="utf-8")
print("PASS: lazy collision reserve source guard; collision checks/order unchanged")

fixture = r'''
#include <algorithm>
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
    if( void *p = std::malloc( n ? n : 1 ) ) return p;
    throw std::bad_alloc();
}
void *operator new[]( std::size_t n ) {
    if( measuring ) { ++allocation_count; allocated_bytes += n; }
    if( void *p = std::malloc( n ? n : 1 ) ) return p;
    throw std::bad_alloc();
}
void operator delete( void *p ) noexcept { std::free( p ); }
void operator delete[]( void *p ) noexcept { std::free( p ); }
void operator delete( void *p, std::size_t ) noexcept { std::free( p ); }
void operator delete[]( void *p, std::size_t ) noexcept { std::free( p ); }

static void run_case( std::size_t parts, std::size_t hits )
{
    assert( hits <= parts );
    std::vector<Collision> baseline, optimized;
    std::size_t base_allocs, opt_allocs, base_bytes, opt_bytes;

    allocation_count = allocated_bytes = 0;
    measuring = true;
    for( std::size_t i = 0; i < hits; ++i ) baseline.push_back( Collision{} );
    measuring = false;
    base_allocs = allocation_count;
    base_bytes = allocated_bytes;

    allocation_count = allocated_bytes = 0;
    measuring = true;
    for( std::size_t i = 0; i < hits; ++i ) {
        // Mirrors the production rule: just_detect does not reserve, and
        // the vector is reserved only on the first actual collision.
        if( optimized.empty() ) {
            optimized.reserve( std::min<std::size_t>( parts, 4 ) );
        }
        optimized.push_back( Collision{} );
    }
    measuring = false;
    opt_allocs = allocation_count;
    opt_bytes = allocated_bytes;

    assert( baseline.size() == optimized.size() );
    assert( opt_allocs <= base_allocs );
    if( hits == 0 ) assert( opt_allocs == 0 && opt_bytes == 0 );
    if( hits >= 8 ) assert( opt_allocs < base_allocs );
    std::printf("collision-buffer parts=%zu hits=%zu: allocs %zu->%zu, bytes %zu->%zu\n",
        parts, hits, base_allocs, opt_allocs, base_bytes, opt_bytes);
}
int main()
{
    run_case(0, 0);
    run_case(1, 0); run_case(1, 1);
    run_case(8, 0); run_case(8, 1); run_case(8, 8);
    run_case(16, 16); run_case(32, 32);
    run_case(128, 1); run_case(128, 8); run_case(128, 16); run_case(128, 32);
    run_case(512, 512);
    std::puts("PASS: no-hit path stays allocation-free; multi-hit capacity growth is reduced");
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
print("PASS: lazy vehicle collision buffer benchmark")
