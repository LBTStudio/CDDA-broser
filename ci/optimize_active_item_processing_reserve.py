#!/usr/bin/env python3
"""Size the active-item result vector for the actual maximum loop output.

get_for_processing() intentionally returns floor(list_size / processing_speed)
+ 1 items for speed > 1, bounded by the list size; it rotates that many
entries to prevent starvation. Its existing reserve estimate omitted that
extra entry per nonempty queue and triggered a vector growth every call with
multiple scheduled queues. This patch changes reservation only, not iteration.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
signature = "std::vector<item_reference> active_item_cache::get_for_processing()"
if source.count(signature) != 1:
    raise SystemExit("ERROR: expected exactly one active_item_cache::get_for_processing()")

start = source.index(signature)
opening = source.index("{", start)
depth, end = 1, opening + 1
while depth:
    depth += (source[end] == "{") - (source[end] == "}")
    end += 1
method = source[start:end]

old = """items_to_process.reserve( std::accumulate( active_items.begin(), active_items.end(), std::size_t{ 0 },
    []( size_t prev, const auto & kv ) {
        return prev + kv.second.size() / static_cast<size_t>( kv.first );
    } ) );"""
new = """items_to_process.reserve( std::accumulate( active_items.begin(), active_items.end(), std::size_t{ 0 },
    []( size_t prev, const auto & kv ) {
        const size_t list_size = kv.second.size();
        const size_t max_this_queue = list_size == 0 ? 0 :
                                      std::min( list_size,
                                                list_size / static_cast<size_t>( kv.first ) + 1 );
        return prev + max_this_queue;
    } ) );"""
if method.count(old) != 1:
    raise SystemExit("ERROR: active-item reserve expression did not match exactly once")
for required in ("num_to_process >= 0", "items_to_process.push_back( *it );",
                 "kv.second.splice( kv.second.end(), kv.second, kv.second.begin(), it );"):
    if required not in method:
        raise SystemExit(f"ERROR: get_for_processing() loop contract changed: {required}")
method = method.replace(old, new, 1)
if "max_this_queue" not in method or method.count("items_to_process.push_back( *it );") != 1:
    raise SystemExit("ERROR: exact active-item capacity estimate missing or processing loop changed")

source = source[:start] + method + source[end:]
path.write_text(source, encoding="utf-8")
print("PASS: active-item result reserve now matches the loop's maximum output")
