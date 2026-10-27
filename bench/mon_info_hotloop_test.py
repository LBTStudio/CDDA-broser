#!/usr/bin/env python3
"""Differential guard for the mon_info_update visible-creature hot loop.

It normalizes only the four intended local rewrites against pristine CDDA 0.I.
Operation counts are source-derived accounting, not a wall-clock game benchmark.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).resolve()
root = Path(__file__).resolve().parent.parent
transform = root / "ci" / "optimize_mon_info_hotloop.py"
signature = "void game::mon_info_update( )"


def upstream(name):
    return subprocess.check_output(
        ["git", "-C", str(source), "show", "HEAD:src/" + name], text=True
    )


def function(text, start_signature):
    start = text.index(start_signature)
    opening = text.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def tokens(text):
    text = re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"\s+", "", text)


original_source = upstream("game.cpp")
patched_source = (source / "src" / "game.cpp").read_text(encoding="utf-8")
original = function(original_source, signature)
optimized = function(patched_source, signature)

# Exercise the actual transformer on pristine source so a source-guard test
# cannot pass merely because the expected text was hand-edited in the patch.
with tempfile.TemporaryDirectory(prefix="cdda-mon-info-transform-") as tmp:
    temp_game = Path(tmp) / "game.cpp"
    temp_game.write_text(original_source, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(transform), str(temp_game)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
    )
    assert result.returncode == 0, result.stdout
    simulated = function(temp_game.read_text(encoding="utf-8"), signature)
    assert simulated == optimized, "production transform result differs from test transform output"

assert original.count("const bool safemode_empty = get_safemode().empty();") == 1
assert optimized.count("const bool safemode_empty = visible_creatures.empty() || get_safemode().empty();") == 1
assert optimized.index("const bool safemode_empty = visible_creatures.empty() || get_safemode().empty();") < optimized.index(
    "for( Creature *c : visible_creatures )"
)
assert optimized.count("dynamic_cast<monster *>( c )") == 1
assert optimized.count("dynamic_cast<npc *>( c )") == 1
assert "else if( npc *p = dynamic_cast<npc *>( c ) )" in optimized
assert "rl_dist( pos, c_pos )" in optimized
assert "rl_dist( pos, critter.pos_bub( here ) )" not in optimized

# Undo only the explicit optimizations and compare the rest of the function.
normalized = optimized
new_loop = """    const auto visible_creatures = u.get_visible_creatures( MAPSIZE_X );
    // The list is stable during this routine. Preserve the zero-visible-creature
    // no-op while doing the safe-mode rule lookup at most once for a nonempty list.
    const bool safemode_empty = visible_creatures.empty() || get_safemode().empty();

    for( Creature *c : visible_creatures ) {
        monster *m = dynamic_cast<monster *>( c );
        const tripoint_bub_ms c_pos = c->pos_bub( here );"""
old_loop = """    for( Creature *c : u.get_visible_creatures( MAPSIZE_X ) ) {
        monster *m = dynamic_cast<monster *>( c );
        npc *p = dynamic_cast<npc *>( c );
        const tripoint_bub_ms c_pos = c->pos_bub( here );"""
assert normalized.count(new_loop) == 1
normalized = normalized.replace(new_loop, old_loop, 1)
new_npc = "        } else if( npc *p = dynamic_cast<npc *>( c ) ) {"
old_npc = "        } else if( p != nullptr ) {"
assert normalized.count(new_npc) == 1
normalized = normalized.replace(new_npc, old_npc, 1)
new_position = "rl_dist( pos, c_pos )"
old_position = "rl_dist( pos, critter.pos_bub( here ) )"
assert normalized.count(new_position) == 1
normalized = normalized.replace(new_position, old_position, 1)
new_check = """        bool need_processing = false;

        if( m != nullptr ) {"""
old_check = """        bool need_processing = false;
        const bool safemode_empty = get_safemode().empty();

        if( m != nullptr ) {"""
assert normalized.count(new_check) == 1
normalized = normalized.replace(new_check, old_check, 1)
assert tokens(normalized) == tokens(original), (
    "changes beyond the safe-mode lookup, deferred NPC cast, or cached position were detected"
)

# Static operation accounting for monster/NPC-only visible sets. The game
# semantics and list order remain source-identical; this model does not assign
# a gameplay-wide speedup.
for count in (0, 32, 512, 2048):
    monsters = count * 3 // 4
    npcs = count - monsters
    baseline_safe_lookups = count
    optimized_safe_lookups = 1 if count else 0
    baseline_casts = count * 2
    optimized_casts = monsters + npcs * 2
    baseline_position_conversions = count + monsters
    optimized_position_conversions = count
    assert optimized_safe_lookups <= baseline_safe_lookups
    assert optimized_casts <= baseline_casts
    assert optimized_position_conversions <= baseline_position_conversions
    print(
        f"source-derived N={count}: safe-mode checks "
        f"{baseline_safe_lookups}->{optimized_safe_lookups}, dynamic casts "
        f"{baseline_casts}->{optimized_casts}, position conversions "
        f"{baseline_position_conversions}->{optimized_position_conversions}"
    )

print("PASS mon_info_update differential guard: safe mode, visible-creature order, monster/NPC branches, distance and danger/distraction semantics preserved")
