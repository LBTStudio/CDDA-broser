#!/usr/bin/env python3
"""Replace the <=8 candidate vectors in map::spread_gas with stack arrays.

This changes storage only. Candidate traversal order, gas_can_spread_to calls,
wind-filter probability checks and rng calls must remain in source order.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
signature = "void map::spread_gas("
if source.count(signature) != 1:
    raise SystemExit("ERROR: expected exactly one map::spread_gas()")

start = source.index(signature)
opening = source.index("{", start)
depth, end = 1, opening + 1
while depth:
    depth += (source[end] == "{") - (source[end] == "}")
    end += 1
method = source[start:end]


def replace_once(old: str, new: str, label: str) -> None:
    global method
    count = method.count(old)
    if count != 1:
        raise SystemExit(f"ERROR: expected one {label} anchor, found {count}")
    method = method.replace(old, new, 1)


replace_once(
    "    std::vector<size_t> spread;",
    "    // A tile has at most eight horizontal neighbors; avoid heap allocation per gas field.\n"
    "    std::array<size_t, 8> spread{};\n"
    "    size_t spread_count = 0;",
    "spread candidate container",
)
replace_once(
    "            spread.push_back( i );",
    "            spread[spread_count++] = i;",
    "spread append",
)
replace_once(
    "    if( !spread.empty() && one_in( spread.size() ) ) {",
    "    if( spread_count > 0 && one_in( spread_count ) ) {",
    "spread eligibility condition",
)
replace_once(
    "            std::pair<tripoint_bub_ms, maptile> &n = neighs[ random_entry( spread ) ];",
    "            std::pair<tripoint_bub_ms, maptile> &n = neighs[ spread[rng( 0, spread_count - 1 )] ];",
    "non-windy destination choice",
)
replace_once(
    "            std::vector<size_t> neighbour_vec;",
    "            std::array<size_t, 8> neighbour_vec{};\n"
    "            size_t neighbour_count = 0;",
    "wind-filter candidate container",
)
replace_once(
    "            for( const size_t &i : spread ) {",
    "            for( size_t spread_index = 0; spread_index < spread_count; ++spread_index ) {\n"
    "                const size_t i = spread[spread_index];",
    "wind-filter candidate traversal",
)
replace_once(
    "                    neighbour_vec.push_back( i );",
    "                    neighbour_vec[neighbour_count++] = i;",
    "wind-filter append",
)
replace_once(
    "            if( !neighbour_vec.empty() ) {",
    "            if( neighbour_count > 0 ) {",
    "wind-filter eligibility condition",
)
replace_once(
    "                std::pair<tripoint_bub_ms, maptile> &n = neighs[ random_entry( neighbour_vec ) ];",
    "                std::pair<tripoint_bub_ms, maptile> &n = neighs[ neighbour_vec[rng( 0, neighbour_count - 1 )] ];",
    "wind-filter destination choice",
)

for forbidden in (
    "std::vector<size_t> spread",
    "std::vector<size_t> neighbour_vec",
    "spread.push_back",
    "spread.empty()",
    "spread.size()",
    "random_entry( spread )",
    "neighbour_vec.push_back",
    "neighbour_vec.empty()",
    "neighbour_vec.size()",
    "random_entry( neighbour_vec )",
):
    if forbidden in method:
        raise SystemExit(f"ERROR: dynamic gas candidate operation remains: {forbidden}")

if method.count("spread_count") != 6:
    raise SystemExit(f"ERROR: unexpected spread_count use count {method.count('spread_count')}")
if method.count("neighbour_count") != 4:
    raise SystemExit(f"ERROR: unexpected neighbour_count use count {method.count('neighbour_count')}")
if method.count("rng( 0, spread_count - 1 )") != 1:
    raise SystemExit("ERROR: expected one non-windy random selection")
if method.count("rng( 0, neighbour_count - 1 )") != 1:
    raise SystemExit("ERROR: expected one windy random selection")

source = source[:start] + method + source[end:]
path.write_text(source, encoding="utf-8")
print("PASS: map::spread_gas uses two eight-entry stack buffers")
