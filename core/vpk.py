import os
import shutil
import struct
import zlib

def _decode_vpk_string(value: bytes) -> str:
    """解码 VPK 目录树中的字节字符串。

    VPK 目录树保存的是 NUL 结尾字节串；实际制作工具可能使用 UTF-8，
    也可能使用生成机器的 Windows ANSI code page。这里不再“UTF-8 失败才
    CP936”，而是：
      1) 先用 UTF-8 严格验证；
      2) 同时取得当前 Windows ACP；
      3) 对各候选结果按可打印字符和中日韩文字可读性评分；
      4) 保留完整字节，绝不 errors=ignore 丢掉路径字节。

    这样可以兼容 UTF-8、简中 CP936/GB18030、繁中 CP950、日文 CP932、
    韩文 CP949 等常见 Windows VPK 来源。
    """
    if not value:
        return ""

    candidates = []

    def score(text: str) -> float:
        if not text:
            return -1e9
        printable = 0
        controls = 0
        cjk = kana = hangul = 0
        suspicious = 0
        mojibake = (
            "鏌", "愪", "鍦", "鍥", "鐨", "瀹", "璇", "浼", "濡",
            "鑷", "鐗", "闄", "浣", "寮", "绁", "骞", "姝", "櫒",
        )
        for ch in text:
            cp = ord(ch)
            if ch.isprintable():
                printable += 1
            else:
                controls += 1
            if 0x3400 <= cp <= 0x9FFF or 0xF900 <= cp <= 0xFAFF:
                cjk += 1
            elif 0x3040 <= cp <= 0x30FF:
                kana += 1
            elif 0xAC00 <= cp <= 0xD7AF:
                hangul += 1
        suspicious = sum(text.count(x) for x in mojibake)
        return (
            printable * 0.30
            + cjk * 8.0
            + kana * 5.0
            + hangul * 5.0
            - controls * 25.0
            - suspicious * 7.0
        )

    # UTF-8：严格解码，成功就作为高可信候选。
    try:
        utf8_text = value.decode("utf-8")
        candidates.append((score(utf8_text), utf8_text, "utf-8"))
    except UnicodeDecodeError:
        pass

    # Windows 当前 ANSI code page。GCFScape 等传统 Windows 工具通常会
    # 依赖本地代码页来解释非 UTF-8 的目录树字符串。
    acp = None
    try:
        import ctypes
        acp = int(ctypes.windll.kernel32.GetACP())
    except Exception:
        pass

    encodings = []
    if acp and 1 <= acp <= 65535:
        encodings.append(f"cp{acp}")
    encodings.extend(["cp936", "gb18030", "cp950", "cp932", "cp949"])

    seen = {"utf-8"}
    for enc in encodings:
        if enc.lower() in seen:
            continue
        seen.add(enc.lower())
        try:
            text = value.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        candidates.append((score(text), text, enc))

    if not candidates:
        return value.decode("utf-8", errors="replace")

    # 对纯 ASCII，所有候选都一样，优先 UTF-8。
    if all(b < 0x80 for b in value):
        return value.decode("ascii")

    # UTF-8 是格式最常见、且不会发生中文“字节被错误二次解释”的首选。
    # 只要 UTF-8 结果包含中日韩文字，就直接采用，避免中文 UTF-8 被 CP936
    # 成功解码成“鏌愪簺…”这类看似合法但实际上错误的中文。
    for _, text, enc in candidates:
        if enc == "utf-8" and any(
            (0x3400 <= ord(ch) <= 0x9FFF)
            or (0x3040 <= ord(ch) <= 0x30FF)
            or (0xAC00 <= ord(ch) <= 0xD7AF)
            for ch in text
        ):
            return text

    # 否则选评分最高的本地/候选编码。
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _read_cstring(data: bytes, offset: int):
    """读取 VPK 字符串；返回 (bytes, next_offset)，越界时 next_offset=-1。"""
    if offset < 0 or offset >= len(data):
        return b"", -1
    end = data.find(b"\x00", offset)
    if end < 0:
        return b"", -1
    return data[offset:end], end + 1


