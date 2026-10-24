#!/usr/bin/env python3
"""Reuse the already-loaded eight neighbors for wind-blocker filtering.

The original calls map::get_wind_blockers(), which resolves three of the same
neighbor tiles again via maptile_at(). Replace those lookups with indexes into
get_neighbors()'s already materialized array. The thresholds and direction
mapping mirror get_wind_blockers() in pristine CDDA 0.I.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
if "static std::array<std::size_t, 3> wind_blocker_neighbor_indices(" in source:
    raise SystemExit("ERROR: wind blocker neighbor-index helper already exists")

signature = "void map::spread_gas("
fire_signature = "void field_processor_fd_fire("
if source.count(signature) != 1 or source.count(fire_signature) != 1:
    raise SystemExit("ERROR: expected one gas-spread and one fire processor")


def extract(text: str, sig: str) -> tuple[int, int, str]:
    start = text.index(sig)
    opening = text.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return start, end, text[start:end]


gas_start, gas_end, gas = extract(source, signature)
fire_start, fire_end, fire = extract(source, fire_signature)

gas_old = """            auto maptiles = get_wind_blockers( winddirection, p );
            // Three map tiles that are facing the wind direction.
            const maptile &remove_tile = std::get<0>( maptiles );
            const maptile &remove_tile2 = std::get<1>( maptiles );
            const maptile &remove_tile3 = std::get<2>( maptiles );"""
gas_new = """            const auto blocker_indices = wind_blocker_neighbor_indices( winddirection );
            const maptile &remove_tile = neighs[blocker_indices[0]].second;
            const maptile &remove_tile2 = neighs[blocker_indices[1]].second;
            const maptile &remove_tile3 = neighs[blocker_indices[2]].second;"""
if gas.count(gas_old) != 1:
    raise SystemExit("ERROR: gas wind-blocker lookup sequence did not match exactly once")
gas = gas.replace(gas_old, gas_new, 1)

fire_old = """    auto maptiles = here.get_wind_blockers( winddirection, p );
    maptile remove_tile = std::get<0>( maptiles );
    maptile remove_tile2 = std::get<1>( maptiles );
    maptile remove_tile3 = std::get<2>( maptiles );"""
fire_new = """    const auto blocker_indices = wind_blocker_neighbor_indices( winddirection );
    const maptile &remove_tile = neighs[blocker_indices[0]].second;
    const maptile &remove_tile2 = neighs[blocker_indices[1]].second;
    const maptile &remove_tile3 = neighs[blocker_indices[2]].second;"""
if fire.count(fire_old) != 1:
    raise SystemExit("ERROR: fire wind-blocker lookup sequence did not match exactly once")
fire = fire.replace(fire_old, fire_new, 1)

if "get_wind_blockers(" in gas or "get_wind_blockers(" in fire:
    raise SystemExit("ERROR: field processor still calls get_wind_blockers()")
if "neighs[blocker_indices[0]].second" not in gas or "neighs[blocker_indices[0]].second" not in fire:
    raise SystemExit("ERROR: reuse of the existing neighbor array is missing")

helper = """// Neighbor order matches eight_horizontal_neighbors in point.h:
// northwest, north, northeast, west, east, southwest, south, southeast.
struct wind_blocker_neighbor_rule {
    int minimum_direction;
    std::array<std::size_t, 3> indices;
};

static std::array<std::size_t, 3> wind_blocker_neighbor_indices( const int winddirection )
{
    static constexpr std::array<wind_blocker_neighbor_rule, 9> rules = {{
            { 330, {{ 4, 2, 7 }} }, // east, northeast, southeast
            { 301, {{ 7, 4, 6 }} }, // southeast, east, south
            { 240, {{ 6, 5, 7 }} }, // south, southwest, southeast
            { 211, {{ 5, 3, 6 }} }, // southwest, west, south
            { 150, {{ 3, 0, 5 }} }, // west, northwest, southwest
            { 121, {{ 0, 1, 3 }} }, // northwest, north, west
            { 60,  {{ 1, 0, 2 }} }, // north, northwest, northeast
            { 31,  {{ 2, 4, 1 }} }, // northeast, east, north
            { 0,   {{ 4, 2, 7 }} }  // east, northeast, southeast
    }};
    for( const wind_blocker_neighbor_rule &rule : rules ) {
        if( winddirection >= rule.minimum_direction ) {
            return rule.indices;
        }
    }
    return rules.back().indices;
}

"""
anchor = '#include "weather.h"\n'
if source.count(anchor) != 1:
    raise SystemExit("ERROR: expected one weather.h include anchor")
source = source.replace(anchor, anchor + "\n" + helper, 1)

# Replace function bodies by their exact source offsets after helper insertion.
# Offsets after the include are shifted, so locate signatures again.
gas_start, gas_end, _ = extract(source, signature)
fire_start, fire_end, _ = extract(source, fire_signature)
# Apply later offset first to keep the earlier offset stable.
if gas_start < fire_start:
    source = source[:fire_start] + fire + source[fire_end:]
    gas_start, gas_end, _ = extract(source, signature)
    source = source[:gas_start] + gas + source[gas_end:]
else:
    source = source[:gas_start] + gas + source[gas_end:]
    fire_start, fire_end, _ = extract(source, fire_signature)
    source = source[:fire_start] + fire + source[fire_end:]

path.write_text(source, encoding="utf-8")
print("PASS: fire and gas processors reuse preloaded neighbor tiles for wind blockers")
