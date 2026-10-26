#!/usr/bin/env python3
"""Reserve vehicle collision storage once per move_vehicle() call.

The collision vector is cleared and reused across collision attempts. Reserving
from the vehicle's part count avoids geometric capacity growth for the usual
case without changing which collision tests run or how results are ordered.
Source anchors deliberately fail closed if upstream changes this path.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
signature = "vehicle *map::move_vehicle( vehicle &veh, const tripoint_rel_ms &dp, const tileray &facing )"
if source.count(signature) != 1:
    raise SystemExit("ERROR: expected exactly one map::move_vehicle definition")
old = """    std::vector<veh_collision> collisions;

    // Find collisions"""
new = """    std::vector<veh_collision> collisions;
    // A single movement can inspect many vehicle parts and may repeat collision
    // detection after an impact. Reserve once, then reuse capacity across retries.
    collisions.reserve( static_cast<std::size_t>( veh.part_count() ) );

    // Find collisions"""
if source.count(old) != 1:
    raise SystemExit("ERROR: expected collision vector declaration did not match once")
start = source.index(signature)
end_marker = "float map::vehicle_vehicle_collision("
end = source.index(end_marker, start)
function = source[start:end]
if function.count(old) != 1:
    raise SystemExit("ERROR: collision vector anchor is not unique within move_vehicle")
source = source[:start] + function.replace(old, new, 1) + source[end:]
if source.count("collisions.reserve( static_cast<std::size_t>( veh.part_count() ) );") != 1:
    raise SystemExit("ERROR: collision storage reservation is missing or duplicated")
path.write_text(source, encoding="utf-8")
print("PASS: move_vehicle reserves collision storage using vehicle part count")
