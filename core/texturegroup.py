"""MDL $texturegroup / skin-family 检测核心。

Source 编译后的 .mdl 通常不会保留 QC 中原样的
    $texturegroup "name" { ... }
文本，而是把纹理族编译成 studiohdr_t 的 skin-family 表。
因此本模块以 MDL 二进制中的 numskinfamilies / skinindex 为主来源，
并将每个 skin family 的材质映射完整列出。

如果某些工具产生的 MDL 仍然直接包含 QC 文本，则保留文本扫描作为
兼容性 fallback，但不会用它作为正常 compiled MDL 的唯一检测方式。
"""

import os
import re
import struct
from typing import Dict, List, Optional

from .vpk import parse_vpk_entries, read_vpk_file_data

# 兼容少数仍保留 QC 文本的 MDL；实际 compiled MDL 优先走二进制 skin family。
_HEADER_RE = re.compile(rb"\$texturegroup\s+\"([^\"]*)\"\s*\{", re.IGNORECASE)

STUDIO_HDR_MIN = 408
STUDIO_NUMTEXTURES = 204
STUDIO_TEXTUREINDEX = 208
STUDIO_NUMSKINREF = 220
STUDIO_NUMSKINFAMILIES = 224
STUDIO_SKININDEX = 228
STUDIO_TEXTURE_STRIDE = 72  # mstudiotexture_t: name[64] + flags + used
MAX_TEXTURES = 65536
MAX_SKIN_REFS = 65536
MAX_SKIN_FAMILIES = 65536


def _valid_range(offset: int, size: int, total: int) -> bool:
    return 0 <= offset <= total and 0 <= size <= total - offset


