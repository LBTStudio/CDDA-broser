#!/usr/bin/env python3
"""Verify wind-blocker indices reproduce CDDA 0.I's lookup table for all directions.

The fire and gas processors already hold all eight neighboring maptile copies.
This test checks that both processors reuse those copies and that the new
index helper maps each of 360 wind directions to exactly the same three
relative neighbors as the original get_wind_blockers() table.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).resolve()
out = Path(__file__).resolve().parent / "out" / "wind-blocker-neighbor-reuse"
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


upstream = original("map_field.cpp")
patched = (source / "src/map_field.cpp").read_text(encoding="utf-8")
old_wind = function(
    upstream,
    "std::tuple<maptile, maptile, maptile> map::get_wind_blockers("
)
new_wind = function(
    patched,
    "std::tuple<maptile, maptile, maptile> map::get_wind_blockers("
)
assert old_wind == new_wind, "original get_wind_blockers implementation changed"

offset_rows = re.findall(
    r"\{\s*(\d+),\s*std::make_tuple\(\s*(point_rel_ms::[a-z_]+)\s*,"
    r"\s*(point_rel_ms::[a-z_]+)\s*,\s*(point_rel_ms::[a-z_]+)\s*\)\s*\}",
    old_wind,
    re.S,
)
assert len(offset_rows) == 9, f"expected 9 source wind-direction rules, got {len(offset_rows)}"

# This order is defined by eight_horizontal_neighbors in point.h.
offset_to_index = {
    "point_rel_ms::north_west": 0,
    "point_rel_ms::north": 1,
    "point_rel_ms::north_east": 2,
    "point_rel_ms::west": 3,
    "point_rel_ms::east": 4,
    "point_rel_ms::south_west": 5,
    "point_rel_ms::south": 6,
    "point_rel_ms::south_east": 7,
}
rows = []
for threshold, a, b, c in offset_rows:
    indices = [offset_to_index[x] for x in (a, b, c)]
    rows.append("    { " + threshold + ", {{ " + ", ".join(map(str, indices)) + " }} },")
expected_rows = "\n".join(rows)

old_gas = function(upstream, "void map::spread_gas(")
new_gas = function(patched, "void map::spread_gas(")
gas_expected = old_gas
gas_replacements = [
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
for old, new in gas_replacements:
    assert gas_expected.count(old) == 1, f"gas baseline anchor not unique: {old[:80]!r}"
    gas_expected = gas_expected.replace(old, new, 1)
gas_old_wind = """            auto maptiles = get_wind_blockers( winddirection, p );
            // Three map tiles that are facing the wind direction.
            const maptile &remove_tile = std::get<0>( maptiles );
            const maptile &remove_tile2 = std::get<1>( maptiles );
            const maptile &remove_tile3 = std::get<2>( maptiles );"""
gas_new_wind = """            const auto blocker_indices = wind_blocker_neighbor_indices( winddirection );
            const maptile &remove_tile = neighs[blocker_indices[0]].second;
            const maptile &remove_tile2 = neighs[blocker_indices[1]].second;
            const maptile &remove_tile3 = neighs[blocker_indices[2]].second;"""
assert gas_expected.count(gas_old_wind) == 1
gas_expected = gas_expected.replace(gas_old_wind, gas_new_wind, 1)
assert gas_expected == new_gas, "gas processor differs beyond the audited storage and wind-neighbor reuse"

old_fire = function(upstream, "void field_processor_fd_fire(")
new_fire = function(patched, "void field_processor_fd_fire(")
fire_expected = old_fire
fire_old_storage = """    std::vector<size_t> neighbour_vec;
    neighbour_vec.reserve( neighs.size() );"""
fire_new_storage = """    // A fire can use at most eight neighbors. Keep this hot-path scratch
    // storage on the stack instead of allocating a vector for every fire tile.
    std::array<size_t, 8> neighbour_vec{};
    size_t neighbour_count = 0;"""
assert fire_expected.count(fire_old_storage) == 1
fire_expected = fire_expected.replace(fire_old_storage, fire_new_storage, 1)
assert fire_expected.count("            neighbour_vec.push_back( i );") == 1
fire_expected = fire_expected.replace(
    "            neighbour_vec.push_back( i );",
    "            neighbour_vec[neighbour_count++] = i;", 1
)
assert fire_expected.count("neighbour_vec.size()") == 8
fire_expected = fire_expected.replace("neighbour_vec.size()", "neighbour_count")
assert fire_expected.count("neighbour_vec.empty()") == 1
fire_expected = fire_expected.replace("neighbour_vec.empty()", "neighbour_count == 0")
fire_old_wind = """    auto maptiles = here.get_wind_blockers( winddirection, p );
    maptile remove_tile = std::get<0>( maptiles );
    maptile remove_tile2 = std::get<1>( maptiles );
    maptile remove_tile3 = std::get<2>( maptiles );"""
fire_new_wind = """    const auto blocker_indices = wind_blocker_neighbor_indices( winddirection );
    const maptile &remove_tile = neighs[blocker_indices[0]].second;
    const maptile &remove_tile2 = neighs[blocker_indices[1]].second;
    const maptile &remove_tile3 = neighs[blocker_indices[2]].second;"""
assert fire_expected.count(fire_old_wind) == 1
fire_expected = fire_expected.replace(fire_old_wind, fire_new_wind, 1)
assert fire_expected == new_fire, "fire processor differs beyond the audited storage and wind-neighbor reuse"

for method_name, method in (("gas", new_gas), ("fire", new_fire)):
    assert "get_wind_blockers(" not in method, f"{method_name} processor still performs extra blocker lookups"
    assert "wind_blocker_neighbor_indices( winddirection )" in method
    assert "neighs[blocker_indices[0]].second" in method
    assert "neighs[blocker_indices[1]].second" in method
    assert "neighs[blocker_indices[2]].second" in method

struct_start = patched.index("struct wind_blocker_neighbor_rule {")
helper_start = patched.index(
    "static std::array<std::size_t, 3> wind_blocker_neighbor_indices(", struct_start
)
rule_definition = patched[struct_start:helper_start]
helper_function = function(
    patched, "static std::array<std::size_t, 3> wind_blocker_neighbor_indices("
)

cpp = r'''
#include <array>
#include <cassert>
#include <cstddef>
#include <cstdio>
'''
cpp += rule_definition + "\n" + helper_function + "\n"
cpp += r'''
struct baseline_rule {
    int minimum_direction;
    std::array<std::size_t, 3> indices;
};
'''
cpp += "static constexpr std::array<baseline_rule, 9> baseline_rules = {{\n"
cpp += expected_rows + "\n}};\n"
cpp += r'''
int main() {
    for (int direction = 0; direction < 360; ++direction) {
        std::array<std::size_t, 3> expected = baseline_rules.back().indices;
        for (const baseline_rule &rule : baseline_rules) {
            if (direction >= rule.minimum_direction) {
                expected = rule.indices;
                break;
            }
        }
        assert(wind_blocker_neighbor_indices(direction) == expected);
    }
    std::puts("PASS wind blocker mapping: new neighbor indices match original source table for all 360 wind directions");
}
'''
with tempfile.TemporaryDirectory(prefix="wind-blocker-reuse-", dir=out) as tmp:
    cpp_path = Path(tmp) / "test.cpp"
    binary = Path(tmp) / "test"
    cpp_path.write_text(cpp, encoding="utf-8")
    subprocess.run(
        ["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
         str(cpp_path), "-o", str(binary)],
        check=True, timeout=60
    )
    subprocess.run([str(binary)], check=True, timeout=60)
print("PASS wind-blocker source contract and 360-direction mapping test")
