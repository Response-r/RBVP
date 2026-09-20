import os
import struct
import mmap

from .vpk import parse_vpk_entries, _vpk_archive_path

_VTX_BODY = struct.Struct("<ii")
_VTX_LOD = struct.Struct("<ii")
_VTX_LOD_HEAD = struct.Struct("<iif")
_VTX_MESH = struct.Struct("<iiB")
_VTX_SG = struct.Struct("<iiiiiiB")
_VTX_STRIP = struct.Struct("<iiiihBii")


def _valid_count(value, maximum=100_000_000):
    return 0 <= int(value) <= maximum


def parse_vvd_header(vvd_bytes):
    """
    返回 VVD 的关键头信息。
    VVD 头：ID + version + checksum + numLODs + numLODVertexes[8] + ...
    """
    if len(vvd_bytes) < 48:
        return None

    try:
        magic, version, checksum, num_lods = struct.unpack("<4siii", vvd_bytes[:16])
        if magic not in (b"IDSV", b"IDCV", b"IDDV"):
            return None
        if not (1 <= num_lods <= 8):
            return None

        num_lod_vertexes = struct.unpack("<8i", vvd_bytes[16:48])
        if any(v < 0 or v > 100_000_000 for v in num_lod_vertexes):
            return None

        return {
            "magic": magic,
            "version": version,
            "checksum": checksum,
            "num_lods": num_lods,
            "num_lod_vertexes": num_lod_vertexes,
        }
    except struct.error:
        return None


def parse_vvd_lod0_verts(vvd_bytes):
    header = parse_vvd_header(vvd_bytes)
    if not header:
        return 0
    return int(header["num_lod_vertexes"][0])


def _read_i32(data, offset):
    if offset < 0 or offset + 4 > len(data):
        return None
    return struct.unpack_from("<i", data, offset)[0]


def parse_mdl_lod0_vertex_info(mdl_bytes):
    """
    从 MDL 的 studiohdr_t / bodyparts / mstudiomodel_t 精确取模型顶点数。
    不再读取“猜测偏移”或用 numvertices // 2 伪造三角面。

    studiohdr_t 固定 408 字节；
    numbodyparts @ 232，bodypartindex @ 236；
    mstudiobodyparts_t = 16 字节；
    Source SDK 的 mstudiomodel_t(v44-v48) 采用 152 字节步长。
    """
    if len(mdl_bytes) < 408 or mdl_bytes[:4] != b"IDST":
        return {"vertices": 0, "models": 0}

    try:
        mdl_version = _read_i32(mdl_bytes, 4)
        bodypart_count = _read_i32(mdl_bytes, 232)
        bodypart_index = _read_i32(mdl_bytes, 236)

        if mdl_version is None or bodypart_count is None or bodypart_index is None:
            return {"vertices": 0, "models": 0}

        if bodypart_count < 0 or bodypart_count > 4096:
            return {"vertices": 0, "models": 0}
        if bodypart_count == 0:
            return {"vertices": 0, "models": 0}
        if bodypart_index < 408 or bodypart_index + bodypart_count * 16 > len(mdl_bytes):
            return {"vertices": 0, "models": 0}

        # Source 2013 studio.h 中 v44-v48 的 mstudiomodel_t 大小：
        # 64 + 11*4 + 12 + 8*4 = 152。
        MODEL_STRIDE = 152
        total_vertices = 0
        total_models = 0

        for body_i in range(bodypart_count):
            bp = bodypart_index + body_i * 16
            nummodels, _base, modelindex = struct.unpack_from("<ii i", mdl_bytes, bp + 4)

            if nummodels <= 0:
                continue
            if nummodels > 4096:
                return {"vertices": 0, "models": 0}

            model_base = bp + modelindex
            model_bytes_end = model_base + nummodels * MODEL_STRIDE
            if model_base < 0 or model_base >= len(mdl_bytes) or model_bytes_end > len(mdl_bytes):
                # 某些第三方/较旧结构可能不同，放弃这个 MDL fallback，
                # 让 VVD/VTX 结果继续作为主来源。
                return {"vertices": 0, "models": 0}

            for model_i in range(nummodels):
                model_ptr = model_base + model_i * MODEL_STRIDE

                # mstudiomodel_t：
                # nummeshes @ 72, meshindex @ 76,
                # numvertices @ 80
                numvertices = _read_i32(mdl_bytes, model_ptr + 80)
                if numvertices is None or not _valid_count(numvertices):
                    return {"vertices": 0, "models": 0}

                total_vertices += numvertices
                total_models += 1

        return {"vertices": total_vertices, "models": total_models}
    except (struct.error, ValueError):
        return {"vertices": 0, "models": 0}


