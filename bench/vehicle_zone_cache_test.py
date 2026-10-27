#!/usr/bin/env python3
"""Regression test for vehicle loot-zone caches when vehicles cross z-levels.

The source guard ensures CI tests the production turn-end invalidation path.
The compiled fixture reproduces the relevant cache state transitions:
source-level eviction, destination-level dirty state, active-zone rebuild,
and a subsequent no-op turn. It is a focused regression fixture, not a full
CDDA/WebAssembly end-to-end simulation.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(sys.argv[1]).resolve()
map_source = (root / "src/map.cpp").read_text(encoding="utf-8")


def function(source: str, signature: str, next_signature: str) -> str:
    start = source.index(signature)
    end = source.index(next_signature, start)
    text = source[start:end]
    opening = text.index("\n{\n") + 1
    depth, pos = 1, opening + 1
    while depth:
        depth += (text[pos] == "{") - (text[pos] == "}")
        pos += 1
    return text[:pos]


vehmove = function(
    map_source,
    "void map::vehmove()",
    "bool map::vehproceed( VehicleList &vehicle_list )",
)
assert vehmove.count("bool vehicle_zones_dirty = false;") == 1, (
    "vehicle::vehmove must perform one cross-z vehicle-zone dirty scan"
)
scan_start = vehmove.index("bool vehicle_zones_dirty = false;")
scan_end = vehmove.index("if( vehicle_zones_dirty )", scan_start)
scan = vehmove[scan_start:scan_end]
assert "for( int zlev = -OVERMAP_DEPTH; zlev <= OVERMAP_HEIGHT; ++zlev )" in scan
assert "const level_cache *cache = get_cache_lazy( zlev );" in scan, (
    "the scan must not allocate caches for unloaded z-levels"
)
assert "if( cache == nullptr )" in scan
assert "for( vehicle *veh : cache->zone_vehicles )" in scan
assert "if( veh->refresh_zones() )" in scan
assert "vehicle_zones_dirty = true;" in scan
assert "check_vehicle_zones( get_abs_sub().z() )" not in vehmove, (
    "checking only the active z-level can miss a vehicle that moved vertically"
)
rebuild_start = vehmove.index("if( vehicle_zones_dirty )", scan_start)
rebuild_end = vehmove.index("}", rebuild_start)
rebuild = vehmove[rebuild_start:rebuild_end]
assert "zone_manager::get_manager().cache_vzones( this );" in rebuild
assert scan_start < rebuild_start
assert vehmove.index("if( cdda_web_outermost_vehicle_move && cdda_web_vehicle_move_redraw_pending )") > rebuild_start, (
    "the final browser redraw must remain after zone cache refresh"
)

program = r"""
#include <array>
#include <cassert>
#include <cstdio>
#include <set>
#include <vector>

struct Vehicle {
    int zone_point = 0;
    bool zones_dirty = false;
    int refresh_calls = 0;

    bool refresh_zones() {
        if (!zones_dirty) return false;
        zones_dirty = false;
        ++refresh_calls;
        return true;
    }
};
struct LevelCache {
    std::vector<Vehicle *> zone_vehicles;
};
struct GlobalZoneCache {
    std::set<int> points;
    int rebuilds = 0;
};

static bool finish_vehicle_turn(LevelCache *levels[3], const int active_z,
                                GlobalZoneCache &global) {
    bool vehicle_zones_dirty = false;
    for (int z = -1; z <= 1; ++z) {
        LevelCache *cache = levels[z + 1];
        if (cache == nullptr) continue;
        for (Vehicle *veh : cache->zone_vehicles) {
            if (veh->refresh_zones()) {
                vehicle_zones_dirty = true;
            }
        }
    }
    if (vehicle_zones_dirty) {
        global.points.clear();
        if (levels[active_z + 1] != nullptr) {
            for (Vehicle *veh : levels[active_z + 1]->zone_vehicles) {
                global.points.insert(veh->zone_point);
            }
        }
        ++global.rebuilds;
    }
    return vehicle_zones_dirty;
}

int main() {
    LevelCache below, active, above;
    LevelCache *levels[3] = { &below, &active, &above };
    GlobalZoneCache global;
    Vehicle cart{10, false, 0};

    // A clean turn must not recache.
    active.zone_vehicles.push_back(&cart);
    global.points.insert(10);
    assert(!finish_vehicle_turn(levels, 0, global));
    assert(global.rebuilds == 0 && global.points == std::set<int>{10});

    // Move the zone-carrying vehicle out of the active z-level. The old
    // implementation checks only 'active', sees no dirty vehicle, and leaves
    // point 10 stale. The fixed path finds the dirty vehicle in 'above'.
    active.zone_vehicles.clear();
    above.zone_vehicles.push_back(&cart);
    cart.zone_point = 110;
    cart.zones_dirty = true;
    assert(finish_vehicle_turn(levels, 0, global));
    assert(!cart.zones_dirty && cart.refresh_calls == 1);
    assert(global.rebuilds == 1 && global.points.empty());
    assert(global.points.count(10) == 0);

    // The following turn must not keep rebuilding because the out-of-level
    // vehicle's dirty flag was consumed while scanning the destination level.
    assert(!finish_vehicle_turn(levels, 0, global));
    assert(global.rebuilds == 1 && cart.refresh_calls == 1);

    // Moving the same vehicle back into the active level re-adds its current
    // point and removes any obsolete location in one rebuild.
    above.zone_vehicles.clear();
    active.zone_vehicles.push_back(&cart);
    cart.zone_point = 99;
    cart.zones_dirty = true;
    assert(finish_vehicle_turn(levels, 0, global));
    assert(global.rebuilds == 2 && cart.refresh_calls == 2);
    assert(global.points == std::set<int>{99});
    assert(global.points.count(10) == 0 && global.points.count(110) == 0);

    // Dirty active and off-level vehicles in the same turn refresh together
    // but rebuild the global active-z cache once.
    Vehicle active_cart{100, true, 0};
    Vehicle far_cart{200, true, 0};
    active.zone_vehicles.push_back(&active_cart);
    below.zone_vehicles.push_back(&far_cart);
    assert(finish_vehicle_turn(levels, 0, global));
    assert(!active_cart.zones_dirty && !far_cart.zones_dirty);
    assert(active_cart.refresh_calls == 1 && far_cart.refresh_calls == 1);
    assert(global.rebuilds == 3);
    assert(global.points == std::set<int>({99, 100}));

    // A missing/unloaded level is skipped rather than created or dereferenced.
    // An object whose cache is genuinely unloaded is not in the runtime scan.
    levels[0] = nullptr;
    far_cart.zones_dirty = true;
    assert(!finish_vehicle_turn(levels, 0, global));
    assert(far_cart.zones_dirty && global.rebuilds == 3);

    std::puts("PASS vehicle-zone regression: cross-z eviction, return, one rebuild per dirty turn, clean-turn no-op, unloaded-level guard");
}
"""

with tempfile.TemporaryDirectory(prefix="vehicle-zone-cache-") as tmp:
    cpp = Path(tmp) / "test.cpp"
    binary = Path(tmp) / "test"
    cpp.write_text(program, encoding="utf-8")
    subprocess.run(
        ["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
         str(cpp), "-o", str(binary)],
        check=True, timeout=60,
    )
    subprocess.run([str(binary)], check=True, timeout=30)

print("PASS vehicle-zone source contract and cross-z dirty-cache regression")
