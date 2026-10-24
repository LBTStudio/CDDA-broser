#!/usr/bin/env python3
"""Avoid heap allocation for bounded unique vehicle-submap sets.

The active map normally has at most 121 submaps per z-level. Store up to 256
unique coordinates in a bounded open-addressing stack table; only pathological
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

old = """    // Keep the original heap-backed path for large populations.
    std::set<tripoint> positions;
    for( vehicle *this_vehicle : vehicles ) {
        positions.emplace( submap_position( this_vehicle ) );
    }
    for( const tripoint &pos : positions ) {
        process( pos );
    }"""
new = """    // Most single-level maps have at most 121 submaps. Use bounded open
    // addressing to deduplicate vehicle positions without tree nodes or an
    // N-sized vector, then sort the unique coordinates for set-compatible order.
    constexpr std::size_t unique_capacity = 256;
    constexpr std::size_t table_capacity = 512;
    std::array<tripoint, unique_capacity> positions{};
    std::array<int, table_capacity> indices{};
    indices.fill( -1 );
    std::size_t count = 0;
    bool overflow = false;
    for( vehicle *this_vehicle : vehicles ) {
        const tripoint candidate = submap_position( this_vehicle );
        const std::size_t hx = static_cast<unsigned int>( candidate.x ) * 73856093u;
        const std::size_t hy = static_cast<unsigned int>( candidate.y ) * 19349663u;
        const std::size_t hz = static_cast<unsigned int>( candidate.z ) * 83492791u;
        std::size_t bucket = ( hx ^ hy ^ hz ) % table_capacity;
        bool handled = false;
        for( std::size_t probe = 0; probe < table_capacity; ++probe ) {
            const int existing_index = indices[bucket];
            if( existing_index < 0 ) {
                if( count == unique_capacity ) {
                    overflow = true;
                    break;
                }
                positions[count] = candidate;
                indices[bucket] = static_cast<int>( count );
                ++count;
                handled = true;
                break;
            }
            const tripoint &existing = positions[static_cast<std::size_t>( existing_index )];
            if( existing.x == candidate.x && existing.y == candidate.y &&
                existing.z == candidate.z ) {
                handled = true;
                break;
            }
            bucket = ( bucket + 1 ) % table_capacity;
        }
        if( overflow ) {
            break;
        }
        if( !handled ) {
            overflow = true;
            break;
        }
    }
    if( !overflow ) {
        const auto last = positions.begin() + count;
        std::sort( positions.begin(), last );
        for( auto iter = positions.begin(); iter != last; ++iter ) {
            process( *iter );
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
if method.count(old) != 1:
    raise SystemExit("ERROR: large vehicle-submap set block did not match exactly once")
method = method.replace(old, new, 1)
if "std::set<tripoint> positions;" in method:
    raise SystemExit("ERROR: tree-backed large-vehicle path remains after transform")
if "unique_capacity = 256" not in method:
    raise SystemExit("ERROR: bounded unique-position buffer missing")
if "table_capacity = 512" not in method or "indices.fill( -1 );" not in method:
    raise SystemExit("ERROR: bounded open-addressing table missing")
if "fallback.reserve( vehicles.size() );" not in method:
    raise SystemExit("ERROR: overflow fallback missing")
source = source[:start] + method + source[end:]
path.write_text(source, encoding="utf-8")
print("PASS: vehicle-submap path uses bounded open-addressing storage with overflow fallback")
