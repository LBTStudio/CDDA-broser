# Vehicle loot-zone cache invalidation across z-levels — 2026-10-27

## Finding

The vehicle-turn optimization previously replaced the unconditional end-of-turn
`zone_manager::cache_vzones(this)` with a check of only
`check_vehicle_zones(get_abs_sub().z())`.

That is not a sufficient invalidation condition for a vehicle that changes
z-level during the turn. `map::displace_vehicle()` removes the vehicle from its
source `level_cache::zone_vehicles`, adds it to the destination cache, and sets
`zones_dirty`. At end of turn the active z-level can therefore have no dirty
vehicle, while the destination level still contains the dirty vehicle. The
active-zone cache can then retain points from the old vehicle location.

## Fix

- Iterate the valid z-level range using `get_cache_lazy()`, so unloaded levels
  are skipped rather than allocated.
- Call `refresh_zones()` for zone-bearing vehicles in every loaded level.
  This consumes dirty state for a vehicle that moved out of the active level;
  otherwise the global cache would either stay stale or be rebuilt every turn
  because the off-level dirty flag remained set.
- Rebuild the active vehicle-zone point cache once, only if at least one zone
  was actually refreshed.
- Keep the final browser redraw after cache maintenance.

## Regression coverage

`bench/vehicle_zone_cache_test.py` source-checks the actual patched
`map::vehmove()` structure and compiles a focused state-transition fixture.
It tests moving a zone vehicle out of the active level, returning it, multiple
dirty levels in one turn, no-op clean turns, and absent/unloaded level caches.
The existing worklist and redraw-batching tests continue to check source
normalization and ordering.

These are focused source/fixture regression tests. They do not replace a
full WebAssembly build or a real-game vehicle-driving scenario on a 4GB
Chromebook; those remain separate validation gaps.
