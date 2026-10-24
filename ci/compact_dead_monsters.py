#!/usr/bin/env python3
"""Replace per-dead vector erases with stable one-pass compaction.

The source must already contain the monster-snapshot cache field and the
upstream remove_dead() implementation from CDDA 0.I. Fail closed if its
signature or expected operations have changed.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
signature = "void creature_tracker::remove_dead()"
if source.count(signature) != 1:
    raise SystemExit("ERROR: expected exactly one creature_tracker::remove_dead()")

start = source.index(signature)
opening = source.index("{", start)
depth = 1
end = opening + 1
while depth:
    depth += (source[end] == "{") - (source[end] == "}")
    end += 1
old_function = source[start:end]
required = (
    "remove_from_location_map( *critter );",
    "monsters_snapshot_.reset();",
    "monsters_list.erase( iter );",
    "removed_this_turn_.clear();",
)
for marker in required:
    if marker not in old_function:
        raise SystemExit(f"ERROR: remove_dead() missing expected upstream marker: {marker}")

replacement = """void creature_tracker::remove_dead()
{
    // Stable compaction avoids shifting the vector tail for every dead monster.
    auto write = monsters_list.begin();
    bool removed_any = false;
    for( auto read = monsters_list.begin(); read != monsters_list.end(); ++read ) {
        monster *const critter = read->get();
        if( critter->is_dead() ) {
            if( !removed_any ) {
                // Invalidate cached membership exactly once before the vector changes.
                monsters_snapshot_.reset();
                removed_any = true;
            }
            remove_from_location_map( *critter );
        } else {
            if( write != read ) {
                *write = *read;
            }
            ++write;
        }
    }

    if( removed_any ) {
        monsters_list.erase( write, monsters_list.end() );
    }
    removed_this_turn_.clear();
}"""
source = source[:start] + replacement + source[end:]
path.write_text(source, encoding="utf-8")
print("PASS: stable remove_dead() compaction installed")
