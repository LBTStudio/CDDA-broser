#!/usr/bin/env python3
"""Audit the CDDA 0.I default martial-arts binding without changing door controls.

Run against the patched CDDA source tree:
    python3 bench/martial_arts_keybinding_test.py /path/to/patched/cdda
"""
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
bindings = json.loads((root / "data/raw/keybindings.json").read_text(encoding="utf-8"))

def entries(action, category="DEFAULTMODE"):
    return [entry for entry in bindings
            if entry.get("id") == action and entry.get("category") == category]

style = entries("pick_style")
close = entries("close")
assert style, "Missing DEFAULTMODE pick_style binding"
assert close, "Missing DEFAULTMODE close-door binding"

style_keys = [(b.get("input_method"), b.get("key"), tuple(b.get("mod", [])))
              for entry in style for b in entry.get("bindings", [])]
close_keys = [(b.get("input_method"), b.get("key"), tuple(b.get("mod", [])))
              for entry in close for b in entry.get("bindings", [])]

# Upstream 0.I defaults to Shift+- / '_' for martial arts; lowercase c remains
# the door-close action. Do not silently reassign either action in this patch.
assert ("keyboard_code", "-", ("shift",)) in style_keys or \
       ("keyboard_char", "_", ()) in style_keys, style_keys
assert ("keyboard_any", "c", ()) in close_keys, close_keys
assert not any(key.lower() == "c" and not mods for _, key, mods in style_keys), (
    "Default pick_style unexpectedly conflicts with close-door on c"
)

sdl = (root / "src/sdltiles.cpp").read_text(encoding="utf-8")
assert "if( keysym.mod & KMOD_SHIFT )" in sdl
assert "evt.modifiers.emplace( keymod_t::shift );" in sdl
assert "evt.sequence.emplace_back( keysym.sym );" in sdl
print("PASS: default martial-arts binding is distinct from close-door; SDL preserves Shift")
