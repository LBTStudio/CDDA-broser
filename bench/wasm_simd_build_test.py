#!/usr/bin/env python3
"""Exercise the real Makefile tuning script in scalar and SIMD experiment modes.

This is configuration validation, not a full-game performance benchmark. The
source Makefile should be an untouched CDDA 0.I Makefile.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

if len(sys.argv) != 3:
    raise SystemExit("usage: wasm_simd_build_test.py /path/to/pristine/Makefile /path/to/ci/tune-makefile.sh")

source_makefile = Path(sys.argv[1]).resolve()
tune_script = Path(sys.argv[2]).resolve()
syntax = subprocess.run(
    ["bash", "-n", str(tune_script)], text=True,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
)
assert syntax.returncode == 0, (
    f"tune-makefile.sh must parse before configuration tests run:\n{syntax.stdout}"
)
upstream = source_makefile.read_text(encoding="utf-8")
assert "EMCC_COMMON_FLAGS = -sUSE_SDL=2" in upstream
assert "CXXFLAGS += $(EMCC_COMMON_FLAGS)" in upstream
assert "LDFLAGS += $(EMCC_COMMON_FLAGS)" in upstream
assert "-msimd128" not in upstream


def configure(mode):
    with tempfile.TemporaryDirectory(prefix=f"cdda-simd-{mode}-") as tmp:
        work = Path(tmp)
        (work / "Makefile").write_text(upstream, encoding="utf-8")
        env = os.environ.copy()
        env.update({"CDDA_WASM_SIMD": mode, "CDDA_RUNTIME_BACKEND": "asyncify", "CDDA_LINK_OPT": "O2"})
        result = subprocess.run(
            ["bash", str(tune_script)], cwd=work, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
        )
        if result.returncode != 0:
            raise AssertionError(f"tune-makefile failed in SIMD={mode}:\n{result.stdout}")
        return (work / "Makefile").read_text(encoding="utf-8")


scalar = configure("off")
assert "CDDA_BROSER_WASM_SIMD=on" not in scalar
assert "-msimd128" not in scalar
assert "OPTLEVEL = -O3" in scalar
assert "LDFLAGS += -O2" in scalar
assert "-sINITIAL_MEMORY=256MB" in scalar
assert "-sMAXIMUM_MEMORY=2GB" in scalar

simd = configure("on")
assert simd.count("# CDDA_BROSER_WASM_SIMD=on") == 1
assert simd.count("CXXFLAGS += -msimd128") == 1
assert simd.count("LDFLAGS += -msimd128") == 1
assert "OPTLEVEL = -O3" in simd
assert "LDFLAGS += -O2" in simd
assert "-sINITIAL_MEMORY=256MB" in simd
assert "-sMAXIMUM_MEMORY=2GB" in simd
assert scalar != simd

with tempfile.TemporaryDirectory(prefix="cdda-simd-invalid-") as tmp:
    work = Path(tmp)
    (work / "Makefile").write_text(upstream, encoding="utf-8")
    env = os.environ.copy()
    env.update({"CDDA_WASM_SIMD": "auto", "CDDA_RUNTIME_BACKEND": "asyncify", "CDDA_LINK_OPT": "O2"})
    result = subprocess.run(
        ["bash", str(tune_script)], cwd=work, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
    )
    assert result.returncode != 0
    assert "CDDA_WASM_SIMD must be off or on" in result.stdout
    assert (work / "Makefile").read_text(encoding="utf-8") == upstream

print("PASS SIMD build configuration: scalar default, compile/link flag parity, memory/link safety, invalid-mode rejection")