def parse_mdl_header_data(mdl_bytes):
    """
    保留旧函数名以兼容现有代码。
    现在只返回真实 MDL 顶点数；绝不伪造三角面数。
    """
    info = parse_mdl_lod0_vertex_info(mdl_bytes)
    return int(info["vertices"]), 0


def _vtx_header(vtx_bytes):
    if len(vtx_bytes) < 36:
        return None

    try:
        fields = struct.unpack("<iiHHiiiiii", vtx_bytes[:36])
        (
            version,
            vert_cache_size,
            max_bones_per_strip,
            max_bones_per_tri,
            max_bones_per_vert,
            checksum,
            num_lods,
            mat_repl_offset,
            num_body_parts,
            body_part_offset,
        ) = fields

        if version not in (6, 7):
            return None
        if not (0 <= num_lods <= 8):
            return None
        if not (0 <= num_body_parts <= 4096):
            return None
        if num_body_parts and not (0 <= body_part_offset < len(vtx_bytes)):
            return None

        return {
            "version": version,
            "checksum": checksum,
            "num_lods": num_lods,
            "num_body_parts": num_body_parts,
            "body_part_offset": body_part_offset,
        }
    except struct.error:
        return None


def find_model_sidecar_entry(entries, base_rel, suffixes):
    """
    在 VPK 中按常见 Source 模型文件组合寻找 sidecar：
      <base>.vvd
      <base>.dx90.vtx / .dx80.vtx / .sw.vtx / .vtx
    """
    base_rel = base_rel.lower().replace("\\", "/")
    for suffix in suffixes:
        candidate = (base_rel + suffix).lower()
        if candidate in entries:
            return candidate
    return None


def parse_vtx_lod0_tris(vtx_bytes):
    """
    严格按照 Source OptimizedModel / optimize.h 的 packed(1) 布局读取：
      FileHeader  36 bytes
      BodyPart    8 bytes
      Model       8 bytes
      LOD         12 bytes
      Mesh        9 bytes
      StripGroup  25 bytes
      StripHeader 27 bytes
    """
    header = _vtx_header(vtx_bytes)
    if not header or header["num_body_parts"] == 0:
        return 0

    total_triangles = 0

    try:
        body_base = header["body_part_offset"]

        for bp_i in range(header["num_body_parts"]):
            bp_ptr = body_base + bp_i * 8
            if bp_ptr + 8 > len(vtx_bytes):
                return 0

            num_models, model_offset = _VTX_BODY.unpack_from(vtx_bytes, bp_ptr)
            if num_models < 0 or num_models > 4096:
                return 0

            model_base = bp_ptr + model_offset

            for m_i in range(num_models):
                m_ptr = model_base + m_i * 8
                if m_ptr + 8 > len(vtx_bytes):
                    return 0

                num_lods, lod_offset = _VTX_LOD.unpack_from(vtx_bytes, m_ptr)
                if num_lods <= 0:
                    continue
                if num_lods > 8:
                    return 0

                lod0_ptr = m_ptr + lod_offset
                if lod0_ptr + 12 > len(vtx_bytes):
                    return 0

                num_meshes, mesh_offset, _switch_point = _VTX_LOD_HEAD.unpack_from(vtx_bytes, lod0_ptr)
                if num_meshes < 0 or num_meshes > 65536:
                    return 0

                mesh_base = lod0_ptr + mesh_offset

                for mesh_i in range(num_meshes):
                    mesh_ptr = mesh_base + mesh_i * 9
                    if mesh_ptr + 9 > len(vtx_bytes):
                        return 0

                    num_strip_groups, strip_group_offset, _mesh_flags = _VTX_MESH.unpack_from(vtx_bytes, mesh_ptr)
                    if num_strip_groups < 0 or num_strip_groups > 65536:
                        return 0

                    sg_base = mesh_ptr + strip_group_offset

                    for sg_i in range(num_strip_groups):
                        sg_ptr = sg_base + sg_i * 25
                        if sg_ptr + 25 > len(vtx_bytes):
                            return 0

                        (
                            num_verts,
                            vert_offset,
                            num_indices,
                            index_offset,
                            num_strips,
                            strip_offset,
                            sg_flags,
                        ) = _VTX_SG.unpack_from(vtx_bytes, sg_ptr)

                        if not all(
                            _valid_count(v, 100_000_000)
                            for v in (num_verts, num_indices, num_strips)
                        ):
                            return 0

                        if num_strips == 0:
                            continue

                        strip_base = sg_ptr + strip_offset
                        for strip_i in range(num_strips):
                            strip_ptr = strip_base + strip_i * 27
                            if strip_ptr + 27 > len(vtx_bytes):
                                return 0

                            (
                                strip_num_indices,
                                strip_index_offset,
                                strip_num_verts,
                                strip_vert_offset,
                                num_bones,
                                strip_flags,
                                num_bone_state_changes,
                                bone_state_change_offset,
                            ) = _VTX_STRIP.unpack_from(vtx_bytes, strip_ptr)

                            if strip_num_indices < 0 or strip_num_indices > num_indices:
                                return 0

                            # Renderer 也按此二者区分 triangle list / triangle strip。
                            if strip_flags & 0x01:  # STRIP_IS_TRILIST
                                total_triangles += strip_num_indices // 3
                            elif strip_flags & 0x02:  # STRIP_IS_TRISTRIP
                                total_triangles += max(0, strip_num_indices - 2)
                            else:
                                # 未知拓扑绝不再猜测。
                                return 0

        return max(0, total_triangles)
    except (struct.error, ValueError, IndexError):
        return 0