def parse_vpk_entries(vpk_path):
    """
    读取 VPK tree。
    关键修复：
    1. VPK v1/v2 均支持。
    2. 对 _dir.vpk 中 arch_idx == 0x7FFF 的文件，offset 永远是相对于
       data section 起点的，不能根据大小猜测是否需要加 data_start。
    3. 保存完整的 header/data 信息，后续读取时可做严格边界校验。
    """
    entries = {}
    try:
        with open(vpk_path, "rb") as f:
            header12 = f.read(12)
            if len(header12) < 12:
                return entries

            magic, version, tree_size = struct.unpack("<III", header12)
            if magic != 0x55AA1234 or version not in (1, 2):
                return entries

            if version == 1:
                header_size = 12
                file_data_section_size = None
                archive_md5_section_size = 0
                other_md5_section_size = 0
                signature_section_size = 0
            else:
                tail = f.read(16)
                if len(tail) < 16:
                    return entries
                (
                    file_data_section_size,
                    archive_md5_section_size,
                    other_md5_section_size,
                    signature_section_size,
                ) = struct.unpack("<IIII", tail)
                header_size = 28

            data_start = header_size + tree_size

            f.seek(header_size)
            tree_data = f.read(tree_size)
            if len(tree_data) != tree_size:
                return entries

            idx = 0
            while idx < len(tree_data):
                ext_b, next_idx = _read_cstring(tree_data, idx)
                if next_idx < 0 or not ext_b:
                    break
                idx = next_idx
                ext_str = _decode_vpk_string(ext_b)

                while idx < len(tree_data):
                    path_b, next_idx = _read_cstring(tree_data, idx)
                    if next_idx < 0:
                        idx = len(tree_data)
                        break
                    idx = next_idx
                    if not path_b:
                        break

                    path_str = _decode_vpk_string(path_b)

                    while idx < len(tree_data):
                        fname_b, next_idx = _read_cstring(tree_data, idx)
                        if next_idx < 0:
                            idx = len(tree_data)
                            break
                        idx = next_idx
                        if not fname_b:
                            break

                        # VPK directory entry固定 18 字节。
                        if idx + 18 > len(tree_data):
                            idx = len(tree_data)
                            break

                        crc, preload, arch_idx, offset, length, term = struct.unpack(
                            "<IHHIIH", tree_data[idx: idx + 18]
                        )
                        idx += 18

                        if preload:
                            if idx + preload > len(tree_data):
                                idx = len(tree_data)
                                break
                            preload_data = tree_data[idx: idx + preload]
                            idx += preload
                        else:
                            preload_data = b""

                        path_clean = path_str.replace("\\", "/").strip("/")
                        # Source VPK 对根目录有两种常见写法：空字符串和单个空格。
                        # 后者如果不归一化，会被错误解包成 "\\ \\addoninfo.txt"。
                        if not path_clean.strip():
                            path_clean = ""
                        elif path_clean.lower().startswith("root/"):
                            path_clean = path_clean[5:]
                        elif path_clean.lower() == "root":
                            path_clean = ""

                        fname_str = _decode_vpk_string(fname_b)
                        if path_clean:
                            rel_path = f"{path_clean}/{fname_str}.{ext_str}"
                        else:
                            rel_path = f"{fname_str}.{ext_str}"

                        rel_path = rel_path.replace("\\", "/")

                        entries[rel_path] = {
                            "crc": crc,
                            "preload": preload_data,
                            "arch_idx": arch_idx,
                            "offset": offset,
                            "length": length,
                            "term": term,
                            "data_start": data_start,
                            "header_size": header_size,
                            "version": version,
                            "file_data_section_size": file_data_section_size,
                        }

    except (OSError, struct.error, ValueError):
        return {}

    return entries


def _vpk_archive_path(vpk_path, arch_idx):
    """把 archive index 解析成实际 VPK 文件。"""
    dir_name, file_name = os.path.split(vpk_path)
    lower_name = file_name.lower()

    if lower_name.endswith("_dir.vpk"):
        prefix = file_name[:-8]
    else:
        prefix = os.path.splitext(file_name)[0]

    # 0x7FFF 表示当前 _dir.vpk / 当前单体 VPK 的 data section。
    if arch_idx == 0x7FFF:
        return vpk_path

    # 普通 archive part：foo_000.vpk / foo_001.vpk ...
    return os.path.join(dir_name, f"{prefix}_{arch_idx:03d}.vpk")


