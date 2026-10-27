#!/usr/bin/env python3
"""Batch synchronous vehicle-step redraws on WebAssembly browsers.

During map::vehmove(), the event loop cannot paint intermediate frames between
substeps. Still pump input events after each step, but defer the expensive UI
composition/display refresh until the outer vehicle movement loop finishes.
Other platforms and paths with an active destination retain the upstream path.
"""
from pathlib import Path
import sys

map_path = Path(sys.argv[1])
map_source = map_path.read_text(encoding="utf-8")
turn_path = map_path.with_name("do_turn.cpp")
turn_source = turn_path.read_text(encoding="utf-8")
header_path = map_path.with_name("do_turn.h")
header_source = header_path.read_text(encoding="utf-8")

# Keep the batch state local to the map translation unit and only compile it
# into the WebAssembly browser build.
marker = "template<typename WorkRange, typename ProcessFn>"
state = """#if defined(EMSCRIPTEN) || defined(__EMSCRIPTEN__)
static bool cdda_web_vehicle_move_batch_active = false;
static bool cdda_web_vehicle_move_redraw_pending = false;
#endif

"""
if map_source.count(marker) != 1:
    raise SystemExit("ERROR: vehicle worklist helper insertion point changed")
if "cdda_web_vehicle_move_batch_active" in map_source:
    raise SystemExit("ERROR: vehicle redraw batching state already exists")
map_source = map_source.replace(marker, state + marker, 1)

old_start = """void map::vehmove()
{
    // give vehicles movement points"""
new_start = """void map::vehmove()
{
#if defined(EMSCRIPTEN) || defined(__EMSCRIPTEN__)
    const bool cdda_web_outermost_vehicle_move = !cdda_web_vehicle_move_batch_active;
    if( cdda_web_outermost_vehicle_move ) {
        cdda_web_vehicle_move_redraw_pending = false;
    }
    cdda_web_vehicle_move_batch_active = true;
#endif
    // give vehicles movement points"""
if map_source.count(old_start) != 1:
    raise SystemExit("ERROR: map::vehmove entry point changed")
map_source = map_source.replace(old_start, new_start, 1)

zone_cache = "\"\"\"    // Vehicles can cross z-levels during one turn. A moved vehicle is removed from\n    // its old zone cache and inserted into its destination cache before this point,\n    // so checking only the player's z-level can miss the dirty vehicle entirely.\n    // Refresh dirty zone vehicles across loaded levels, then rebuild the active\n    // zone point set once. get_cache_lazy avoids creating unused level caches.\n    bool vehicle_zones_dirty = false;\n    for( int zlev = -OVERMAP_DEPTH; zlev <= OVERMAP_HEIGHT; ++zlev ) {\n        const level_cache *cache = get_cache_lazy( zlev );\n        if( cache == nullptr ) {\n            continue;\n        }\n        for( vehicle *veh : cache->zone_vehicles ) {\n            if( veh->refresh_zones() ) {\n                vehicle_zones_dirty = true;\n            }\n        }\n    }\n    if( vehicle_zones_dirty ) {\n        zone_manager::get_manager().cache_vzones( this );\n    }\"\""
zone_cache_with_flush = zone_cache + """

#if defined(EMSCRIPTEN) || defined(__EMSCRIPTEN__)
    cdda_web_vehicle_move_batch_active = !cdda_web_outermost_vehicle_move;
    if( cdda_web_outermost_vehicle_move && cdda_web_vehicle_move_redraw_pending ) {
        cdda_web_vehicle_move_redraw_pending = false;
        // The browser cannot paint synchronously between vehicle substeps.
        // Present the final board once, after all movement and zone updates.
        ui_manager::redraw_invalidated();
        refresh_display();
    }
#endif"""
if map_source.count(zone_cache) != 1:
    raise SystemExit("ERROR: map::vehmove exit point changed")
map_source = map_source.replace(zone_cache, zone_cache_with_flush, 1)

old_display = """    if( !player_character.activity && ( seen || sees_veh( player_character, veh, true ) ) ) {
        g->invalidate_main_ui_adaptor();
        ui_manager::redraw_invalidated();
        handle_key_blocking_activity();
    }"""
