#!/usr/bin/env python3
"""Differential test for map::vehmove's post-movement vehicle work list.

Extracts the exact helper applied by CI, compiles it in a small C++ fixture,
and compares pointer order / on-map precedence against the original std::map
behavior while counting heap allocations. These are subsystem results, not
end-to-end driving performance claims.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]).resolve()


def original(path):
    return subprocess.check_output(
        ["git", "-C", str(source), "show", "HEAD:src/" + path], text=True)


def function(text, signature):
    start = text.index(signature)
    # Function bodies begin on their own line; this avoids mistaking a lambda
    # brace in the function body for the start of the function itself.
    opening = text.index("\n{\n", start) + 1
    depth, pos = 1, opening + 1
    while depth:
        depth += (text[pos] == "{") - (text[pos] == "}")
        pos += 1
    return text[start:pos]


old_map = original("map.cpp")
new_map = (source / "src/map.cpp").read_text(encoding="utf-8")
old_turn = function(old_map, "void map::vehmove()")
new_turn = function(new_map, "void map::vehmove()")

old_block = """    std::map<vehicle *, bool> vehs; // value true means in on map
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

new_block = """    // A tree node per vehicle caused avoidable allocation churn on every turn.
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

assert old_block in old_turn, "upstream baseline vehicle worklist changed"
assert new_block in new_turn, "optimized vehicle worklist block missing"

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
assert old_zone_cache in old_turn, "upstream vehicle-zone refresh block changed"
assert new_zone_cache in new_turn, "dirty-guarded vehicle-zone refresh missing"
# The browser-only redraw batching is allowed to wrap the entry/exit of
# map::vehmove; normalize those two blocks as well before checking the rest.
new_entry = """void map::vehmove()
{
#if defined(EMSCRIPTEN) || defined(__EMSCRIPTEN__)
    const bool cdda_web_outermost_vehicle_move = !cdda_web_vehicle_move_batch_active;
    if( cdda_web_outermost_vehicle_move ) {
        cdda_web_vehicle_move_redraw_pending = false;
    }
    cdda_web_vehicle_move_batch_active = true;
#endif
    // give vehicles movement points"""
old_entry = """void map::vehmove()
{
    // give vehicles movement points"""
assert new_entry in new_turn, "browser vehicle-move entry batch missing"
normalized_turn = new_turn.replace(new_entry, old_entry, 1)
new_zone_with_flush = new_zone_cache + """

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
assert new_zone_with_flush in normalized_turn, "browser vehicle-move exit batch missing"
normalized_turn = normalized_turn.replace(new_zone_with_flush, old_zone_cache, 1)
normalized_turn = normalized_turn.replace(new_block, old_block, 1).replace(
    new_zone_cache, old_zone_cache, 1)
assert normalized_turn == old_turn, (
    "map::vehmove changed outside the approved worklist, zone-cache, and redraw-batch blocks")
assert "bool vehicle_zones_dirty = false;" in new_turn
assert "for( int zlev = -OVERMAP_DEPTH; zlev <= OVERMAP_HEIGHT; ++zlev ) {" in new_zone_cache
assert "const level_cache *cache = get_cache_lazy( zlev );" in new_zone_cache
assert "if( veh->refresh_zones() ) {" in new_zone_cache
assert "if( vehicle_zones_dirty ) {" in new_zone_cache
assert "zone_manager::get_manager().cache_vzones( this );" in new_zone_cache

# The recache switch must be honored when called by cache_vzones itself; this
# avoids recursively clearing and repopulating the same point-set cache twice.
old_get_zones = function(old_map, "std::vector<zone_data *> map::get_vehicle_zones(")
new_get_zones = function(new_map, "std::vector<zone_data *> map::get_vehicle_zones(")
old_get_signature = "std::vector<zone_data *> map::get_vehicle_zones( const int zlev )"
new_get_signature = "std::vector<zone_data *> map::get_vehicle_zones( const int zlev, bool refresh_cache )"
assert old_get_signature in old_get_zones
assert new_get_signature in new_get_zones
assert "if( rebuild ) {" in old_get_zones
assert "if( rebuild && refresh_cache ) {" in new_get_zones
normalized_get_zones = new_get_zones.replace(new_get_signature, old_get_signature, 1).replace(
    "if( rebuild && refresh_cache ) {", "if( rebuild ) {", 1)
assert normalized_get_zones == old_get_zones, (
    "map::get_vehicle_zones changed outside the recursion guard")

old_header = original("map.h")
new_header = (source / "src/map.h").read_text(encoding="utf-8")
old_decl = "std::vector<zone_data *> get_vehicle_zones( int zlev );"
new_decl = "std::vector<zone_data *> get_vehicle_zones( int zlev, bool refresh_cache = true );"
assert old_decl in old_header and new_decl in new_header
assert new_header.replace(new_decl, old_decl, 1) == old_header, (
    "map.h changed beyond the vehicle-zone refresh parameter")

old_clzones = original("clzones.cpp")
new_clzones = (source / "src/clzones.cpp").read_text(encoding="utf-8")
old_cache_fn = function(old_clzones, "void zone_manager::cache_vzones( map *pmap )")
new_cache_fn = function(new_clzones, "void zone_manager::cache_vzones( map *pmap )")
old_get_call = "here.get_vehicle_zones( here.get_abs_sub().z() )"
new_get_call = "here.get_vehicle_zones( here.get_abs_sub().z(), /* refresh_cache = */ false )"
assert old_get_call in old_cache_fn and new_get_call in new_cache_fn
normalized_cache_fn = new_cache_fn.replace(new_get_call, old_get_call, 1)
normalized_cache_fn = normalized_cache_fn.replace("    vzone_cache_dirty_ = false;\\n", "", 1)
assert normalized_cache_fn == old_cache_fn, (
    "zone_manager::cache_vzones changed beyond recursion guard and dirty-flag reset")

helper = function(new_map, "template<typename WorkRange, typename ProcessFn>")
assert "std::less<vehicle *>{}" in helper, "std::map-compatible pointer order missing"
assert "on_map = on_map || iter->second;" in helper, "on-map precedence merge missing"
assert "process( current, on_map );" in helper, "deduplicated callback missing"
assert helper.startswith("template<typename WorkRange, typename ProcessFn>"), "helper template declaration was omitted"

fixture = r'''
#include <algorithm>
#include <cassert>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <map>
#include <new>
#include <stdexcept>
#include <unordered_set>
#include <utility>
#include <vector>

struct vehicle { int id = 0; };
static bool measuring = false;
static std::size_t allocations = 0;
static std::size_t bytes_allocated = 0;

void *operator new( std::size_t n ) {
    if( measuring ) { ++allocations; bytes_allocated += n; }
    if( void *p = std::malloc( n ? n : 1 ) ) { return p; }
    throw std::bad_alloc();
}
void *operator new[]( std::size_t n ) {
    if( measuring ) { ++allocations; bytes_allocated += n; }
    if( void *p = std::malloc( n ? n : 1 ) ) { return p; }
    throw std::bad_alloc();
}
void operator delete( void *p ) noexcept { std::free( p ); }
void operator delete[]( void *p ) noexcept { std::free( p ); }
void operator delete( void *p, std::size_t ) noexcept { std::free( p ); }
void operator delete[]( void *p, std::size_t ) noexcept { std::free( p ); }

__HELPER__

using result_item = std::pair<vehicle *, bool>;

static void run_case( const std::size_t count )
{
    std::vector<vehicle> storage( count * 2 + 8 );
    for( std::size_t i = 0; i < storage.size(); ++i ) {
        storage[i].id = static_cast<int>( i );
    }
    std::vector<vehicle *> on_map;
    on_map.reserve( count );
    // Reverse insertion order to make accidental input-order processing fail.
    for( std::size_t i = count; i > 0; --i ) {
        on_map.push_back( &storage[i - 1] );
    }
    std::vector<std::vector<vehicle *>> links( count );
    for( std::size_t i = 0; i < count; ++i ) {
        if( count > 0 ) {
            links[i].push_back( &storage[( i + 1 ) % count] );
            links[i].push_back( &storage[count + ( i % count )] );
            // Repeated edges are deliberate; connected_vehs deduplicates them.
            links[i].push_back( &storage[count + ( i % count )] );
        }
    }

    std::vector<result_item> baseline_result;
    std::vector<result_item> optimized_result;
    baseline_result.reserve( storage.size() );
    optimized_result.reserve( storage.size() );

    std::size_t baseline_allocs = 0;
    std::size_t optimized_allocs = 0;
    std::size_t baseline_bytes = 0;
    std::size_t optimized_bytes = 0;

    {
        std::map<vehicle *, bool> work;
        std::unordered_set<vehicle *> connected;
        allocations = bytes_allocated = 0;
        measuring = true;
        for( std::size_t i = 0; i < count; ++i ) {
            vehicle *veh = on_map[i];
            work[veh] = true;
            for( vehicle *linked : links[static_cast<std::size_t>( veh->id )] ) {
                connected.insert( linked );
            }
        }
        for( vehicle *linked : connected ) {
            work.emplace( linked, false );
        }
        for( const auto &entry : work ) {
            baseline_result.emplace_back( entry.first, entry.second );
        }
        measuring = false;
        baseline_allocs = allocations;
        baseline_bytes = bytes_allocated;
    }

    {
        std::vector<result_item> work;
        std::unordered_set<vehicle *> connected;
        allocations = bytes_allocated = 0;
        measuring = true;
        work.reserve( on_map.size() );
        for( std::size_t i = 0; i < count; ++i ) {
            vehicle *veh = on_map[i];
            work.emplace_back( veh, true );
            for( vehicle *linked : links[static_cast<std::size_t>( veh->id )] ) {
                connected.insert( linked );
            }
        }
        work.reserve( work.size() + connected.size() );
        for( vehicle *linked : connected ) {
            work.emplace_back( linked, false );
        }
        for_each_unique_vehicle_turn_work_item( work, [&]( vehicle *veh, bool on_map_flag ) {
            optimized_result.emplace_back( veh, on_map_flag );
        } );
        measuring = false;
        optimized_allocs = allocations;
        optimized_bytes = bytes_allocated;
    }

    assert( baseline_result.size() == optimized_result.size() );
    for( std::size_t i = 0; i < baseline_result.size(); ++i ) {
        assert( baseline_result[i].first == optimized_result[i].first );
        assert( baseline_result[i].second == optimized_result[i].second );
    }
    for( std::size_t i = 0; i < baseline_result.size(); ++i ) {
        if( baseline_result[i].first->id < static_cast<int>( count ) ) {
            assert( baseline_result[i].second );
        }
    }
    if( count >= 16 ) {
        assert( optimized_allocs < baseline_allocs );
        assert( optimized_bytes < baseline_bytes );
    }
    std::printf(
        "vehicle-worklist n=%zu unique=%zu: allocations %zu -> %zu, bytes %zu -> %zu\n",
        count, optimized_result.size(), baseline_allocs, optimized_allocs,
        baseline_bytes, optimized_bytes );
}

int main()
{
    for( std::size_t count : { 0, 1, 8, 16, 32, 128, 512 } ) {
        run_case( count );
    }
    std::puts( "PASS vehicle worklist order, de-duplication, on-map precedence, and allocation comparison" );
}
'''

fixture = fixture.replace("__HELPER__", helper)
with tempfile.TemporaryDirectory(prefix="cdda-vehicle-worklist-") as temp:
    temp = Path(temp)
    cpp = temp / "vehicle_worklist.cpp"
    binary = temp / "vehicle_worklist"
    cpp.write_text(fixture, encoding="utf-8")
    subprocess.run(
        ["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-mismatched-new-delete",
         str(cpp), "-o", str(binary)],
        check=True, timeout=90)
    subprocess.run([str(binary)], check=True, timeout=90)

print("PASS vehicle turn-worklist source guard and differential allocation benchmark")
