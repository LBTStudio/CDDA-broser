#!/usr/bin/env python3
"""Differential test for the fire processor's eight-entry neighbor scratch buffer.

Extracts the actual neighbor-selection block from pristine CDDA 0.I and the
patched source, compiles both into a controlled C++ fixture, and compares
candidate order, RNG consumption, and allocation counts. Timings are subsystem
fixtures, not building-fire or Chromebook end-to-end measurements.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).resolve()
out = Path(__file__).resolve().parent / "out" / "fire-neighbor-buffer"
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


def neighbor_block(method: str) -> str:
    start = method.index("    auto neighs = here.get_neighbors( p );")
    end = method.index("    // If the flames are in a pit", start)
    return method[start:end]


upstream = original("map_field.cpp")
patched = (source / "src/map_field.cpp").read_text(encoding="utf-8")
old_method = function(upstream, "void field_processor_fd_fire(")
new_method = function(patched, "void field_processor_fd_fire(")

old_decl = """    std::vector<size_t> neighbour_vec;
    neighbour_vec.reserve( neighs.size() );"""
new_decl = """    // A fire can use at most eight neighbors. Keep this hot-path scratch
    // storage on the stack instead of allocating a vector for every fire tile.
    std::array<size_t, 8> neighbour_vec{};
    size_t neighbour_count = 0;"""
expected = old_method.replace(old_decl, new_decl, 1)
assert expected != old_method, "upstream neighbor vector declaration not found"
assert expected.count("neighbour_vec.push_back( i );") == 1
expected = expected.replace(
    "            neighbour_vec.push_back( i );",
    "            neighbour_vec[neighbour_count++] = i;", 1
)
assert expected.count("neighbour_vec.size()") == 8
assert expected.count("neighbour_vec.empty()") == 1
expected = expected.replace("neighbour_vec.size()", "neighbour_count")
expected = expected.replace("neighbour_vec.empty()", "neighbour_count == 0")
assert expected.count("neighbour_count") == 11
old_wind = """    auto maptiles = here.get_wind_blockers( winddirection, p );
    maptile remove_tile = std::get<0>( maptiles );
    maptile remove_tile2 = std::get<1>( maptiles );
    maptile remove_tile3 = std::get<2>( maptiles );"""
new_wind = """    const auto blocker_indices = wind_blocker_neighbor_indices( winddirection );
    const maptile &remove_tile = neighs[blocker_indices[0]].second;
    const maptile &remove_tile2 = neighs[blocker_indices[1]].second;
    const maptile &remove_tile3 = neighs[blocker_indices[2]].second;"""
assert expected.count(old_wind) == 1
expected = expected.replace(old_wind, new_wind, 1)
assert new_method == expected, (
    "field_processor_fd_fire changed beyond vector -> fixed-array transformation")
assert "neighbour_vec.reserve" not in new_method
assert "neighbour_vec.push_back" not in new_method
assert "neighbour_vec.size()" not in new_method
assert "neighbour_vec.empty()" not in new_method
assert "if( neighbour_count == 0 )" in new_method

old_block = neighbor_block(old_method)
new_block = neighbor_block(new_method)
assert old_block.count("neighbour_vec.size()") == 0
assert new_block.count("neighbour_count") == 2

program = r'''
#include <algorithm>
#include <array>
#include <cassert>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <new>
#include <tuple>
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

struct tripoint_bub_ms {
    int px = 0;
    int py = 0;
    tripoint_bub_ms() = default;
    tripoint_bub_ms( int x, int y ) : px( x ), py( y ) {}
    int x() const { return px; }
    int y() const { return py; }
};
struct maptile {
    tripoint_bub_ms location;
    maptile() = default;
    explicit maptile( tripoint_bub_ms p ) : location( p ) {}
    tripoint_bub_ms pos() const { return location; }
};
using neighbor_array = std::array<std::pair<tripoint_bub_ms, maptile>, 8>;
struct map {
    neighbor_array neighbors;
    int blockers[3] = { 0, 2, 6 };
    neighbor_array get_neighbors( const tripoint_bub_ms & ) const {
        return neighbors;
    }
    std::tuple<maptile, maptile, maptile> get_wind_blockers(
        const int &, const tripoint_bub_ms & ) const {
        return { neighbors[static_cast<size_t>( blockers[0] )].second,
                 neighbors[static_cast<size_t>( blockers[1] )].second,
                 neighbors[static_cast<size_t>( blockers[2] )].second };
    }
};
static map global_map;
static unsigned int random_state = 1;
static size_t random_calls = 0;
static size_t probability_calls = 0;
static size_t rng( int low, size_t high ) {
    ++random_calls;
    random_state = random_state * 1664525u + 1013904223u;
    return static_cast<size_t>( low ) + random_state %
           ( high - static_cast<size_t>( low ) + 1 );
}
static bool x_in_y( int x, int y ) {
    ++probability_calls;
    random_state = random_state * 1664525u + 1013904223u;
    return y > 0 && random_state % static_cast<unsigned int>( y ) <
           static_cast<unsigned int>( x );
}
static void configure_neighbors( int center_x, int center_y, int winddirection ) {
    static constexpr int dx[8] = { -1, 0, 1, -1, 1, -1, 0, 1 };
    static constexpr int dy[8] = { -1, -1, -1, 0, 0, 1, 1, 1 };
    for( int i = 0; i < 8; ++i ) {
        tripoint_bub_ms pos( center_x + dx[i], center_y + dy[i] );
        global_map.neighbors[static_cast<size_t>( i )] = { pos, maptile( pos ) };
    }
    global_map.blockers[0] = winddirection % 8;
    global_map.blockers[1] = ( winddirection + 2 ) % 8;
    global_map.blockers[2] = ( winddirection + 5 ) % 8;
}
''';

program += "\nstatic void build_baseline( const tripoint_bub_ms &p, int winddirection, int windpower, std::vector<size_t> &out ) {\n";
program += "    map &here = global_map;\n" + old_block;
program += "    out.assign( neighbour_vec.begin(), neighbour_vec.end() );\n}\n";
program += "\nstatic void build_optimized( const tripoint_bub_ms &p, int winddirection, int windpower, std::vector<size_t> &out ) {\n";
program += "    map &here = global_map;\n" + new_block;
program += "    out.assign( neighbour_vec.begin(), neighbour_vec.begin() + neighbour_count );\n}\n";

program += r'''
struct rng_state_snapshot {
    unsigned int random;
    size_t random_count;
    size_t probability_count;
};
static rng_state_snapshot save_rng() {
    return { random_state, random_calls, probability_calls };
}
int main() {
    std::vector<size_t> before;
    std::vector<size_t> after;
    before.reserve( 8 );
    after.reserve( 8 );
    unsigned int scenario = 0xC0FFEEu;
    for( int trial = 0; trial < 2000; ++trial ) {
        scenario = scenario * 1664525u + 1013904223u;
        const int winddirection = static_cast<int>( scenario % 8 );
        scenario = scenario * 1664525u + 1013904223u;
        const int windpower = static_cast<int>( scenario % 51 );
        scenario = scenario * 1664525u + 1013904223u;
        const int cx = static_cast<int>( scenario % 1000 );
        scenario = scenario * 1664525u + 1013904223u;
        const int cy = static_cast<int>( scenario % 1000 );
        configure_neighbors( cx, cy, winddirection );
        const tripoint_bub_ms p( cx, cy );

        random_state = 17u + static_cast<unsigned int>( trial );
        random_calls = probability_calls = 0;
        before.clear();
        build_baseline( p, winddirection, windpower, before );
        const auto old_rng = save_rng();

        random_state = 17u + static_cast<unsigned int>( trial );
        random_calls = probability_calls = 0;
        after.clear();
        build_optimized( p, winddirection, windpower, after );
        const auto new_rng = save_rng();

        assert( before == after );
        assert( old_rng.random == new_rng.random );
        assert( old_rng.random_count == new_rng.random_count );
        assert( old_rng.probability_count == new_rng.probability_count );
        assert( before.size() <= 8 );
    }
    std::puts( "PASS fire neighbor selection: 2,000 scenarios preserve candidate order and identical RNG consumption" );

    constexpr int repetitions = 10000;
    configure_neighbors( 20, 20, 3 );
    const tripoint_bub_ms p( 20, 20 );
    random_state = 1234u;
    allocation_count = allocation_bytes = 0;
    count_allocations = true;
    for( int i = 0; i < repetitions; ++i ) {
        before.clear();
        build_baseline( p, i % 8, 12, before );
    }
    count_allocations = false;
    const size_t old_allocations = allocation_count;
    const size_t old_bytes = allocation_bytes;

    random_state = 1234u;
    allocation_count = allocation_bytes = 0;
    count_allocations = true;
    for( int i = 0; i < repetitions; ++i ) {
        after.clear();
        build_optimized( p, i % 8, 12, after );
    }
    count_allocations = false;
    const size_t new_allocations = allocation_count;
    const size_t new_bytes = allocation_bytes;

    assert( old_allocations == static_cast<size_t>( repetitions ) );
    assert( new_allocations == 0 );
    assert( old_bytes > 0 && new_bytes == 0 );
    std::printf( "MEASURE fire neighbor selection (%d burning tiles): allocations %zu -> %zu; requested bytes %zu -> %zu\n",
                 repetitions, old_allocations, new_allocations, old_bytes, new_bytes );
    std::puts( "PASS fire neighbor stack buffer: hot path eliminates scratch heap allocation" );
}
''';

with tempfile.TemporaryDirectory(prefix="fire-neighbor-buffer-", dir=out) as tmp:
    cpp = Path(tmp) / "test.cpp"
    binary = Path(tmp) / "test"
    cpp.write_text(program, encoding="utf-8")
    subprocess.run(
        ["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
         str(cpp), "-o", str(binary)],
        check=True, timeout=60
    )
    subprocess.run([str(binary)], check=True, timeout=60)
print("PASS fire neighbor source contract, differential order/RNG, and allocation benchmark")
