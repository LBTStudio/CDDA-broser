#!/usr/bin/env python3
"""Regression test for the browser build's martial-style and close-door keys.

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

style_entries = entries("pick_style")
close_entries = entries("close")
chat_entries = entries("chat")
assert style_entries, "Missing DEFAULTMODE pick_style binding"
assert close_entries, "Missing DEFAULTMODE close-door binding"
assert chat_entries, "Missing DEFAULTMODE chat binding"

def key_specs(rows):
    return [
        (b.get("input_method"), b.get("key"), tuple(sorted(b.get("mod", []))))
        for row in rows
        for b in row.get("bindings", [])
    ]

style_keys = key_specs(style_entries)
close_keys = key_specs(close_entries)
chat_keys = key_specs(chat_entries)

# Preserve both upstream martial-style alternatives while adding plain c.
assert ("keyboard_char", "_", ()) in style_keys, style_keys
assert ("keyboard_code", "-", ("shift",)) in style_keys, style_keys
assert ("keyboard_code", "c", ()) in style_keys, (
    "plain c must select martial arts style in DEFAULTMODE"
)

# Reassign the conflicting close-door key rather than allowing the two
# actions to compete for the same event. Keep Shift+C for NPC chat.
assert ("keyboard_code", "c", ("alt",)) in close_keys, (
    "Alt+C must close doors after plain c is assigned to martial style"
)
assert not any(key == "c" and not mods for _, key, mods in close_keys), close_keys
assert ("keyboard_code", "c", ("shift",)) in chat_keys or        ("keyboard_char", "C", ()) in chat_keys, chat_keys

# Only pick_style may own unmodified c within DEFAULTMODE.
owners = [
    row.get("id")
    for row in bindings
    if row.get("category") == "DEFAULTMODE"
    and any(
        b.get("key") == "c"
        and not b.get("mod")
        and b.get("input_method") in ("keyboard_any", "keyboard_code", "keyboard_char")
        for b in row.get("bindings", [])
    )
]
assert owners == ["pick_style"], f"unmodified c has conflicting DEFAULTMODE owners: {owners}"

# The browser SDL bridge must still preserve Shift for other actions (such as chat).
sdl = (root / "src/sdltiles.cpp").read_text(encoding="utf-8")
assert "if( keysym.mod & KMOD_SHIFT )" in sdl
assert "evt.modifiers.emplace( keymod_t::shift );" in sdl
assert "evt.sequence.emplace_back( keysym.sym );" in sdl

print("PASS: c selects martial style; Alt+C closes doors; Shift+C remains for chat")