def _decode_cstring(data: bytes) -> str:
    raw = data.split(b"\x00", 1)[0]
    for enc in ("utf-8", "cp1252", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _read_i32(data: bytes, offset: int) -> Optional[int]:
    if offset < 0 or offset + 4 > len(data):
        return None
    return struct.unpack_from("<i", data, offset)[0]


def _find_matching_brace(data: bytes, open_brace: int) -> Optional[int]:
    depth = 0
    in_string = False
    escaped = False
    i = open_brace
    while i < len(data):
        c = data[i]
        if in_string:
            if escaped:
                escaped = False
            elif c == 92:
                escaped = True
            elif c == 34:
                in_string = False
        else:
            if c == 34:
                in_string = True
            elif c == 123:
                depth += 1
            elif c == 125:
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return None


def _line_number(data: bytes, pos: int) -> int:
    return data.count(b"\n", 0, pos) + 1


def _decode_fragment(data: bytes) -> str:
    return _decode_cstring(data).strip("\x00\r\n \t")


def extract_texturegroups_from_qc_text(data: bytes) -> List[Dict[str, object]]:
    """兼容性 fallback：直接存在 QC 文本时提取 $texturegroup "..." { ... }。"""
    if not data or b"$texturegroup" not in data.lower():
        return []
    results: List[Dict[str, object]] = []
    for match in _HEADER_RE.finditer(data):
        open_brace = match.end() - 1
        close_brace = _find_matching_brace(data, open_brace)
        if close_brace is None:
            continue
        block = data[match.start():close_brace + 1]
        results.append({
            "kind": "qc_text",
            "name": _decode_fragment(match.group(1)),
            "line": _line_number(data, match.start()),
            "content": _decode_fragment(block),
        })
    return results


def parse_mdl_skin_families(data: bytes) -> Optional[Dict[str, object]]:
    """解析 compiled MDL 的 skin-family 表。

    studiohdr_t 中：
      numtextures       @ 204
      textureindex      @ 208
      numskinref        @ 220
      numskinfamilies   @ 224
      skinindex         @ 228

    skinindex 指向 numskinfamilies * numskinref 个 int16，
    每个值是 texture table 的索引。
    """
    if len(data) < STUDIO_HDR_MIN or data[:4] != b"IDST":
        return None

    numtextures = _read_i32(data, STUDIO_NUMTEXTURES)
    textureindex = _read_i32(data, STUDIO_TEXTUREINDEX)
    numskinref = _read_i32(data, STUDIO_NUMSKINREF)
    numskinfamilies = _read_i32(data, STUDIO_NUMSKINFAMILIES)
    skinindex = _read_i32(data, STUDIO_SKININDEX)

    values = (numtextures, textureindex, numskinref, numskinfamilies, skinindex)
    if any(v is None for v in values):
        return None
    numtextures = int(numtextures)
    textureindex = int(textureindex)
    numskinref = int(numskinref)
    numskinfamilies = int(numskinfamilies)
    skinindex = int(skinindex)

    if not (0 <= numtextures <= MAX_TEXTURES):
        return None
    if not (0 <= numskinref <= MAX_SKIN_REFS):
        return None
    if not (1 <= numskinfamilies <= MAX_SKIN_FAMILIES):
        return None

    textures = []
    if numtextures:
        if textureindex < 0 or not _valid_range(textureindex, numtextures * STUDIO_TEXTURE_STRIDE, len(data)):
            return None
        for i in range(numtextures):
            ptr = textureindex + i * STUDIO_TEXTURE_STRIDE
            textures.append(_decode_cstring(data[ptr:ptr + 64]))

    total_cells = numskinref * numskinfamilies
    if total_cells == 0:
        # 没有 skin refs 就没有可展示的 $texturegroup 数据。
        return {"numtextures": numtextures, "numskinref": numskinref,
                "numskinfamilies": numskinfamilies, "families": []}

    if skinindex < 0 or not _valid_range(skinindex, total_cells * 2, len(data)):
        return None

    families = []
    for family_index in range(numskinfamilies):
        material_names = []
        texture_indices = []
        for ref_index in range(numskinref):
            cell = family_index * numskinref + ref_index
            tex_index = struct.unpack_from("<h", data, skinindex + cell * 2)[0]
            texture_indices.append(tex_index)
            if 0 <= tex_index < len(textures):
                material_names.append(textures[tex_index] or f"<texture:{tex_index}>")
            else:
                material_names.append(f"<invalid:{tex_index}>")
        families.append({
            "family_index": family_index,
            "texture_indices": texture_indices,
            "materials": material_names,
        })

    return {
        "numtextures": numtextures,
        "numskinref": numskinref,
        "numskinfamilies": numskinfamilies,
        "families": families,
    }


def extract_texturegroups_from_mdl(data: bytes) -> List[Dict[str, object]]:
    """判断编译后的 MDL 是否存在可由 skin-family 表确认的 $texturegroup。

    编译后的 MDL 不保证保留原始 QC 的 $texturegroup 名称，因此这里的
    业务语义是“该 MDL 是否存在纹理组”，而不是把一个 texturegroup 展开成
    多个 Skin Family 条目。只返回最多一个命中记录。
    """
    binary = parse_mdl_skin_families(data)
    if binary:
        family_count = int(binary["numskinfamilies"])
        # 普通单皮肤模型无法仅凭 compiled MDL 证明存在 QC $texturegroup。
        if family_count > 1:
            return [{
                "kind": "compiled_texturegroup",
                "name": "",
                "line": "—",
                "content": "",
            }]

    # 兼容少数仍保留 QC 文本的 MDL；同一 MDL 只算一个命中。
    qc_hits = extract_texturegroups_from_qc_text(data)
    if qc_hits:
        first = qc_hits[0].copy()
        first["kind"] = "qc_text"
        return [first]
    return []


def scan_vpk_texturegroups(vpk_path: str) -> List[Dict[str, object]]:
    """扫描单个 VPK，判断其中每个 MDL 是否存在 $texturegroup。

    一个 MDL 无论对应多少个 skin family，都只返回一个命中。
    """
    entries = parse_vpk_entries(vpk_path)
    if not entries:
        return []

    results: List[Dict[str, object]] = []
    seen = set()
    for rel_path, entry in entries.items():
        if not rel_path.casefold().endswith(".mdl"):
            continue
        data = read_vpk_file_data(vpk_path, entry)
        if not data:
            continue
        if isinstance(data, memoryview):
            data = data.tobytes()
        mdl_key = rel_path.casefold()
        if mdl_key in seen:
            continue
        hits = extract_texturegroups_from_mdl(bytes(data))
        if hits:
            seen.add(mdl_key)
            hit = hits[0].copy()
            hit.update({"vpk": vpk_path, "mdl_path": rel_path})
            results.append(hit)
    return results


def scan_addons_texturegroups(addons_dir: str) -> List[Dict[str, object]]:
    """递归遍历 Addons 中所有 VPK，并汇总全部 MDL 纹理组信息。"""
    vpk_files: List[str] = []
    for root, dirs, files in os.walk(addons_dir):
        dirs[:] = [d for d in dirs if d.casefold() != "temp" and not d.startswith(".")]
        for name in files:
            if name.casefold().endswith(".vpk"):
                vpk_files.append(os.path.join(root, name))
    vpk_files.sort(key=lambda p: p.casefold())

    all_hits: List[Dict[str, object]] = []
    for path in vpk_files:
        all_hits.extend(scan_vpk_texturegroups(path))
    return all_hits
