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
start = source.index("    uilist bmenu;", source.index("max_bp_name_len"))
end = source.index("    bmenu.hilight_disabled = true;", start)
menu_setup = source[start:end]

assert "bmenu.desc_enabled = true;" in menu_setup
assert "bmenu.neutral_selection_background = true;" in menu_setup, (
    "Treatment body-part selection must opt into its dedicated neutral "
    "background to avoid confusion with limb-state colors."
)
assert "bmenu.text = menu_header;" in menu_setup
assert source.count("bmenu.neutral_selection_background = true;") == 1, (
    "Keep the neutral background scoped to the treatment menu only."
)

ui_source = (root / "src/uilist.cpp").read_text(encoding="utf-8")
assert "ImGuiCol_HeaderHovered" in ui_source
assert "ImGuiCol_HeaderActive" in ui_source
assert "ImGui::PopStyleColor( 3 );" in ui_source
print("PASS: treatment menu uses a scoped neutral selection background")
