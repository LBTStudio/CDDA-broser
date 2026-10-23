#!/usr/bin/env python3
"""Regression checks for the real linker-cache fingerprint implementation."""
import os
import subprocess
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
script = repo / "ci" / "link-fingerprint.sh"

def fingerprint(cwd: Path, manifest: Path, link_opt: str | None = None) -> str:
    env = os.environ.copy()
    if link_opt is not None:
        env["CDDA_LINK_OPT"] = link_opt
    result = subprocess.run(
        ["bash", str(script), str(manifest)],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()

with tempfile.TemporaryDirectory(prefix="cdda-link-fingerprint-") as temp:
    cwd = Path(temp)
    (cwd / "Makefile").write_text("LDFLAGS += -O2\n", encoding="utf-8")
    obj = cwd / "obj" / "tiles" / "one.o"
    manifest = cwd / "objs-all.txt"
    manifest.write_text(str(obj) + "\n", encoding="utf-8")

    # The same pre-link state used to be handed to the Actions cache from
    # the plan job; it must explicitly be unusable rather than look valid.
    missing = fingerprint(cwd, manifest)
    assert missing.startswith("incomplete-"), missing

    obj.parent.mkdir(parents=True)
    obj.write_bytes(b"compiled-object-version-1")
    first = fingerprint(cwd, manifest)
    second = fingerprint(cwd, manifest)
    assert len(first) == 16 and all(ch in "0123456789abcdef" for ch in first), first
    assert first == second, (first, second)
    assert not first.startswith("incomplete-"), first

    obj.write_bytes(b"compiled-object-version-2")
    changed_object = fingerprint(cwd, manifest)
    assert changed_object != first, (first, changed_object)

    (cwd / "Makefile").write_text("LDFLAGS += -O1\n", encoding="utf-8")
    changed_flags = fingerprint(cwd, manifest)
    assert changed_flags != changed_object, (changed_object, changed_flags)

    changed_link_opt = fingerprint(cwd, manifest, link_opt="O1")
    assert changed_link_opt != changed_flags, (changed_flags, changed_link_opt)

print("PASS link fingerprint: missing inputs are non-reusable; identical inputs stable; object/Makefile/link-opt changes invalidate cache")
