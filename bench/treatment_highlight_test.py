#!/usr/bin/env python3
"""Regression checks for the browser treatment-menu selection highlight.

Run against a patched CDDA 0.I source tree:
    python3 bench/treatment_highlight_test.py /path/to/patched/cdda

This is a source-level integration guard, not a full browser visual test.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
source = (root / "src/character_body.cpp").read_text(encoding="utf-8")
start = source.index("    uilist bmenu;", source.index("default_selection_treatment_rank"))
end = source.index("    bmenu.hilight_disabled = true;", start)
menu_setup = source[start:end]

assert "bmenu.desc_enabled = true;" in menu_setup
assert "bmenu.hilight_color = c_white;" in menu_setup, (
    "Treatment body-part selection must use a neutral highlight, not the "
    "default blue that can be confused with limb-state colors."
)
assert "bmenu.text = menu_header;" in menu_setup
assert source.count("bmenu.hilight_color = c_white;") == 1, (
    "Keep the neutral highlight scoped to the treatment menu only."
)

print("PASS: treatment menu uses a neutral white selection highlight")
