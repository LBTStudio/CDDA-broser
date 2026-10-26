#!/usr/bin/env python3
"""Source guards for the browser-only vehicle movement redraw batch.

The browser runs vehicle substeps synchronously; it cannot paint between those
substeps. This test protects the optimization's scope and keeps event pumping
and all ordinary/native refresh paths intact.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
map_source = (root / "src/map.cpp").read_text(encoding="utf-8")
turn_source = (root / "src/do_turn.cpp").read_text(encoding="utf-8")
header_source = (root / "src/do_turn.h").read_text(encoding="utf-8")


def between(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


assert map_source.count("static bool cdda_web_vehicle_move_batch_active = false;") == 1
assert map_source.count("static bool cdda_web_vehicle_move_redraw_pending = false;") == 1
assert map_source.count(
    "if( cdda_web_vehicle_move_batch_active && !player_character.has_destination() ) {"
) == 1

vehmove = between(
    map_source,
    "void map::vehmove()",
    "bool map::vehproceed( VehicleList &vehicle_list )",
)
assert "cdda_web_vehicle_move_batch_active = true;" in vehmove
assert "cdda_web_vehicle_move_redraw_pending = false;" in vehmove
assert "if( cdda_web_outermost_vehicle_move && cdda_web_vehicle_move_redraw_pending ) {" in vehmove
assert "ui_manager::redraw_invalidated();\n        refresh_display();" in vehmove
assert vehmove.index("check_vehicle_zones( get_abs_sub().z() )") < vehmove.index(
    "ui_manager::redraw_invalidated();\n        refresh_display();"
), "final display must follow vehicle-zone cache refresh"

move_vehicle = between(
    map_source,
    "vehicle *map::move_vehicle( vehicle &veh",
    "int map::vehicle_vehicle_collision(",
)
deferred = """cdda_web_vehicle_move_redraw_pending = true;
            handle_key_blocking_activity( /* skip_idle_refresh = */ true );"""
assert deferred in move_vehicle
assert "#else\n        ui_manager::redraw_invalidated();\n        handle_key_blocking_activity();\n#endif" in move_vehicle
assert "cdda_web_vehicle_move_redraw_pending = false;\n            ui_manager::redraw_invalidated();" in move_vehicle
assert move_vehicle.count("handle_key_blocking_activity( /* skip_idle_refresh = */ true );") == 1

assert header_source.count(
    "void handle_key_blocking_activity( bool skip_idle_refresh = false );"
) == 1
handle = between(
    turn_source,
    "void handle_key_blocking_activity( bool skip_idle_refresh )",
    "namespace\n{\nvoid monmove()",
)
assert "if( !skip_idle_refresh ) {\n            refresh_display();\n        }" in handle
assert "inp_mngr.pump_events();" in handle
# Blocking activity/destination refreshes must remain unconditional.
assert "if( refresh ) {\n            ui_manager::redraw();\n            refresh_display();" in handle

print("PASS: WebAssembly vehicle substeps defer redraws, retain event pumping, and flush once at turn end")