new_display = """    if( !player_character.activity && ( seen || sees_veh( player_character, veh, true ) ) ) {
        g->invalidate_main_ui_adaptor();
#if defined(EMSCRIPTEN) || defined(__EMSCRIPTEN__)
        if( cdda_web_vehicle_move_batch_active && !player_character.has_destination() ) {
            // Keep polling queued input between substeps, but avoid composing
            // and presenting frames that the browser cannot paint yet.
            cdda_web_vehicle_move_redraw_pending = true;
            handle_key_blocking_activity( /* skip_idle_refresh = */ true );
        } else {
            // An immediate destination/modal refresh supersedes any pending batch.
            cdda_web_vehicle_move_redraw_pending = false;
            ui_manager::redraw_invalidated();
            handle_key_blocking_activity();
        }
#else
        ui_manager::redraw_invalidated();
        handle_key_blocking_activity();
#endif
    }"""
if map_source.count(old_display) != 1:
    raise SystemExit("ERROR: vehicle move display block did not match exactly once")
map_source = map_source.replace(old_display, new_display, 1)

old_decl = "void handle_key_blocking_activity();"
new_decl = "void handle_key_blocking_activity( bool skip_idle_refresh = false );"
if header_source.count(old_decl) != 1:
    raise SystemExit("ERROR: handle_key_blocking_activity declaration changed")
header_source = header_source.replace(old_decl, new_decl, 1)

old_signature = "void handle_key_blocking_activity()"
new_signature = "void handle_key_blocking_activity( bool skip_idle_refresh )"
if turn_source.count(old_signature) != 1:
    raise SystemExit("ERROR: handle_key_blocking_activity definition changed")
turn_start = turn_source.index(old_signature)
# The activity patch inserts profiler comments inside the anonymous namespace,
# so the next function is no longer adjacent to the namespace opening. Bound
# this top-level function by its unindented closing brace instead.
import re
closing = re.search(r"(?m)^}[ \\t]*$", turn_source[turn_start:])
if closing is None:
    raise SystemExit("ERROR: handle_key_blocking_activity closing brace not found")
turn_end = turn_start + closing.end()
turn_fn = turn_source[turn_start:turn_end]
if turn_fn.count(old_signature) != 1:
    raise SystemExit("ERROR: ambiguous handle_key_blocking_activity definition")
turn_fn = turn_fn.replace(old_signature, new_signature, 1)
old_idle_refresh = """    } else {
        refresh_display();
        inp_mngr.pump_events();
    }"""
new_idle_refresh = """    } else {
        if( !skip_idle_refresh ) {
            refresh_display();
        }
        inp_mngr.pump_events();
    }"""
if turn_fn.count(old_idle_refresh) != 1:
    raise SystemExit("ERROR: idle display-refresh branch changed")
turn_fn = turn_fn.replace(old_idle_refresh, new_idle_refresh, 1)
turn_source = turn_source[:turn_start] + turn_fn + turn_source[turn_end:]

# Fail closed if the final shape does not keep the batching browser-only and
# if all ordinary callers do not retain the default immediate refresh behavior.
if map_source.count("if( cdda_web_vehicle_move_batch_active && !player_character.has_destination() ) {") != 1:
    raise SystemExit("ERROR: web vehicle movement batching guard missing")
if map_source.count("handle_key_blocking_activity( /* skip_idle_refresh = */ true );") != 1:
    raise SystemExit("ERROR: skipped idle refresh call missing")
if map_source.count("if( cdda_web_outermost_vehicle_move && cdda_web_vehicle_move_redraw_pending ) {") != 1:
    raise SystemExit("ERROR: final vehicle redraw flush missing")
if turn_fn.count("if( !skip_idle_refresh ) {\n            refresh_display();\n        }") != 1:
    raise SystemExit("ERROR: skipped refresh does not preserve default behavior")
if "ui_manager::redraw_invalidated();\n        refresh_display();" not in map_source:
    raise SystemExit("ERROR: final redraw/refresh pair missing")

# Write only after all anchors and invariants have passed.
map_path.write_text(map_source, encoding="utf-8")
turn_path.write_text(turn_source, encoding="utf-8")
header_path.write_text(header_source, encoding="utf-8")
print("PASS: WebAssembly vehicle substeps pump input but batch redraw/present to one final frame")