def read_vpk_file_data(vpk_path, entry):
    """
    精确读取 VPK entry。
    对当前文件(0x7FFF)使用：
        文件偏移 = data_start + entry.offset
    这是原实现最关键的错误来源之一。
    """
    preload_data = entry.get("preload", b"") or b""
    length = int(entry.get("length", 0))

    if length <= 0:
        return bytes(preload_data)

    arch_idx = int(entry.get("arch_idx", 0x7FFF))
    target_vpk = _vpk_archive_path(vpk_path, arch_idx)

    if not os.path.exists(target_vpk):
        return b""

    if arch_idx == 0x7FFF:
        # VPK 规范：directory/current archive 中 offset 相对 data section。
        seek_pos = int(entry.get("data_start", 0)) + int(entry.get("offset", 0))
    else:
        # 分卷 archive 中 offset 相对文件起点。
        seek_pos = int(entry.get("offset", 0))

    if seek_pos < 0:
        return b""

    try:
        file_size = os.path.getsize(target_vpk)
        if seek_pos > file_size:
            return b""

        wanted = length
        if seek_pos + wanted > file_size:
            return b""

        with open(target_vpk, "rb") as f:
            f.seek(seek_pos)
            payload = f.read(wanted)

        if len(payload) != wanted:
            return b""

        return bytes(preload_data) + payload
    except OSError:
        return b""


def pack_directory_internal_vpk(source_dir, output_path):
    """构建一个标准 VPK v1 单体文件；用于 vpk.exe 未产生可用输出时的可靠回退。"""
    source_dir = os.path.abspath(source_dir)
    output_path = os.path.abspath(output_path)
    files = []
    for root, dirs, names in os.walk(source_dir):
        dirs[:] = [d for d in dirs if d not in (".", "..")]
        for name in names:
            full = os.path.join(root, name)
            if os.path.abspath(full) == output_path:
                continue
            rel = os.path.relpath(full, source_dir).replace(os.sep, "/")
            files.append((rel, full))
    files.sort(key=lambda item: item[0].casefold())

    entries = []
    data_size = 0
    for rel, full in files:
        with open(full, "rb") as f:
            size = os.path.getsize(full)
        # VPK directory tree stores extension/path/file separately.
        slash = rel.rfind("/")
        fname = rel[slash + 1:]
        path_part = rel[:slash] if slash >= 0 else ""
        dot = fname.rfind(".")
        if dot > 0:
            base = fname[:dot]
            ext = fname[dot + 1:]
        else:
            base, ext = fname, ""
        crc = 0
        with open(full, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                crc = zlib.crc32(chunk, crc)
        entries.append((ext, path_part, base, crc & 0xffffffff, data_size, size, full))
        data_size += size

    # Build grouped VPK tree: extension -> path -> filename.
    from collections import defaultdict
    grouped = defaultdict(lambda: defaultdict(list))
    for item in entries:
        ext, path_part, base, crc, offset, size, full = item
        grouped[ext][path_part].append((base, crc, offset, size))

    tree = bytearray()
    for ext in sorted(grouped, key=lambda x: x.casefold()):
        tree.extend(ext.encode("utf-8")); tree.append(0)
        for path_part in sorted(grouped[ext], key=lambda x: x.casefold()):
            tree.extend((path_part if path_part else " ").encode("utf-8")); tree.append(0)
            for base, crc, offset, size in sorted(grouped[ext][path_part], key=lambda x: x[0].casefold()):
                tree.extend(base.encode("utf-8")); tree.append(0)
                tree.extend(struct.pack("<IHHIIH", crc, 0, 0x7FFF, offset, size, 0xFFFF))
            tree.append(0)
        tree.append(0)
    tree.extend(b"\x00")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp = output_path + ".rbvp_tmp"
    try:
        with open(tmp, "wb") as out:
            out.write(struct.pack("<III", 0x55AA1234, 1, len(tree)))
            out.write(tree)
            for _, _, _, _, _, _, full in entries:
                with open(full, "rb") as f:
                    shutil.copyfileobj(f, out, length=1024 * 1024)
        os.replace(tmp, output_path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return len(entries), data_size

