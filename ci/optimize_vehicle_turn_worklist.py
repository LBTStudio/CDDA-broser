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

old_zone_cache = """    // refresh vehicle zones for moved vehicles
    zone_manager::get_manager().cache_vzones( this );"""
new_zone_cache = """    // Vehicles can cross z-levels during one turn. A moved vehicle is removed from
    // its old zone cache and inserted into its destination cache before this point,
    // so checking only the player's z-level can miss the dirty vehicle entirely.
    // Refresh dirty zone vehicles across loaded levels, then rebuild the active
    // zone point set once. get_cache_lazy avoids creating unused level caches.
    bool vehicle_zones_dirty = false;
    for( int zlev = -OVERMAP_DEPTH; zlev <= OVERMAP_HEIGHT; ++zlev ) {
        const level_cache *cache = get_cache_lazy( zlev );
        if( cache == nullptr ) {
            continue;
        }
        for( vehicle *veh : cache->zone_vehicles ) {
            if( veh->refresh_zones() ) {
                vehicle_zones_dirty = true;
            }
        }
    }
    if( zone_manager::get_manager().vehicle_zone_cache_dirty() ) {
        vehicle_zones_dirty = true;
    }
    if( vehicle_zones_dirty ) {
        zone_manager::get_manager().cache_vzones( this );
    }"""
if source.count(old_zone_cache) != 1:
    raise SystemExit("ERROR: expected unconditional vehicle-zone refresh did not match exactly once")
source = source.replace(old_zone_cache, new_zone_cache, 1)

# Do not let map::get_vehicle_zones recursively rebuild the cache while
# zone_manager::cache_vzones is already rebuilding it.
header_path = path.with_name("map.h")
header = header_path.read_text(encoding="utf-8")
old_header_decl = "        std::vector<zone_data *> get_vehicle_zones( int zlev );"
new_header_decl = "        std::vector<zone_data *> get_vehicle_zones( int zlev, bool refresh_cache = true );"
if header.count(old_header_decl) != 1:
    raise SystemExit("ERROR: expected map::get_vehicle_zones declaration did not match exactly once")
header = header.replace(old_header_decl, new_header_decl, 1)

old_get_zones_signature = "std::vector<zone_data *> map::get_vehicle_zones( const int zlev )"
new_get_zones_signature = "std::vector<zone_data *> map::get_vehicle_zones( const int zlev, bool refresh_cache )"
if source.count(old_get_zones_signature) != 1:
    raise SystemExit("ERROR: expected map::get_vehicle_zones definition did not match exactly once")
source = source.replace(old_get_zones_signature, new_get_zones_signature, 1)
old_recursive_refresh = """    if( rebuild ) {
        zone_manager::get_manager().cache_vzones();
    }"""
new_recursive_refresh = """    if( rebuild && refresh_cache ) {
        zone_manager::get_manager().cache_vzones();
    }"""
if source.count(old_recursive_refresh) != 1:
    raise SystemExit("ERROR: expected vehicle-zone recursive refresh did not match exactly once")
source = source.replace(old_recursive_refresh, new_recursive_refresh, 1)

zone_manager_path = path.with_name("clzones.cpp")
zone_manager_source = zone_manager_path.read_text(encoding="utf-8")
zone_header_path = path.with_name("clzones.h")
zone_header = zone_header_path.read_text(encoding="utf-8")
old_zone_cache_field = """        std::unordered_map<std::string, std::unordered_set<tripoint_abs_ms>> vzone_cache;
        std::unordered_set<tripoint_abs_ms> get_point_set( const zone_type_id &type,"""
new_zone_cache_field = """        std::unordered_map<std::string, std::unordered_set<tripoint_abs_ms>> vzone_cache;
        bool vzone_cache_dirty_ = false; // NOLINT(cata-serialize)
        std::unordered_set<tripoint_abs_ms> get_point_set( const zone_type_id &type,"""
if zone_header.count(old_zone_cache_field) != 1:
    raise SystemExit("ERROR: expected vehicle-zone cache member did not match exactly once")
zone_header = zone_header.replace(old_zone_cache_field, new_zone_cache_field, 1)
old_zone_cache_getter = "        void cache_vzones( map *pmap = nullptr );"
new_zone_cache_getter = old_zone_cache_getter + "\n        bool vehicle_zone_cache_dirty() const { return vzone_cache_dirty_; }"
if zone_header.count(old_zone_cache_getter) != 1:
    raise SystemExit("ERROR: expected cache_vzones declaration did not match exactly once")
zone_header = zone_header.replace(old_zone_cache_getter, new_zone_cache_getter, 1)

