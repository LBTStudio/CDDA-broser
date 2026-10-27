#!/usr/bin/env python3
"""Regression guard for the CDDA 0.I browser martial-style shortcut.

Run after applying the browser patches:
    python3 bench/martial_style_key_test.py /path/to/patched/cdda
"""
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
path = root / "data/raw/keybindings.json"
entries = json.loads(path.read_text(encoding="utf-8"))
defaults = [e for e in entries if e.get("category") == "DEFAULTMODE"]
actions = {e.get("id"): e for e in defaults}

assert "pick_style" in actions and "close" in actions and "chat" in actions

style = actions["pick_style"].get("bindings", [])
close = actions["close"].get("bindings", [])
chat = actions["chat"].get("bindings", [])

assert any(
    b.get("input_method") == "keyboard_code"
    and b.get("key") == "c"
    and not b.get("mod")
    for b in style
), "plain C must select martial arts style in DEFAULTMODE"

assert any(
    b.get("input_method") == "keyboard_code"
    and b.get("key") == "c"
    and sorted(b.get("mod", [])) == ["alt"]
    for b in close
), "Alt+C must close doors after plain C is reassigned"

assert not any(
    b.get("key") == "c" and not b.get("mod")
    for b in close
), "close door must not conflict with martial-style selection"

assert any(
    (b.get("key") == "C" and b.get("input_method") == "keyboard_char")
    or (
        b.get("key") == "c"
        and b.get("input_method") == "keyboard_code"
        and sorted(b.get("mod", [])) == ["shift"]
    )
    for b in chat
), "Shift+C must remain available for chat with NPC"

# Ensure no other DEFAULTMODE action owns an unmodified c binding.
owners = [
    e.get("id")
    for e in defaults
    if any(
        b.get("key") == "c"
        and not b.get("mod")
        and b.get("input_method") in ("keyboard_any", "keyboard_code", "keyboard_char")
        for b in e.get("bindings", [])
    )
]
assert owners == ["pick_style"], f"unmodified c has conflicting DEFAULTMODE owners: {owners}"

print("PASS: C selects martial style; Alt+C closes doors; Shift+C chats")
