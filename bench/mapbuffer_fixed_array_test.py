#!/usr/bin/env python3
"""Source-preserving regression and allocation benchmark for mapbuffer::save_quad.

The optimized CDDA checkout is supplied as argv[1]. Its HEAD must be pristine
upstream CDDA 0.I, while the runtime patches have already been applied.
This checks that serialization/deletion logic is byte-for-byte unchanged, then
compares the fixed-four-element setup from the source patch with the old vectors.
The microbenchmark measures setup allocations, not full-game save wall time.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
import shlex

source = Path(sys.argv[1]).resolve()
bench_dir = Path(__file__).resolve().parent
out = bench_dir / "out" / "mapbuffer-fixed-arrays"
out.mkdir(parents=True, exist_ok=True)

original = subprocess.check_output(
    ["git", "-C", str(source), "show", "HEAD:src/mapbuffer.cpp"], text=True)
patched = (source / "src" / "mapbuffer.cpp").read_text()


def function_body(text, signature):
    start = text.index(signature)
    opening = text.index("{", start)
    depth, pos = 1, opening + 1
    while depth:
        depth += (text[pos] == "{") - (text[pos] == "}")
        pos += 1
    return text[start:pos]


old_quad = function_body(original, "void mapbuffer::save_quad(")
new_quad = function_body(patched, "void mapbuffer::save_quad(")
old_setup = """    std::vector<point_rel_sm> offsets;
    std::vector<tripoint_abs_sm> submap_addrs;
    offsets.reserve( 4 );
    submap_addrs.reserve( 4 );
    offsets.push_back( point_rel_sm::zero );
    offsets.push_back( point_rel_sm::south );
    offsets.push_back( point_rel_sm::east );
    offsets.push_back( point_rel_sm::south_east );
"""
new_setup = """    const std::array<point_rel_sm, 4> offsets = {
        point_rel_sm::zero, point_rel_sm::south, point_rel_sm::east, point_rel_sm::south_east
    };
    const tripoint_abs_sm base_addr = project_to<coords::sm>( om_addr );
    std::array<tripoint_abs_sm, 4> submap_addrs = {
        base_addr, base_addr, base_addr, base_addr
    };
    std::size_t submap_index = 0;
