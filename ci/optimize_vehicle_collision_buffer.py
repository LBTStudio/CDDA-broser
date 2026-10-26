#!/usr/bin/env python3
"""Lazily reserve bounded collision storage after the first real collision.

Unlike reserving in map::move_vehicle(), this does not allocate at all for
ordinary collision-free movement. just_detect remains allocation behavior as
upstream (one candidate can be returned immediately). A 16-entry initial cap
limits memory over-reservation for large vehicles with only one hit; vector
growth remains available for exceptional multi-collision cases.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
signature = """bool vehicle::collision( map &here, std::vector<veh_collision> &colls,
                         const tripoint_rel_ms &dp,"""
if source.count(signature) != 1:
    raise SystemExit("ERROR: expected exactly one vehicle::collision definition")

rotor_old = """                    } else {
                        colls.push_back( rotor_coll );
                    }"""
rotor_new = """                    } else {
                        if( colls.empty() ) {
                            colls.reserve( std::min<std::size_t>(
                                               static_cast<std::size_t>( part_count() ), 16 ) );
                        }
                        colls.push_back( rotor_coll );
                    }"""
main_old = """        colls.push_back( coll );

        if( just_detect ) {"""
main_new = """        if( !just_detect && colls.empty() ) {
            // Reserve only after a real collision is found. Clear-road driving
            // keeps the upstream zero-allocation fast path.
            colls.reserve( std::min<std::size_t>(
                               static_cast<std::size_t>( part_count() ), 16 ) );
        }
        colls.push_back( coll );

        if( just_detect ) {"""
if source.count(rotor_old) != 1:
    raise SystemExit("ERROR: rotor collision push anchor changed")
if source.count(main_old) != 1:
    raise SystemExit("ERROR: main collision push anchor changed")
start = source.index(signature)
end_marker = "veh_collision vehicle::part_collision("
end = source.index(end_marker, start)
function = source[start:end]
if function.count(rotor_old) != 1 or function.count(main_old) != 1:
    raise SystemExit("ERROR: collision insertion anchors are not unique in the target function")
function = function.replace(rotor_old, rotor_new, 1).replace(main_old, main_new, 1)
source = source[:start] + function + source[end:]
if source.count("colls.reserve( std::min<std::size_t>(") != 2:
    raise SystemExit("ERROR: expected both lazy collision reserve sites")
if "if( !just_detect && colls.empty() ) {" not in source:
    raise SystemExit("ERROR: no collision-only allocation guard")
path.write_text(source, encoding="utf-8")
print("PASS: vehicle::collision reserves bounded storage only after a collision is found")