def analyze_vpk_fast(vpk_path):
    """高性能 VPK 模型统计：mmap + 单 VPK 文件映射，减少系统调用与复制。"""
    vpk_name = os.path.basename(vpk_path)
    entries = parse_vpk_entries(vpk_path)
    if not entries:
        return None
    mdl_files = [k for k in entries if k.endswith('.mdl')]
    if not mdl_files:
        return None

    mappings = {}
    handles = {}
    archive_paths = {}

    def target_for_arch(arch_idx):
        target = archive_paths.get(arch_idx)
        if target:
            return target
        if arch_idx == 0x7FFF:
            target = vpk_path
        else:
            d, fn = os.path.split(vpk_path)
            prefix = fn[:-8] if fn.lower().endswith('_dir.vpk') else os.path.splitext(fn)[0]
            target = os.path.join(d, f'{prefix}_{arch_idx:03d}.vpk')
        archive_paths[arch_idx] = target
        return target

    def get_mapping(arch_idx):
        mm = mappings.get(arch_idx)
        if mm is not None:
            return mm
        target = target_for_arch(arch_idx)
        try:
            fh = open(target, 'rb')
            size = os.fstat(fh.fileno()).st_size
            if size <= 0:
                fh.close()
                return None
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
            handles[arch_idx] = fh
            mappings[arch_idx] = mm
            return mm
        except OSError:
            try: fh.close()
            except Exception: pass
            return None

    def read_entry(entry):
        length = int(entry.get('length', 0))
        preload = entry.get('preload', b'') or b''
        if length <= 0:
            return preload
        arch_idx = int(entry.get('arch_idx', 0x7FFF))
        mm = get_mapping(arch_idx)
        if mm is None:
            return b''
        offset = int(entry.get('offset', 0))
        if arch_idx == 0x7FFF:
            offset += int(entry.get('data_start', 0))
        if offset < 0 or offset + length > len(mm):
            return b''
        if preload:
            return preload + mm[offset:offset + length]
        return memoryview(mm)[offset:offset + length]

    models = []
    total_verts = total_tris = 0
    try:
        for mdl_rel in mdl_files:
            base = mdl_rel[:-4]
            verts = tris = 0
            vvd_entry = entries.get(base + '.vvd')
            if vvd_entry is not None:
                verts = parse_vvd_lod0_verts(read_entry(vvd_entry))
            vtx_entry = (entries.get(base + '.dx90.vtx') or entries.get(base + '.dx80.vtx')
                         or entries.get(base + '.sw.vtx') or entries.get(base + '.vtx'))
            if vtx_entry is not None:
                tris = parse_vtx_lod0_tris(read_entry(vtx_entry))
            if verts == 0:
                verts = int(parse_mdl_lod0_vertex_info(read_entry(entries[mdl_rel])).get('vertices', 0))
            total_verts += verts
            total_tris += tris
            models.append({'mdl_path': mdl_rel, 'verts': verts, 'tris': tris})
    finally:
        for mm in mappings.values():
            try: mm.close()
            except Exception: pass
        for fh in handles.values():
            try: fh.close()
            except Exception: pass

    models.sort(key=lambda m:(m['tris'],m['verts'],m['mdl_path'].casefold()), reverse=True)
    return {'vpk':vpk_name,'models':models,'mdl_count':len(mdl_files),'verts':total_verts,'tris':total_tris}

