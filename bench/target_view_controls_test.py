#!/usr/bin/env python3
"""Regression checks for aiming view restoration and browser keyboard view controls."""
from __future__ import annotations

import json
import sys
from pathlib import Path

source = Path(sys.argv[1]).resolve()
ranged = (source / "src/ranged.cpp").read_text(encoding="utf-8")
keybindings = json.loads((source / "data/raw/keybindings.json").read_text(encoding="utf-8"))

# Fire mode must restore the caller's camera offset on every exit path.
start = ranged.index("target_handler::trajectory target_handler::mode_fire")
end = ranged.index("target_handler::trajectory target_handler::mode_throw", start)
fire_body = ranged[start:end]
guard = "restore_on_out_of_scope view_offset_prev( you.view_offset );"
assert guard in fire_body, "mode_fire() must restore the previous view offset"
assert fire_body.index(guard) < fire_body.index("return ui.run();"), "view guard must cover ui.run()"

# Other target entry points already use the same contract; keep that invariant.
for signature in (
    "target_handler::trajectory target_handler::mode_select_only",
    "target_handler::trajectory target_handler::mode_throw",
    "target_handler::trajectory target_handler::mode_reach",
):
    a = ranged.index(signature)
    b = ranged.find("\ntarget_handler::trajectory ", a + len(signature))
    body = ranged[a:] if b == -1 else ranged[a:b]
    assert guard in body, f"{signature} lost its view-offset guard"

def entries(action_id: str, category: str | None = None) -> list[dict]:
    return [
        item for item in keybindings
        if item.get("id") == action_id
        and (category is None or item.get("category") == category)
    ]

def has_binding(item: dict, input_method: str, key: str, mods: tuple[str, ...] = ()) -> bool:
    return any(
        binding.get("input_method") == input_method
        and binding.get("key") == key
        and tuple(binding.get("mod", [])) == mods
        for binding in item.get("bindings", [])
    )

# The target-screen reset belongs to the target category so the keybinding editor
# can list/rebind the action in the same context where it is registered.
target_center = entries("CENTER", "TARGET")
assert len(target_center) == 1, "CENTER must be listed once under TARGET"
assert has_binding(target_center[0], "keyboard_any", "0")
assert has_binding(target_center[0], "keyboard_code", "KEYPAD_0")

# Keyboard users need a predictable center action and panning keys in normal gameplay.
default_center = entries("center", "DEFAULTMODE")
assert len(default_center) == 1, "missing DEFAULTMODE center action"
assert has_binding(default_center[0], "keyboard_code", "HOME", ("shift",))
assert any(b.get("input_method") == "gamepad" for b in default_center[0].get("bindings", []))

expected_pan = {
    "shift_n": "UP",
    "shift_e": "RIGHT",
    "shift_s": "DOWN",
    "shift_w": "LEFT",
}
for action_id, key in expected_pan.items():
    found = entries(action_id, "DEFAULTMODE")
    assert len(found) == 1, f"missing DEFAULTMODE action {action_id}"
    assert has_binding(found[0], "keyboard_code", key, ("shift",)), (
        f"{action_id} must bind Shift+{key}"
    )
    assert any(b.get("input_method") == "gamepad" for b in found[0].get("bindings", [])), (
        f"{action_id} must retain its gamepad binding"
    )

# New Shift+arrow bindings must not collide with another DEFAULTMODE action.
for action_id, key in expected_pan.items():
    signature = ("keyboard_code", key, ("shift",))
    collisions = []
    for item in keybindings:
        if item.get("category") != "DEFAULTMODE" or item.get("id") == action_id:
            continue
        for binding in item.get("bindings", []):
            if (
                binding.get("input_method"),
                binding.get("key"),
                tuple(binding.get("mod", [])),
            ) == signature:
                collisions.append(item.get("id"))
    assert not collisions, f"{action_id} Shift+{key} collision: {collisions}"

# The new center binding also has no same-context collision.
center_signature = ("keyboard_code", "HOME", ("shift",))
for item in keybindings:
    if item.get("category") != "DEFAULTMODE" or item.get("id") == "center":
        continue
    for binding in item.get("bindings", []):
        if (
            binding.get("input_method"),
            binding.get("key"),
            tuple(binding.get("mod", [])),
        ) == center_signature:
            raise AssertionError(f"center Shift+Home collision: {item.get('id')}")

# The previously important C bindings are deliberately unchanged.
close = entries("close", "DEFAULTMODE")
chat = entries("chat", "DEFAULTMODE")
assert len(close) == 1 and has_binding(close[0], "keyboard_any", "c")
assert len(chat) == 1 and has_binding(chat[0], "keyboard_char", "C")
assert has_binding(chat[0], "keyboard_code", "c", ("shift",))

print("PASS target view controls: aim offset restored; Shift+arrows pan; TARGET center rebinding; C/Chat bindings preserved")