old_clear = """void zone_manager::clear()
{
    zones.clear();
    added_vzones.clear();
    changed_vzones.clear();
    removed_vzones.clear();
    // Do not clear types since it is needed for the next games.
    area_cache.clear();
    vzone_cache.clear();
}"""
new_clear = old_clear.replace("    vzone_cache.clear();", "    vzone_cache.clear();\n    vzone_cache_dirty_ = false;")
if zone_manager_source.count(old_clear) != 1:
    raise SystemExit("ERROR: expected zone_manager::clear body did not match exactly once")
zone_manager_source = zone_manager_source.replace(old_clear, new_clear, 1)

old_edited = """void zone_manager::zone_edited( zone_data &zone )
{
    if( zone.get_is_vehicle() ) {
        //Check if this zone has already been stored"""
new_edited = """void zone_manager::zone_edited( zone_data &zone )
{
    if( zone.get_is_vehicle() ) {
        // Geometry can be clean while an edit changes cache participation.
        vzone_cache_dirty_ = true;
        //Check if this zone has already been stored"""
if zone_manager_source.count(old_edited) != 1:
    raise SystemExit("ERROR: expected zone_edited body did not match exactly once")
zone_manager_source = zone_manager_source.replace(old_edited, new_edited, 1)

old_zone_call = "    auto vzones = here.get_vehicle_zones( here.get_abs_sub().z() );"
new_zone_call = "    auto vzones = here.get_vehicle_zones( here.get_abs_sub().z(), /* refresh_cache = */ false );"
zone_fn_signature = "void zone_manager::cache_vzones( map *pmap )"
next_fn_signature = "std::unordered_set<tripoint_abs_ms> zone_manager::get_point_set("
if zone_manager_source.count(zone_fn_signature) != 1 or zone_manager_source.count(next_fn_signature) != 1:
    raise SystemExit("ERROR: cache_vzones function boundaries changed")
zone_fn_start = zone_manager_source.index(zone_fn_signature)
zone_fn_end = zone_manager_source.index(next_fn_signature, zone_fn_start)
zone_fn = zone_manager_source[zone_fn_start:zone_fn_end]
if zone_fn.count(old_zone_call) != 1:
    raise SystemExit("ERROR: cache_vzones-local source anchor did not match exactly once")
zone_fn = zone_fn.replace(old_zone_call, new_zone_call, 1)
if "vzone_cache_dirty_ = false;" not in zone_fn:
    pos = zone_fn.rfind("}")
    if pos < 0:
        raise SystemExit("ERROR: could not find cache_vzones final brace")
    zone_fn = zone_fn[:pos] + "    vzone_cache_dirty_ = false;\n" + zone_fn[pos:]
zone_manager_source = (
    zone_manager_source[:zone_fn_start] + zone_fn + zone_manager_source[zone_fn_end:]
)

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
if source.count("bool vehicle_zones_dirty = false;") != 1:
    raise SystemExit("ERROR: cross-level vehicle-zone dirty scan is missing")
if "for( int zlev = -OVERMAP_DEPTH; zlev <= OVERMAP_HEIGHT; ++zlev ) {" not in source:
    raise SystemExit("ERROR: vehicle-zone scan does not cover every valid z-level")
if "const level_cache *cache = get_cache_lazy( zlev );" not in source:
    raise SystemExit("ERROR: cross-level vehicle-zone scan must not allocate unloaded level caches")
if "if( veh->refresh_zones() ) {" not in source:
    raise SystemExit("ERROR: dirty vehicle-zone refresh is missing")
if "if( zone_manager::get_manager().vehicle_zone_cache_dirty() ) {" not in source:
    raise SystemExit("ERROR: vehicle-zone edit dirty flag check is missing")
if "if( vehicle_zones_dirty ) {" not in source:
    raise SystemExit("ERROR: vehicle-zone cache rebuild is not conditional on actual refresh")
if source.count("if( rebuild && refresh_cache ) {") != 1:
    raise SystemExit("ERROR: nested vehicle-zone cache rebuild is still unconditional")
if "std::vector<zone_data *> map::get_vehicle_zones( const int zlev, bool refresh_cache )" not in source:
    raise SystemExit("ERROR: explicit vehicle-zone refresh control is missing")
if "here.get_vehicle_zones( here.get_abs_sub().z(), /* refresh_cache = */ false );" not in zone_manager_source:
    raise SystemExit("ERROR: cache_vzones can still recursively request its own rebuild")

# Write only after all source anchors have been checked and all transforms succeeded.
path.write_text(source, encoding="utf-8")
header_path.write_text(header, encoding="utf-8")
zone_manager_path.write_text(zone_manager_source, encoding="utf-8")
zone_header_path.write_text(zone_header, encoding="utf-8")
print("PASS: vehicle turn worklist now uses contiguous storage with std::map-compatible order and merge semantics")
