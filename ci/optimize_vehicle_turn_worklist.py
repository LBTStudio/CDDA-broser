#!/usr/bin/env python3
"""Replace map::vehmove's per-turn tree worklist with a sorted contiguous list.

The worklist preserves std::map<vehicle *, bool>'s pointer order and its
"on-map wins over connected-only" rule.  The conversion is source-anchored:
unknown upstream changes fail closed rather than rewriting a different block.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")

signature = "void map::vehmove()"
if source.count(signature) != 1:
    raise SystemExit("ERROR: expected exactly one map::vehmove() definition")

helper_signature = "static void for_each_unique_vehicle_turn_work_item("
if helper_signature in source:
    raise SystemExit("ERROR: vehicle turn-worklist helper already exists")

old = """    std::map<vehicle *, bool> vehs; // value true means in on map
    std::unordered_set<vehicle *> connected_vehs;
    for( int zlev = minz; zlev <= maxz; ++zlev ) {
        const level_cache *cache = get_cache_lazy( zlev );
        if( !cache ) {
            continue;
        }
        for( vehicle *veh : cache->vehicle_list ) {
            vehs[veh] = true; // force on map vehicles to true
            veh->get_connected_vehicles( *this, connected_vehs );
        }
    }
    for( vehicle *connected_veh : connected_vehs ) {
        vehs.emplace( connected_veh, false ); // add with 'false' if does not exist (off map)
    }
    for( const std::pair<vehicle *const, bool> &veh_pair : vehs ) {
        veh_pair.first->process_effects();
        veh_pair.first->recalculate_enchantment_cache();
        veh_pair.first->idle( *this, /* on_map = */ veh_pair.second );
    }"""

new = """    // A tree node per vehicle caused avoidable allocation churn on every turn.
    // Collect on-map vehicles first, then add connected off-map vehicles below.
    std::vector<std::pair<vehicle *, bool>> vehs;
    vehs.reserve( vehicle_list.size() );
    std::unordered_set<vehicle *> connected_vehs;
    for( int zlev = minz; zlev <= maxz; ++zlev ) {
        const level_cache *cache = get_cache_lazy( zlev );
        if( !cache ) {
            continue;
        }
        for( vehicle *veh : cache->vehicle_list ) {
            vehs.emplace_back( veh, true );
            veh->get_connected_vehicles( *this, connected_vehs );
        }
    }
    // Usually the first reserve already covers this. If a large connected
    // convoy extends beyond it, grow at most once before appending that set.
    vehs.reserve( vehs.size() + connected_vehs.size() );
    for( vehicle *connected_veh : connected_vehs ) {
        vehs.emplace_back( connected_veh, false );
    }
    for_each_unique_vehicle_turn_work_item( vehs, [this]( vehicle *veh, bool on_map ) {
        veh->process_effects();
        veh->recalculate_enchantment_cache();
        veh->idle( *this, /* on_map = */ on_map );
    } );"""

if source.count(old) != 1:
    raise SystemExit("ERROR: expected vehicle worklist block did not match exactly once")
source = source.replace(old, new, 1)

helper = """// Match std::map<vehicle *, bool>'s pointer ordering and merge semantics
// without allocating one red-black-tree node per vehicle on every turn.
template<typename WorkRange, typename ProcessFn>
static void for_each_unique_vehicle_turn_work_item( WorkRange &work, ProcessFn process )
{
    std::sort( work.begin(), work.end(), []( const auto &lhs, const auto &rhs ) {
        return std::less<vehicle *>{}( lhs.first, rhs.first );
    } );

    auto iter = work.begin();
    while( iter != work.end() ) {
        vehicle *const current = iter->first;
        bool on_map = false;
        do {
            // map::operator[] sets in-map entries to true, and emplace(false)
            // never overwrote that value when the same pointer was connected.
            on_map = on_map || iter->second;
            ++iter;
        } while( iter != work.end() && iter->first == current );
        process( current, on_map );
    }
}

"""

signature_pos = source.index(signature)
source = source[:signature_pos] + helper + source[signature_pos:]

include = "#include <functional>\n"
if include not in source:
    anchor = "#include <cstdlib>\n"
    if source.count(anchor) != 1:
        raise SystemExit("ERROR: cannot safely add the <functional> include")
    source = source.replace(anchor, anchor + include, 1)

if "std::map<vehicle *, bool> vehs;" in source:
    raise SystemExit("ERROR: old tree-backed vehicle worklist remains")
if source.count(helper_signature) != 1:
    raise SystemExit("ERROR: expected exactly one new vehicle worklist helper")
if source.count("for_each_unique_vehicle_turn_work_item( vehs,") != 1:
    raise SystemExit("ERROR: optimized worklist is not called exactly once")
if "veh->process_effects();\n        veh->recalculate_enchantment_cache();\n        veh->idle( *this, /* on_map = */ on_map );" not in source:
    raise SystemExit("ERROR: vehicle post-movement processing order changed")

path.write_text(source, encoding="utf-8")
print("PASS: vehicle turn worklist now uses contiguous storage with std::map-compatible order and merge semantics")
