#!/usr/bin/env python3
"""Verify a generated staged payload without loading it in a browser."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_set_hash(paths: list[str]) -> str:
    return hashlib.sha256("".join(f"{p}\n" for p in sorted(paths)).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--payload-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    packages = manifest["packages"]
    paths = [path for spec in packages.values() for path in spec["paths"]]

    assert len(packages) == 63, f"expected 63 packages, found {len(packages)}"
    assert len(paths) == 7937, f"expected 7937 paths, found {len(paths)}"
    assert len(paths) == len(set(paths)), "duplicate virtual paths"
    assert path_set_hash(paths) == manifest["source_path_set_sha256"], "path set hash mismatch"
    assert manifest["lossless_invariants"] == {
        "package_path_count_matches_source": True,
        "package_paths_unique": True,
        "package_path_set_sha256_matches_source": True,
    }, "manifest lossless flags are not all true"

    for name, spec in packages.items():
        data = args.payload_dir / spec["data_file"]
        loader = args.payload_dir / spec["loader_file"]
        assert data.is_file() and loader.is_file(), f"missing package output: {name}"
        assert data.stat().st_size == spec["data_bytes"], f"data size mismatch: {name}"
        assert loader.stat().st_size == spec["loader_bytes"], f"loader size mismatch: {name}"
        assert hash_file(data) == spec["data_sha256"], f"data hash mismatch: {name}"
        assert hash_file(loader) == spec["loader_sha256"], f"loader hash mismatch: {name}"

    required_groups = {
        "boot-audio", "boot-localization", "world-core-json", "world-core-support",
        "world-mod-MA", "world-gfx-Ultica_iso-assets",
    }
    assert required_groups <= set(packages), "required content group missing"
    print(json.dumps({
        "status": "PASS",
        "package_count": len(packages),
        "path_count": len(paths),
        "path_set_sha256": manifest["source_path_set_sha256"],
        "logical_bytes": manifest["source_logical_bytes"],
        "required_groups": sorted(required_groups),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
