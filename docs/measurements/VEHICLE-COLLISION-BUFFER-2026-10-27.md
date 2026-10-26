# Vehicle collision candidate buffer reservation — 2026-10-27

## Scope

`map::move_vehicle()` creates a fresh `std::vector<veh_collision>` for each movement step. It then fills that vector through `vehicle::collision()`, potentially repeats collision detection after an impact, and clears/reuses the same vector during retries. Without an initial capacity, a multi-collision vehicle can cause geometric vector growth and temporary allocation churn on every movement step.

This change reserves `veh.part_count()` entries once per call. It does not skip collision checks, alter collision iteration order, change damage handling, or persist/reuse collision records across different vehicles. If a rare collision scenario produces more records than vehicle parts, the vector can still grow normally.

## Validation

`bench/vehicle_collision_buffer_test.py` checks the exact upstream 0.I function and requires that this is the only change within `map::move_vehicle()` from this transform. A standalone allocation benchmark compares the same vector insertion sequence with and without a capacity reservation for 0–512 synthetic collision records.

The synthetic benchmark establishes only the expected capacity-allocation reduction; it is not an end-to-end vehicle driving timing. The existing real-bundle Playwright baseline does not currently spawn or drive a vehicle, so it cannot be used to claim a Chromebook driving speedup. That requires a repeatable vehicle-control fixture and full WebAssembly build.

## Expected trade-off

The reservation allocates space proportional to the vehicle's part count even when there are no collisions. This trades one appropriately sized allocation at the start of a moving vehicle step for potentially several geometric expansions if collisions occur. The allocation benchmark and subsequent CI results should be reviewed before merging; this change remains in a separate PR from the already merged redraw batching / per-turn worklist work.
