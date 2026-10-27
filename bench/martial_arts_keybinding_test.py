#!/usr/bin/env python3
"""Regression test for the martial-style screen's compatible-weapon hint.

Run against the patched CDDA 0.I source tree:
    python3 bench/martial_arts_keybinding_test.py /path/to/patched/cdda

This checks action ownership and dynamic key-label generation. It intentionally
does not impose a global key assignment: existing user keybindings may differ.
"""
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
source = (root / "src/character.cpp").read_text(encoding="utf-8")
start = source.index("bool character_martial_arts::pick_style")
end = source.index("kmenu.query();", start)
menu = source[start:end]

assert 'ctxt.register_action( "SHOW_DESCRIPTION" );' in menu
assert 'ctxt.get_desc( "SHOW_DESCRIPTION" )' in menu
assert "technique details and compatible weapons" in menu
assert 'kmenu.input_category = "MELEE_STYLE_PICKER";' in menu
assert 'kmenu.additional_actions.emplace_back( "SHOW_DESCRIPTION", translation() );' in menu

bindings = json.loads((root / "data/raw/keybindings.json").read_text(encoding="utf-8"))
description = [
    row for row in bindings
    if row.get("id") == "SHOW_DESCRIPTION"
    and row.get("category") == "MELEE_STYLE_PICKER"
]
assert len(description) == 1, (
    "The compatible-weapon hint must resolve through the style-picker's "
    "SHOW_DESCRIPTION action, not the global pick_style action."
)
binding_specs = [
    (entry.get("input_method"), entry.get("key"), tuple(sorted(entry.get("mod", []))))
    for row in description
    for entry in row.get("bindings", [])
]
assert binding_specs, "SHOW_DESCRIPTION needs a binding in MELEE_STYLE_PICKER"

# The global action for opening the style picker must not be modified here.
style = [
    row for row in bindings
    if row.get("id") == "pick_style" and row.get("category") == "DEFAULTMODE"
]
assert style, "Missing DEFAULTMODE pick_style action"
assert not any(
    entry.get("key", "").lower() == "c" and not entry.get("mod")
    for row in style for entry in row.get("bindings", [])
), "Do not reassign plain c to the global pick_style action"

print("PASS: compatible-weapon hint uses the style-picker-local SHOW_DESCRIPTION action")
