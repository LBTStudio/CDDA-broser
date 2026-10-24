#!/usr/bin/env python3
"""Apply the nearby-item grouping optimization after runtime-efficiency.

The latter patch changes the target function's body, so a line-numbered diff
hunk is fragile. This transform uses exact, unique source anchors and fails
closed if upstream or an earlier patch changes the expected structure.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"ERROR: nearby-items-hash expected one {label} anchor, found {count}"
        )
    return text.replace(old, new, 1)


source = replace_once(
    source,
    """static void add_item_recursive( std::vector<std::string> &item_order,
                                std::map<std::string, map_item_stack> &temp_items, const item *it,
                                const tripoint_rel_ms &relative_pos )""",
    """static void add_item_recursive( std::vector<map_item_stack *> &stack_order,
                                std::unordered_map<std::string, map_item_stack> &temp_items, const item *it,
                                const tripoint_rel_ms &relative_pos )""",
    "recursive grouping signature",
)
source = replace_once(
    source,
    "        item_order.push_back( name );",
    "        stack_order.push_back( &entry.first->second );",
    "first-seen order append",
)
source = replace_once(
    source,
    "        add_item_recursive( item_order, temp_items, content, relative_pos );",
    "        add_item_recursive( stack_order, temp_items, content, relative_pos );",
    "recursive call",
)
source = replace_once(
    source,
    """    std::map<std::string, map_item_stack> temp_items;
    std::vector<map_item_stack> ret;
    std::vector<std::string> item_order;""",
    """    std::unordered_map<std::string, map_item_stack> temp_items;
    std::vector<map_item_stack> ret;
    std::vector<map_item_stack *> stack_order;""",
    "nearby-item container declaration",
)
source = replace_once(
    source,
    "                add_item_recursive( item_order, temp_items, &elem, relative_pos );",
    "                add_item_recursive( stack_order, temp_items, &elem, relative_pos );",
    "top-level recursive call",
)
source = replace_once(
    source,
    """    ret.reserve( item_order.size() );
    for( auto &elem : item_order ) {
        ret.push_back( std::move( temp_items.at( elem ) ) );
    }""",
    """    ret.reserve( stack_order.size() );
    for( map_item_stack *stack : stack_order ) {
        ret.push_back( std::move( *stack ) );
    }""",
    "result transfer",
)

recursive_start = source.index("static void add_item_recursive(")
nearby_start = source.index("std::vector<map_item_stack> game::find_nearby_items(")
draw_start = source.index("\nvoid draw_trail(", nearby_start)
if "item_order" in source[recursive_start:nearby_start]:
    raise SystemExit("ERROR: item_order remains in recursive grouping after transform")
if "item_order" in source[nearby_start:draw_start]:
    raise SystemExit("ERROR: item_order remains in find_nearby_items after transform")
if source.count("std::unordered_map<std::string, map_item_stack>") < 2:
    raise SystemExit("ERROR: expected both unordered-map declarations after transform")

path.write_text(source, encoding="utf-8")
print("PASS: nearby item grouping transform matched all six anchors")
