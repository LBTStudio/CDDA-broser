# Vehicle collision candidate buffer reservation — 2026-10-27

## Scope

`vehicle::collision()` appends collision candidates to a caller-owned vector. Some calls find no collision at all; others find multiple hits, including rotor/terrain interactions. Reserving at the start of every movement would force allocations even on clear roads, so this change deliberately avoids that design.

Instead, when a real collision is first found and the vector is empty, it reserves up to 16 entries, capped by the vehicle's part count. The `just_detect` branch keeps its existing single-collision behavior and does not pre-reserve. If no collision is found, no reserve call is made and the upstream zero-allocation path remains intact. Larger collision sets can still grow the vector normally.

## Validation

`bench/vehicle_collision_buffer_test.py` compares the exact 0.I `vehicle::collision()` function and normalizes only the two lazy-reserve sites. It compiles an allocation-count fixture covering no-hit movement, a single hit, and multi-hit cases with 8–512 candidates.

The synthetic benchmark demonstrates only vector capacity behavior. It is not an end-to-end vehicle-driving timing or Chromebook performance claim. The current Playwright real-bundle suite does not yet spawn and drive a controlled vehicle, and this PR does not claim a full WebAssembly product build.

## Trade-off

For a single collision on a large vehicle, reserving 16 entries can request more bytes than a single geometric vector growth would. The 16-entry cap bounds that overhead while reducing repeated reallocations in multi-hit scenarios. If real-vehicle profiling shows that multi-hit cases are not a bottleneck, this optimization should not be retained solely on the synthetic benchmark.
