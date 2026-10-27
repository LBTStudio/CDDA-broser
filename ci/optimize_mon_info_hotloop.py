#!/usr/bin/env python3
"""Reduce redundant work inside game::mon_info_update without skipping danger checks.

This is a source-anchored transformation: unknown upstream changes fail closed.
The visible-creature list, safe-mode outcome, NPC/monster handling, and turn cadence
remain unchanged. Only repeated emptiness checks, a failed NPC cast on monsters,
and a repeated monster position conversion are removed.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
signature = "void game::mon_info_update( )"
if source.count(signature) != 1:
    raise SystemExit("ERROR: expected exactly one game::mon_info_update() definition")

start = source.index(signature)
opening = source.index("{", start)
depth = 1
end = opening + 1
while depth:
    depth += (source[end] == "{") - (source[end] == "}")
    end += 1
function = source[start:end]

# Refuse a second application or a partially applied transformation.
if "const auto visible_creatures = u.get_visible_creatures( MAPSIZE_X );" in function:
    raise SystemExit("ERROR: mon_info hot-loop optimization already exists")
if "const bool safemode_empty = get_safemode().empty();" not in function:
    raise SystemExit("ERROR: original per-creature safe-mode check not found")

old_loop = """    for( Creature *c : u.get_visible_creatures( MAPSIZE_X ) ) {
        monster *m = dynamic_cast<monster *>( c );
        npc *p = dynamic_cast<npc *>( c );
        const tripoint_bub_ms c_pos = c->pos_bub( here );"""
new_loop = """    const auto visible_creatures = u.get_visible_creatures( MAPSIZE_X );
    // The list is stable during this routine. Preserve the zero-visible-creature
    // no-op while doing the safe-mode rule lookup at most once for a nonempty list.
    const bool safemode_empty = visible_creatures.empty() || get_safemode().empty();

    for( Creature *c : visible_creatures ) {
        monster *m = dynamic_cast<monster *>( c );
        const tripoint_bub_ms c_pos = c->pos_bub( here );"""
if function.count(old_loop) != 1:
    raise SystemExit("ERROR: visible-creature loop prefix did not match exactly once")
function = function.replace(old_loop, new_loop, 1)

old_check = """        bool need_processing = false;
        const bool safemode_empty = get_safemode().empty();

        if( m != nullptr ) {"""
new_check = """        bool need_processing = false;

        if( m != nullptr ) {"""
if function.count(old_check) != 1:
    raise SystemExit("ERROR: per-creature safe-mode check did not match exactly once")
function = function.replace(old_check, new_check, 1)

old_npc = "        } else if( p != nullptr ) {"
new_npc = "        } else if( npc *p = dynamic_cast<npc *>( c ) ) {"
if function.count(old_npc) != 1:
    raise SystemExit("ERROR: NPC branch did not match exactly once")
function = function.replace(old_npc, new_npc, 1)

old_position = "rl_dist( pos, critter.pos_bub( here ) )"
new_position = "rl_dist( pos, c_pos )"
if function.count(old_position) != 1:
    raise SystemExit("ERROR: repeated monster position conversion did not match exactly once")
function = function.replace(old_position, new_position, 1)

# Confirm the optimized function still keeps all semantic branches and the
# safe-mode/distraction work. The dedicated differential test verifies the
# normalized body against the pristine upstream function.
for required in (
    "critter.attitude( &u )",
    "get_safemode().check_monster(",
    "cancel_activity_or_ignore_query(",
    "set_safe_mode( SAFE_MODE_STOP )",
    "unique_mons[index]",
    "unique_types[index]",
):
    if required not in function:
        raise SystemExit(f"ERROR: required game semantics disappeared: {required}")

source = source[:start] + function + source[end:]
path.write_text(source, encoding="utf-8")
print("PASS: mon_info_update reuses safe-mode emptiness and visible creature positions without throttling danger checks")
