#!/usr/bin/env python3
"""Differential test for gas/smoke field neighbor-candidate stack buffers.

Extracts map::spread_gas() from pristine CDDA 0.I and patched source. It asserts
that the function differs only by storage/count substitutions, then compiles
those exact candidate-selection blocks into a controlled fixture and compares
chosen destinations plus RNG state/call counts. Allocation numbers are
subsystem measurements, not full-building fire simulations.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).resolve()
out = Path(__file__).resolve().parent / "out" / "gas-spread-buffers"
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


old_method = function(original("map_field.cpp"), "void map::spread_gas(")
new_method = function((source / "src/map_field.cpp").read_text(encoding="utf-8"),
                      "void map::spread_gas(")

expected = old_method
replacements = [
    ("    std::vector<size_t> spread;",
     "    // A tile has at most eight horizontal neighbors; avoid heap allocation per gas field.\n"
     "    std::array<size_t, 8> spread{};\n"
     "    size_t spread_count = 0;"),
    ("            spread.push_back( i );", "            spread[spread_count++] = i;"),
    ("    if( !spread.empty() && one_in( spread.size() ) )",
     "    if( spread_count > 0 && one_in( spread_count ) )"),
    ("            std::pair<tripoint_bub_ms, maptile> &n = neighs[ random_entry( spread ) ];",
     "            std::pair<tripoint_bub_ms, maptile> &n = neighs[ spread[rng( 0, spread_count - 1 )] ];"),
    ("            std::vector<size_t> neighbour_vec;",
     "            std::array<size_t, 8> neighbour_vec{};\n"
     "            size_t neighbour_count = 0;"),
    ("            for( const size_t &i : spread ) {",
     "            for( size_t spread_index = 0; spread_index < spread_count; ++spread_index ) {\n"
     "                const size_t i = spread[spread_index];"),
    ("                    neighbour_vec.push_back( i );", "                    neighbour_vec[neighbour_count++] = i;"),
    ("            if( !neighbour_vec.empty() ) {", "            if( neighbour_count > 0 ) {"),
    ("                std::pair<tripoint_bub_ms, maptile> &n = neighs[ random_entry( neighbour_vec ) ];",
     "                std::pair<tripoint_bub_ms, maptile> &n = neighs[ neighbour_vec[rng( 0, neighbour_count - 1 )] ];"),
]
for old, new in replacements:
    assert expected.count(old) == 1, f"expected one transformation anchor: {old[:80]!r}"
    expected = expected.replace(old, new, 1)
old_wind = """            auto maptiles = get_wind_blockers( winddirection, p );
            // Three map tiles that are facing the wind direction.
            const maptile &remove_tile = std::get<0>( maptiles );
            const maptile &remove_tile2 = std::get<1>( maptiles );
            const maptile &remove_tile3 = std::get<2>( maptiles );"""
new_wind = """            const auto blocker_indices = wind_blocker_neighbor_indices( winddirection );
            const maptile &remove_tile = neighs[blocker_indices[0]].second;
            const maptile &remove_tile2 = neighs[blocker_indices[1]].second;
            const maptile &remove_tile3 = neighs[blocker_indices[2]].second;"""
assert expected.count(old_wind) == 1
expected = expected.replace(old_wind, new_wind, 1)
assert expected == new_method, (
    "map::spread_gas changed beyond the audited vector-to-fixed-array substitutions")

for forbidden in (
    "std::vector<size_t> spread", "std::vector<size_t> neighbour_vec",
    "spread.push_back", "spread.empty()", "spread.size()", "random_entry( spread )",
    "neighbour_vec.push_back", "neighbour_vec.empty()", "neighbour_vec.size()",
    "random_entry( neighbour_vec )",
):
    assert forbidden not in new_method, f"dynamic candidate operation remains: {forbidden}"

def candidate_block(method: str) -> str:
    start = method.index("    auto neighs = get_neighbors( p );")
    end = method.index("    } else if( p.z() < OVERMAP_HEIGHT )", start)
    # Stop before the upward-spread fallback; append the matching closing brace.
    return method[start:end] + "    }\n"


old_block = candidate_block(old_method)
new_block = candidate_block(new_method)
assert "std::vector<size_t> spread;" in old_block
assert "std::array<size_t, 8> spread{};" in new_block
assert "random_entry( spread )" in old_block
assert "spread[rng( 0, spread_count - 1 )]" in new_block

program = r'''
#include <algorithm>
#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <iterator>
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

struct point {
    int x = 0, y = 0;
    bool operator!=( const point &other ) const { return x != other.x || y != other.y; }
};
struct tripoint_bub_ms { int x = 0, y = 0, z = 0; };
struct maptile { point pos_; int id = 0; };
using neighbor_array = std::array<std::pair<tripoint_bub_ms, maptile>, 8>;
struct field_entry { int id = 0; };

static uint32_t random_state = 1;
static size_t rng_calls = 0, one_in_calls = 0, x_in_y_calls = 0;
static bool force_one_in = false;
static size_t rng( int low, size_t high ) {
    ++rng_calls;
    random_state = random_state * 1664525u + 1013904223u;
    return static_cast<size_t>( low ) + random_state %
           ( high - static_cast<size_t>( low ) + 1 );
}
static bool one_in( size_t n ) {
    ++one_in_calls;
    const bool random_result = rng( 1, n ) == 1;
    return force_one_in ? true : random_result;
}
static bool x_in_y( int x, int y ) {
    ++x_in_y_calls;
    return rng( 1, static_cast<size_t>( std::max( 1, y ) ) ) <= static_cast<size_t>( x );
}
template<class Container>
static typename Container::value_type random_entry( const Container &container ) {
    auto iter = container.begin();
    std::advance( iter, rng( 0, container.size() - 1 ) );
    return *iter;
}

struct map {
    neighbor_array neighbors;
    int blocker_indices[3] = { 0, 2, 7 };
    unsigned int allowed_mask = 0xff;
    int last_destination = -1;
    int blocker_lookups = 0;

    neighbor_array get_neighbors( const tripoint_bub_ms & ) const { return neighbors; }
    std::tuple<maptile, maptile, maptile> get_wind_blockers(
        const int &winddirection, const tripoint_bub_ms & ) {
        ++blocker_lookups;
        static constexpr std::array<std::array<int, 4>, 9> rules = {{
            {{330, 4, 2, 7}}, {{301, 7, 4, 6}}, {{240, 6, 5, 7}},
            {{211, 5, 3, 6}}, {{150, 3, 0, 5}}, {{121, 0, 1, 3}},
            {{60, 1, 0, 2}}, {{31, 2, 4, 1}}, {{0, 4, 2, 7}}
        }};
        std::array<size_t, 3> idx{{4, 2, 7}};
        for (const auto &rule : rules) {
            if (winddirection >= rule[0]) {
                idx[0] = static_cast<size_t>(rule[1]);
                idx[1] = static_cast<size_t>(rule[2]);
                idx[2] = static_cast<size_t>(rule[3]);
                break;
            }
        }
        return { neighbors[idx[0]].second, neighbors[idx[1]].second, neighbors[idx[2]].second };
    }
    bool gas_can_spread_to( field_entry &, const maptile &dst ) const {
        return ( allowed_mask & (1u << dst.id) ) != 0;
    }
    void gas_spread_to( field_entry &, const maptile &dst, const tripoint_bub_ms & ) {
        last_destination = dst.id;
    }
    void select_baseline( field_entry &, const tripoint_bub_ms &, int, bool, int );
    void select_optimized( field_entry &, const tripoint_bub_ms &, int, bool, int );
};
static void configure(map &m, uint32_t scenario) {
    for (int i = 0; i < 8; ++i) {
        tripoint_bub_ms p{i, i * 3, static_cast<int>((scenario >> 4) % 3) - 1};
        m.neighbors[static_cast<size_t>(i)] = {p, maptile{{i, i * 3}, i}};
    }
    m.blocker_indices[0] = static_cast<int>(scenario % 8);
    m.blocker_indices[1] = static_cast<int>((scenario >> 3) % 8);
    m.blocker_indices[2] = static_cast<int>((scenario >> 7) % 8);
    m.allowed_mask = (scenario >> 11) & 0xff;
    m.last_destination = -1;
    m.blocker_lookups = 0;
}
static void reset_rng(uint32_t seed) {
    random_state = seed;
    rng_calls = one_in_calls = x_in_y_calls = 0;
}
struct trace {
    uint32_t state;
    size_t rng;
    size_t one_in;
    size_t x_in_y;
    int destination;
    int blocker_lookups;
};
static trace snapshot(const map &m) {
    return {random_state, rng_calls, one_in_calls, x_in_y_calls,
            m.last_destination, m.blocker_lookups};
}
''';
rule_start = patched.index("struct wind_blocker_neighbor_rule {")
helper_start = patched.index(
    "static std::array<std::size_t, 3> wind_blocker_neighbor_indices(", rule_start
)
wind_rule_definition = patched[rule_start:helper_start]
wind_helper = function(
    patched, "static std::array<std::size_t, 3> wind_blocker_neighbor_indices("
)
program += "\n" + wind_rule_definition + "\n" + wind_helper + "\n"
program += ("\nvoid map::select_baseline( field_entry &cur, const tripoint_bub_ms &p,\n"
            "                          int winddirection, bool sheltered, int windpower ) {\n");
program += old_block + "}\n";
program += ("\nvoid map::select_optimized( field_entry &cur, const tripoint_bub_ms &p,\n"
            "                           int winddirection, bool sheltered, int windpower ) {\n");
program += new_block + "}\n";
program += r'''
int main() {
    field_entry cur;
    map before, after;
    uint32_t scenario = 0xC0FFEEu;
    for (int trial = 0; trial < 4000; ++trial) {
        scenario = scenario * 1664525u + 1013904223u;
        configure(before, scenario);
        configure(after, scenario);
        const bool sheltered = (scenario & 1u) != 0;
        const int windpower = static_cast<int>((scenario >> 16) % 30);
        const int winddirection = static_cast<int>((scenario >> 22) % 360);

        force_one_in = (trial % 4) == 0;
        reset_rng(17u + static_cast<uint32_t>(trial));
        before.select_baseline(cur, {3,4,0}, winddirection, sheltered, windpower);
        const trace old_trace = snapshot(before);

        reset_rng(17u + static_cast<uint32_t>(trial));
        after.select_optimized(cur, {3,4,0}, winddirection, sheltered, windpower);
        const trace new_trace = snapshot(after);

        assert(old_trace.state == new_trace.state);
        assert(old_trace.rng == new_trace.rng);
        assert(old_trace.one_in == new_trace.one_in);
        assert(old_trace.x_in_y == new_trace.x_in_y);
        assert(old_trace.destination == new_trace.destination);
        assert(old_trace.blocker_lookups == new_trace.blocker_lookups);
    }
    std::puts("PASS gas spread candidate selection: 4,000 scenarios preserve destination and exact RNG/probability call order");

    constexpr int repetitions = 10000;
    configure(before, 0x0003ffffu);
    configure(after, 0x0003ffffu);
    before.allowed_mask = after.allowed_mask = 0xffu;
    field_entry hot;
    force_one_in = true;

    reset_rng(123u);
    allocation_count = allocation_bytes = 0;
    count_allocations = true;
    for (int i = 0; i < repetitions; ++i) {
        before.last_destination = -1;
        before.select_baseline(hot, {3,4,0}, i % 360, false, 12);
    }
    count_allocations = false;
    const size_t old_allocations = allocation_count;
    const size_t old_bytes = allocation_bytes;

    reset_rng(123u);
    allocation_count = allocation_bytes = 0;
    count_allocations = true;
    for (int i = 0; i < repetitions; ++i) {
        after.last_destination = -1;
        after.select_optimized(hot, {3,4,0}, i % 360, false, 12);
    }
    count_allocations = false;
    const size_t new_allocations = allocation_count;
    const size_t new_bytes = allocation_bytes;
    assert(old_allocations > 0);
    assert(new_allocations == 0 && new_bytes == 0);
    std::printf("MEASURE gas spread candidate selection (%d windy field tiles): allocations %zu -> %zu; requested bytes %zu -> %zu\n",
                repetitions, old_allocations, new_allocations, old_bytes, new_bytes);
    std::puts("PASS gas spread stack buffers: no dynamic candidate storage on the hot path");
}
''';

with tempfile.TemporaryDirectory(prefix="gas-spread-buffer-", dir=out) as tmp:
    cpp = Path(tmp) / "test.cpp"
    binary = Path(tmp) / "test"
    cpp.write_text(program, encoding="utf-8")
    subprocess.run(
        ["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
         str(cpp), "-o", str(binary)],
        check=True, timeout=90
    )
    subprocess.run([str(binary)], check=True, timeout=90)
print("PASS gas spread source contract, differential RNG/candidate test, and allocation benchmark")
