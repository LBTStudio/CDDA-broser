#!/usr/bin/env python3
"""Use contiguous sorting for large vehicle-submap collections.

The <=32 path already uses a fixed stack buffer. For larger collections, a
reserved vector plus sort/unique preserves the std::set visitation order while
replacing one heap node per distinct submap with one contiguous allocation.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
signature = "static void for_each_unique_vehicle_submap("
if source.count(signature) != 1:
    raise SystemExit("ERROR: expected exactly one vehicle-submap helper")

start = source.index(signature)
opening = source.index("{", start)
depth, end = 1, opening + 1
while depth:
    depth += (source[end] == "{") - (source[end] == "}")
    end += 1
method = source[start:end]

old = """    // Keep the original heap-backed path for large populations.
    std::set<tripoint> positions;
    for( vehicle *this_vehicle : vehicles ) {
        positions.emplace( submap_position( this_vehicle ) );
    }
    for( const tripoint &pos : positions ) {
        process( pos );
    }"""
new = """    // For large populations, keep coordinates contiguous and sort once.
    // This preserves the set's order without allocating one tree node per key.
    std::vector<tripoint> positions;
    positions.reserve( vehicles.size() );
    for( vehicle *this_vehicle : vehicles ) {
        positions.push_back( submap_position( this_vehicle ) );
    }
    std::sort( positions.begin(), positions.end() );
    const auto unique_end = std::unique( positions.begin(), positions.end(),
    []( const tripoint &lhs, const tripoint &rhs ) {
        return lhs.x == rhs.x && lhs.y == rhs.y && lhs.z == rhs.z;
    } );
    positions.erase( unique_end, positions.end() );
    for( const tripoint &pos : positions ) {
        process( pos );
    }"""
if method.count(old) != 1:
    raise SystemExit("ERROR: large vehicle-submap set block did not match exactly once")
method = method.replace(old, new, 1)
if "std::set<tripoint> positions;" in method:
    raise SystemExit("ERROR: tree-backed large-vehicle path remains after transform")
if "positions.reserve( vehicles.size() );" not in method:
    raise SystemExit("ERROR: reserved vector path missing")
source = source[:start] + method + source[end:]
path.write_text(source, encoding="utf-8")
print("PASS: large vehicle-submap path now uses reserved vector sort/unique")
