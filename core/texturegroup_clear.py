"""Texture-group cleanup core for unpacked Addons folders."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Tuple

from .texturegroup import extract_texturegroups_from_mdl

WHITELIST_MODEL_DIRS = {
    ("models", "v_models"),
    ("models", "w_models"),
    ("models", "weapons"),
}


def is_whitelisted_model_path(relative_path: str) -> bool:
    normalized = str(relative_path).replace("\\", "/")
    parts = tuple(p.casefold() for p in Path(normalized).parts)
    for i in range(len(parts) - 1):
        if (parts[i], parts[i + 1]) in WHITELIST_MODEL_DIRS:
            return True
    return False


def scan_addons_texturegroup_mdls(addons_dir: str) -> List[Dict[str, object]]:
    """Scan real Addons folders, never VPKs, and return MDLs with texturegroups."""
    results: List[Dict[str, object]] = []
    addons_dir = os.path.abspath(addons_dir)
    for root, dirs, files in os.walk(addons_dir):
        dirs[:] = [d for d in dirs if d.casefold() != "temp" and not d.startswith(".")]
        for name in files:
            if not name.casefold().endswith(".mdl"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, addons_dir)
            if is_whitelisted_model_path(rel):
                continue
            try:
                with open(path, "rb") as f:
                    data = f.read()
                if extract_texturegroups_from_mdl(data):
                    results.append({"mdl_path": path, "relative_path": rel.replace(os.sep, "/")})
            except OSError:
                continue
    results.sort(key=lambda x: x["relative_path"].casefold())
    return results


def _scanable_brace_end(text: str, open_pos: int) -> int | None:
    depth = 0
    in_string = False
    escaped = False
    line_comment = False
    block_comment = False
    i = open_pos
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ""
        if line_comment:
            if c in "\r\n":
                line_comment = False
        elif block_comment:
            if c == "*" and n == "/":
                block_comment = False
                i += 1
        elif in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
        else:
            if c == "/" and n == "/":
                line_comment = True
                i += 1
            elif c == "/" and n == "*":
                block_comment = True
                i += 1
            elif c == '"':
                in_string = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return None


def _find_texturegroup_header(text: str, start: int = 0) -> tuple[int, int] | None:
    """Find the next active $texturegroup header without using a recursive regex engine."""
    lower = text.casefold()
    token = "$texturegroup"
    n = len(text)
    i = start
    in_string = False
    escaped = False
    line_comment = False
    block_comment = False
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if line_comment:
            if c in "\r\n":
                line_comment = False
            i += 1
            continue
        if block_comment:
            if c == "*" and nxt == "/":
                block_comment = False
                i += 2
            else:
                i += 1
            continue
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
            i += 1
            continue
        if c == "/" and nxt == "/":
            line_comment = True
            i += 2
            continue
        if c == "/" and nxt == "*":
            block_comment = True
            i += 2
            continue
        if lower.startswith(token, i):
            # Token boundary: do not match things like $texturegroup_extra.
            before_ok = i == 0 or not (lower[i - 1].isalnum() or lower[i - 1] in "_$")
            after = i + len(token)
            after_ok = after >= n or not (lower[after].isalnum() or lower[after] in "_$")
            if before_ok and after_ok:
                j = after
                while j < n and text[j].isspace():
                    j += 1
                if j < n and text[j] == '"':
                    j += 1
                    escaped_name = False
                    while j < n:
                        if escaped_name:
                            escaped_name = False
                        elif text[j] == "\\":
                            escaped_name = True
                        elif text[j] == '"':
                            break
                        j += 1
                    if j < n and text[j] == '"':
                        j += 1
                        while j < n and text[j].isspace():
                            j += 1
                        if j < n and text[j] == "{":
                            return i, j
        i += 1
    return None


def remove_texturegroups_from_qc(qc_text: str) -> Tuple[str, int]:
    """Remove complete `$texturegroup "name" { ... }` blocks, including nested braces.

    Implemented as a linear scanner instead of a large regex search so very large or
    unusually nested QC files cannot trigger Python's regex recursion limit.
    """
    ranges: List[Tuple[int, int]] = []
    cursor = 0
    while True:
        hit = _find_texturegroup_header(qc_text, cursor)
        if hit is None:
            break
        start, open_pos = hit
        close_pos = _scanable_brace_end(qc_text, open_pos)
        if close_pos is None:
            # Keep the malformed block untouched rather than risking deletion of the rest of the QC.
            cursor = open_pos + 1
            continue
        end = close_pos + 1
        while end < len(qc_text) and qc_text[end] in " \t":
            end += 1
        if end < len(qc_text) and qc_text[end] == "\r":
            end += 1
        if end < len(qc_text) and qc_text[end] == "\n":
            end += 1
        ranges.append((start, end))
        cursor = end

    if not ranges:
        return qc_text, 0
    out = []
    cursor = 0
    for start, end in ranges:
        out.append(qc_text[cursor:start])
        cursor = end
    out.append(qc_text[cursor:])
    return "".join(out), len(ranges)

def copy_tree_over(source_dir: str, destination_dir: str) -> int:
    """Copy all compiled files back over an Addons model directory."""
    count = 0
    os.makedirs(destination_dir, exist_ok=True)
    for root, dirs, files in os.walk(source_dir):
        rel = os.path.relpath(root, source_dir)
        dst_root = destination_dir if rel == "." else os.path.join(destination_dir, rel)
        os.makedirs(dst_root, exist_ok=True)
        for file_name in files:
            src = os.path.join(root, file_name)
            dst = os.path.join(dst_root, file_name)
            shutil.copy2(src, dst)
            count += 1
    return count
