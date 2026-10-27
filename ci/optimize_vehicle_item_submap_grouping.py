#!/usr/bin/env python3
"""Replace tree-based vehicle-submap grouping with sorted contiguous storage.

The loop must preserve std::set<tripoint>'s lexicographic x/y/z order and
duplicate removal. The regression test compares the sequence and exercises
allocations over duplicate-heavy and mostly-unique inputs.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
old = """        std::set<tripoint> submaps_with_vehicles;
        for( vehicle *this_vehicle : cache.vehicle_list ) {
            tripoint_bub_ms pos = this_vehicle->pos_bub( *this );
            submaps_with_vehicles.emplace( pos.x() / SEEX, pos.y() / SEEY, pos.z() );
        }
        for( const tripoint &pos : submaps_with_vehicles ) {"""
new = """        // Keep std::set's x/y/z traversal order and de-duplication while
        // avoiding a tree-node allocation for every distinct vehicle submap.
        std::vector<tripoint> submaps_with_vehicles;
        submaps_with_vehicles.reserve( std::min(
                                           cache.vehicle_list.size(),
                                           static_cast<std::size_t>( my_MAPSIZE * my_MAPSIZE ) ) );
        for( vehicle *this_vehicle : cache.vehicle_list ) {
            tripoint_bub_ms pos = this_vehicle->pos_bub( *this );
            submaps_with_vehicles.emplace_back( pos.x() / SEEX, pos.y() / SEEY, pos.z() );
        }
        std::sort( submaps_with_vehicles.begin(), submaps_with_vehicles.end() );
        submaps_with_vehicles.erase(
            std::unique( submaps_with_vehicles.begin(), submaps_with_vehicles.end() ),
            submaps_with_vehicles.end() );
        for( const tripoint &pos : submaps_with_vehicles ) {"""
if source.count(old) != 1:
    raise SystemExit("ERROR: map::process_items vehicle-submap grouping did not match exactly once")
if "submaps_with_vehicles.erase(" in source:
    raise SystemExit("ERROR: vehicle-submap grouping already optimized")
source = source.replace(old, new, 1)
path.write_text(source, encoding="utf-8")
print("PASS: process_items vehicle-submap grouping uses sorted, de-duplicated contiguous storage")
