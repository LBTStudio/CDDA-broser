#!/usr/bin/env python3
"""Generate the complete CDDA virtual filesystem as independent LZ4 packages.

This tool deliberately uses a checked-in path manifest.  It neither filters
source files nor falls back to prepare-web.sh, so every listed data, MOD,
tileset, sound, font and locale file is packaged at its original virtual path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def sha256_path_set(paths: list[str]) -> str:
    payload = "".join(f"{path}\n" for path in sorted(paths)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_for_virtual_path(source_root: Path, virtual_path: str) -> Path:
    if not virtual_path.startswith("/") or ".." in Path(virtual_path).parts:
        raise ValueError(f"unsafe virtual path: {virtual_path!r}")
    source = source_root / virtual_path.lstrip("/")
    if not source.is_file():
        raise FileNotFoundError(f"manifest path does not exist in source tree: {source}")
    return source


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--file-packager", required=True, type=Path)
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    manifest: dict[str, Any] = json.loads(args.manifest.read_text(encoding="utf-8"))
    package_specs: dict[str, Any] = manifest["packages"]
    args.out.mkdir(parents=True, exist_ok=False)

    all_paths: list[str] = []
    for group, spec in package_specs.items():
        paths = spec.get("paths")
        if not isinstance(paths, list) or not paths:
            raise ValueError(f"package {group} has no paths")
        all_paths.extend(paths)

    if len(all_paths) != manifest["source_file_count"]:
        raise ValueError("manifest package path count does not match source_file_count")
    if len(set(all_paths)) != len(all_paths):
        raise ValueError("manifest package paths are not unique")
    if sha256_path_set(all_paths) != manifest["source_path_set_sha256"]:
        raise ValueError("manifest package paths do not match source_path_set_sha256")

    generated: dict[str, Any] = {}
    for index, (group, spec) in enumerate(package_specs.items(), start=1):
        data_name = spec["data_file"]
        loader_name = spec["loader_file"]
        data_path = args.out / data_name
        loader_path = args.out / loader_name
        files = []
        for virtual_path in spec["paths"]:
            source = source_for_virtual_path(source_root, virtual_path)
            files.append(f"{source}@{virtual_path}")

        command = [
            str(args.file_packager),
            str(data_path),
            "--lz4",
            f"--js-output={loader_path}",
            "--preload",
            *files,
        ]
        print(f"[STAGE] {index}/{len(package_specs)} {group}: {len(files)} paths", flush=True)
        subprocess.run(command, check=True)
        if not data_path.is_file() or not loader_path.is_file():
            raise RuntimeError(f"file_packager omitted output for {group}")
        generated[group] = {
            "data_file": data_name,
            "loader_file": loader_name,
            "data_bytes": data_path.stat().st_size,
            "loader_bytes": loader_path.stat().st_size,
            "data_sha256": sha256_file(data_path),
            "loader_sha256": sha256_file(loader_path),
            "file_count": len(files),
            "logical_bytes": sum(source_for_virtual_path(source_root, p).stat().st_size for p in spec["paths"]),
            "paths": spec["paths"],
        }

    result = {
        "source_path_set_sha256": manifest["source_path_set_sha256"],
        "source_file_count": manifest["source_file_count"],
        "source_logical_bytes": manifest["source_logical_bytes"],
        "packages": generated,
        "runtime_group_maps": manifest["runtime_group_maps"],
        "lossless_invariants": {
            "package_path_count_matches_source": sum(item["file_count"] for item in generated.values()) == manifest["source_file_count"],
            "package_paths_unique": len(set(all_paths)) == len(all_paths),
            "package_path_set_sha256_matches_source": sha256_path_set(all_paths) == manifest["source_path_set_sha256"],
        },
    }
    (args.out / "staged-package-manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if not all(result["lossless_invariants"].values()):
        raise RuntimeError("lossless invariants failed")
    print("[STAGE] lossless package invariants: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
