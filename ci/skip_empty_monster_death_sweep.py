#!/usr/bin/env python3
"""Avoid copying the monster tracker when no dead monster needs processing.

kill_marked_for_death() must retain its owning snapshot when processing deaths,
because die() may add monsters to the tracker. On ordinary turns with no dead
monsters, a full scan can safely return before constructing that snapshot.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
signature = "bool creature_tracker::kill_marked_for_death()"
if source.count(signature) != 1:
    raise SystemExit("ERROR: expected exactly one creature_tracker::kill_marked_for_death()")

start = source.index(signature)
opening = source.index("{", start)
depth, end = 1, opening + 1
while depth:
    depth += (source[end] == "{") - (source[end] == "}")
    end += 1
method = source[start:end]
copy_statement = "    const auto copy = monsters_list;"
if method.count(copy_statement) != 1:
    raise SystemExit("ERROR: expected one owning monster-list snapshot in kill_marked_for_death()")
if "cata_assert( mon_ptr );" not in method or "return monster_is_dead;" not in method:
    raise SystemExit("ERROR: kill_marked_for_death() no longer matches the audited control flow")

guard = """    // Most turns have no dead monsters; avoid copying every shared_ptr in that case.
    bool has_dead_monster = false;
    for( const shared_ptr_fast<monster> &mon_ptr : monsters_list ) {
        cata_assert( mon_ptr );
        if( mon_ptr->is_dead() ) {
            has_dead_monster = true;
            break;
        }
    }
    if( !has_dead_monster ) {
        return false;
    }

"""
new_method = method.replace(copy_statement, guard + copy_statement, 1)
source = source[:start] + new_method + source[end:]
path.write_text(source, encoding="utf-8")
print("PASS: kill_marked_for_death() now skips the owning snapshot when no monster is dead")
