#!/usr/bin/env python3
"""Replace the per-fire-tile heap vector of at most eight indices with a stack array.

This is deliberately source-anchored and fails closed if the upstream fire
processor changes its neighbor-selection structure.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
signature = "void field_processor_fd_fire("
if source.count(signature) != 1:
    raise SystemExit("ERROR: expected exactly one field_processor_fd_fire()")

start = source.index(signature)
opening = source.index("{", start)
depth, end = 1, opening + 1
while depth:
    depth += (source[end] == "{") - (source[end] == "}")
    end += 1
method = source[start:end]

old = """    std::vector<size_t> neighbour_vec;
    neighbour_vec.reserve( neighs.size() );"""
new = """    // A fire can use at most eight neighbors. Keep this hot-path scratch
    // storage on the stack instead of allocating a vector for every fire tile.
    std::array<size_t, 8> neighbour_vec{};
    size_t neighbour_count = 0;"""
if method.count(old) != 1:
    raise SystemExit("ERROR: fire neighbor vector declaration did not match exactly once")
method = method.replace(old, new, 1)

old = "            neighbour_vec.push_back( i );"
new = "            neighbour_vec[neighbour_count++] = i;"
if method.count(old) != 1:
    raise SystemExit("ERROR: fire neighbor append did not match exactly once")
method = method.replace(old, new, 1)

size_use = method.count("neighbour_vec.size()")
empty_use = method.count("neighbour_vec.empty()")
if size_use != 6:
    raise SystemExit(f"ERROR: expected six neighbor-vector size uses, found {size_use}")
if empty_use != 1:
    raise SystemExit(f"ERROR: expected one neighbor-vector empty check, found {empty_use}")
method = method.replace("neighbour_vec.size()", "neighbour_count")
method = method.replace("neighbour_vec.empty()", "neighbour_count == 0")

if "std::vector<size_t> neighbour_vec" in method or "neighbour_vec.reserve" in method:
    raise SystemExit("ERROR: heap-allocated fire neighbor vector remains")
if "neighbour_vec.push_back" in method or "neighbour_vec.size()" in method:
    raise SystemExit("ERROR: dynamic neighbor vector operation remains")
if method.count("neighbour_count") != 9:
    raise SystemExit(
        f"ERROR: unexpected neighbor_count use count {method.count('neighbour_count')}"
    )

source = source[:start] + method + source[end:]
path.write_text(source, encoding="utf-8")
print("PASS: fire neighbor selection now uses an eight-entry stack array")
