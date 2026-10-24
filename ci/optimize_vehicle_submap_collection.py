#!/usr/bin/env python3
"""Avoid heap allocation for bounded unique vehicle-submap sets.

The active map normally has at most 121 submaps per z-level. Store up to 256
unique coordinates in sorted stack storage; only pathological out-of-range
inputs fall back to a vector sorted and deduplicated in contiguous memory.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
signature = "static void for_each_unique_vehicle_submap("
if source.count(signature) != 1:
    raise SystemExit("ERROR: expected exactly one vehicle-submap helper")

start = source.index(signature)
opening = source.index("{", start)
depth, end = 1, opening + 1
while depth:
    depth += (source[end] == "{") - (source[end] == "}")
    end += 1
method = source[start:end]

old = """    // A single z-level normally contains <= 121 submaps. Keep its unique
    // coordinates sorted in bounded stack storage so duplicate-heavy vehicle
    // clusters do not reserve a vector proportional to vehicle count.
    constexpr std::size_t unique_capacity = 256;
    std::array<tripoint, unique_capacity> positions{};
    std::size_t count = 0;
    bool overflow = false;
    for( vehicle *this_vehicle : vehicles ) {
        const tripoint candidate = submap_position( this_vehicle );
        const auto first = positions.begin();
        const auto last = first + count;
        const auto iter = std::lower_bound( first, last, candidate );
        if( iter != last && !( candidate < *iter ) && !( *iter < candidate ) ) {
            continue;
        }
        if( count == unique_capacity ) {
            overflow = true;
            break;
        }
        std::move_backward( iter, last, last + 1 );
        *iter = candidate;
        ++count;
    }
    if( !overflow ) {
        for( std::size_t i = 0; i < count; ++i ) {
            process( positions[i] );
        }
        return;
    }

    // Defensive fallback for unexpected coordinate ranges/modified map sizes.
    std::vector<tripoint> fallback;
    fallback.reserve( vehicles.size() );
    for( vehicle *this_vehicle : vehicles ) {
        fallback.push_back( submap_position( this_vehicle ) );
    }
    std::sort( fallback.begin(), fallback.end() );
    const auto unique_end = std::unique( fallback.begin(), fallback.end(),
    []( const tripoint &lhs, const tripoint &rhs ) {
        return lhs.x == rhs.x && lhs.y == rhs.y && lhs.z == rhs.z;
    } );
    fallback.erase( unique_end, fallback.end() );
    for( const tripoint &pos : fallback ) {
        process( pos );
    }"""
new = """    // For large populations, keep coordinates contiguous and sort once.
    // This preserves the set's order without allocating one tree node per key.
    std::vector<tripoint> positions;
    positions.reserve( vehicles.size() );
    for( vehicle *this_vehicle : vehicles ) {
        positions.push_back( submap_position( this_vehicle ) );
    }
    std::sort( positions.begin(), positions.end() );
    const auto unique_end = std::unique( positions.begin(), positions.end(),
    []( const tripoint &lhs, const tripoint &rhs ) {
        return lhs.x == rhs.x && lhs.y == rhs.y && lhs.z == rhs.z;
    } );
    positions.erase( unique_end, positions.end() );
    for( const tripoint &pos : positions ) {
        process( pos );
    }"""
if method.count(old) != 1:
    raise SystemExit("ERROR: large vehicle-submap set block did not match exactly once")
method = method.replace(old, new, 1)
if "std::set<tripoint> positions;" in method:
    raise SystemExit("ERROR: tree-backed large-vehicle path remains after transform")
if "unique_capacity = 256" not in method:
    raise SystemExit("ERROR: bounded unique-position buffer missing")
if "std::lower_bound( first, last, candidate )" not in method:
    raise SystemExit("ERROR: sorted deduplication lookup missing")
if "fallback.reserve( vehicles.size() );" not in method:
    raise SystemExit("ERROR: overflow fallback missing")
source = source[:start] + method + source[end:]
path.write_text(source, encoding="utf-8")
print("PASS: vehicle-submap path uses bounded sorted stack storage with overflow fallback")