"""
old_loop = """    for( point_rel_sm &offsets_offset : offsets ) {
        tripoint_abs_sm submap_addr = project_to<coords::sm>( om_addr );
        submap_addr += offsets_offset.raw(); // TODO: Make += etc. available to relative parameters as well.
        submap_addrs.push_back( submap_addr );
"""
new_loop = """    for( const point_rel_sm &offsets_offset : offsets ) {
        tripoint_abs_sm submap_addr = base_addr;
        submap_addr += offsets_offset.raw(); // TODO: Make += etc. available to relative parameters as well.
        submap_addrs[submap_index++] = submap_addr;
"""
assert old_setup in old_quad and old_loop in old_quad, "expected pristine 0.I save_quad setup"
assert new_setup in new_quad and new_loop in new_quad, "fixed-array save_quad setup missing"
normalized_quad = new_quad.replace(new_setup, old_setup, 1).replace(new_loop, old_loop, 1)
assert normalized_quad == old_quad, (
    "save_quad changed beyond fixed-size storage and hoisted invariant address calculation"
)
assert "#include <array>" in patched
assert "std::vector<point_rel_sm> offsets" not in new_quad

program = r'''
#include <array>
#include <cassert>
#include <chrono>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <new>
#include <vector>

static bool count_allocations = false;
static std::size_t allocations = 0;

[[gnu::noinline]] void *operator new(std::size_t size) {
    if (count_allocations) ++allocations;
    if (void *p = std::malloc(size ? size : 1)) return p;
    throw std::bad_alloc();
}
[[gnu::noinline]] void operator delete(void *p) noexcept { std::free(p); }
[[gnu::noinline]] void operator delete(void *p, std::size_t) noexcept { std::free(p); }

struct Offset {
    int x, y, z;
};
struct Coord {
    int x, y, z;
    Coord &operator+=(const Offset &offset) {
        x += offset.x; y += offset.y; z += offset.z; return *this;
    }
};

static constexpr Offset zero{0, 0, 0};
static constexpr Offset south{0, 1, 0};
static constexpr Offset east{1, 0, 0};
static constexpr Offset south_east{1, 1, 0};

static void vector_setup(int seed, unsigned long long &checksum) {
    std::vector<Offset> offsets;
    std::vector<Coord> submap_addrs;
    offsets.reserve(4);
    submap_addrs.reserve(4);
    offsets.push_back(zero);
    offsets.push_back(south);
    offsets.push_back(east);
    offsets.push_back(south_east);
    for (Offset &offset : offsets) {
        Coord address{seed, seed * 2, -seed};
        address += offset;
        submap_addrs.push_back(address);
    }
    for (const Coord &address : submap_addrs) {
        checksum = checksum * 33 + static_cast<unsigned>(address.x);
        checksum = checksum * 33 + static_cast<unsigned>(address.y);
        checksum = checksum * 33 + static_cast<unsigned>(address.z);
    }
}

static void array_setup(int seed, unsigned long long &checksum) {
    const std::array<Offset, 4> offsets = {zero, south, east, south_east};
    const Coord base_addr{seed, seed * 2, -seed};
    std::array<Coord, 4> submap_addrs = {
        base_addr, base_addr, base_addr, base_addr
    };
    std::size_t submap_index = 0;
    for (const Offset &offset : offsets) {
        Coord address = base_addr;
        address += offset;
        submap_addrs[submap_index++] = address;
    }
    for (const Coord &address : submap_addrs) {
        checksum = checksum * 33 + static_cast<unsigned>(address.x);
        checksum = checksum * 33 + static_cast<unsigned>(address.y);
        checksum = checksum * 33 + static_cast<unsigned>(address.z);
    }
}

int main() {
    constexpr int repeats = 10000;
    unsigned long long vector_sum = 0, array_sum = 0;
    allocations = 0; count_allocations = true;
    const auto vector_begin = std::chrono::steady_clock::now();
    for (int i = 0; i < repeats; ++i) vector_setup(i + 1, vector_sum);
    const auto vector_end = std::chrono::steady_clock::now();
    count_allocations = false;
    const std::size_t vector_allocations = allocations;

    allocations = 0; count_allocations = true;
    const auto array_begin = std::chrono::steady_clock::now();
    for (int i = 0; i < repeats; ++i) array_setup(i + 1, array_sum);
    const auto array_end = std::chrono::steady_clock::now();
    count_allocations = false;
    const std::size_t array_allocations = allocations;

    assert(vector_sum == array_sum);
    assert(vector_allocations == static_cast<std::size_t>(repeats) * 2);
    assert(array_allocations == 0);
    const auto vector_us = std::chrono::duration<double, std::micro>(vector_end - vector_begin).count();
    const auto array_us = std::chrono::duration<double, std::micro>(array_end - array_begin).count();
    std::printf("MEASURE mapbuffer setup: iterations=%d allocations=%zu->%zu us=%.1f->%.1f checksum=%llu\n",
                repeats, vector_allocations, array_allocations, vector_us, array_us, vector_sum);
    std::puts("PASS mapbuffer setup: same four-address order, two heap allocations per quad eliminated");
}
'''
with tempfile.TemporaryDirectory(prefix="mapbuffer-fixed-array-", dir=out) as tmp:
    cpp = Path(tmp) / "test.cpp"
    binary = Path(tmp) / "test"
    cpp.write_text(program)
    command = ["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", str(cpp), "-o", str(binary)]
    print("COMPILE " + shlex.join(command), flush=True)
    subprocess.run(command, check=True, timeout=60)
    subprocess.run([str(binary)], check=True, timeout=30)

print("PASS mapbuffer source preservation: save_quad serialization and deletion logic unchanged")
