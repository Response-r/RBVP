"""Batch reduction core for 3ds Max 2018 + built-in ProOptimizer.

The batch reducer deliberately targets only loose MDLs under an Addons tree that:
- live under a ``models`` directory;
- are outside models/v_models, models/w_models and models/weapons;
- have STUDIOHDR_FLAGS_STATIC_PROP set;
- have more than the configured minimum LOD0 triangle count.

3ds Max is driven through one dedicated per-batch MaxScript worker session.  The
worker consumes ASCII task files whose path values are UTF-8/base64 encoded, so
Chinese Addons/Mod paths do not have to pass through MaxScript source literals.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import shutil
import struct
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List

from .models import parse_vtx_lod0_tris
from .crowbar import CrowbarError

WHITELIST_MODEL_DIRS = {
    ("models", "v_models"),
    ("models", "w_models"),
    ("models", "weapons"),
}

STUDIOHDR_FLAGS_STATIC_PROP = 1 << 4
STATICPROP_FLAGS_OFFSET = 152


_BATCH_DONE_FILENAME = ".rbvp_batch_reduce_done.json"
_BATCH_DONE_VERSION = 2
_BATCH_FAILED_FILENAME = ".rbvp_batch_reduce_failed.json"
_BATCH_FAILED_VERSION = 1


class MaxAssertionError(RuntimeError):
    """Fatal 3ds Max assertion: abort the current MDL and rebuild the Max session."""




def _batch_done_file(addons_dir: str) -> Path:
    return Path(addons_dir).resolve() / _BATCH_DONE_FILENAME


def _batch_done_key(relative_path: str) -> str:
    return os.path.normcase(str(relative_path).replace("\\", "/")).casefold()


def _file_signature(path: str, *, with_sha256: bool = False) -> dict:
    try:
        st = os.stat(path)
    except OSError:
        return {"size": None, "mtime_ns": None, "sha256": None if with_sha256 else ""}

    result = {
        "size": int(st.st_size),
        "mtime_ns": int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))),
    }
    if with_sha256:
        import hashlib
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)
            result["sha256"] = digest.hexdigest()
        except OSError:
            result["sha256"] = None
    return result


def _source_signature(mdl_path: str) -> dict:
    """Fast signature retained for compatibility with older records."""
    return _file_signature(mdl_path, with_sha256=False)


def _result_signature(mdl_path: str) -> dict:
    """Strong post-process signature used to remember a completed MDL."""
    return _file_signature(mdl_path, with_sha256=True)


def load_batch_reduce_records(addons_dir: str) -> dict:
    path = _batch_done_file(addons_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def is_batch_reduce_done(addons_dir: str, relative_path: str, mdl_path: str, profile: dict | None = None) -> bool:
    """Return True when the current MDL matches a previously successful result.

    The record is based on the SHA-256 of the *post-reduction* MDL, not the
    pre-reduction MDL. This is critical because a successful batch operation
    overwrites the original MDL with the newly compiled file.
    """
    if not relative_path or not os.path.isfile(mdl_path):
        return False

    data = load_batch_reduce_records(addons_dir)
    rec = data.get(_batch_done_key(relative_path))
    if not isinstance(rec, dict):
        return False

    expected = rec.get("result")
    if not isinstance(expected, dict) or not expected.get("sha256"):
        # v1 records used the pre-process source signature and are intentionally
        # not treated as complete, because that signature cannot describe the
        # currently overwritten MDL reliably.
        return False

    current = _result_signature(mdl_path)
    if current.get("sha256") != expected.get("sha256"):
        return False

    recorded_profile = rec.get("profile")
    if profile is not None and isinstance(recorded_profile, dict):
        return recorded_profile == profile
    return True


def mark_batch_reduce_done(
    addons_dir: str,
    relative_path: str,
    mdl_path: str,
    profile: dict | None = None,
    source_before: dict | None = None,
) -> None:
    """Persist a successful MDL completion atomically."""
    path = _batch_done_file(addons_dir)
    data = load_batch_reduce_records(addons_dir)
    if not data or "_meta" not in data:
        data = {} if not isinstance(data, dict) else data
        data.setdefault("_meta", {})
    data["_meta"] = {
        "version": _BATCH_DONE_VERSION,
        "updated": time.time(),
    }
    data[_batch_done_key(relative_path)] = {
        "relative_path": str(relative_path).replace("\\", "/"),
        "source_before": source_before or {},
        "result": _result_signature(mdl_path),
        "profile": dict(profile or {}),
        "updated": time.time(),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _batch_failed_file(addons_dir: str) -> Path:
    return Path(addons_dir).resolve() / _BATCH_FAILED_FILENAME


def load_batch_reduce_failures(addons_dir: str) -> dict:
    path = _batch_failed_file(addons_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def mark_batch_reduce_failed(
    addons_dir: str, relative_path: str, reason: str, stage: str = "UNKNOWN", detail: str = ""
) -> None:
    """Persist the latest failure for a model without making it permanently skipped."""
    path = _batch_failed_file(addons_dir)
    data = load_batch_reduce_failures(addons_dir)
    data.setdefault("_meta", {})
    data["_meta"] = {"version": _BATCH_FAILED_VERSION, "updated": time.time()}
    data[_batch_done_key(relative_path)] = {
        "relative_path": str(relative_path).replace("\\", "/"),
        "reason": str(reason),
        "stage": str(stage),
        "detail": str(detail or ""),
        "updated": time.time(),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def clear_batch_reduce_failure(addons_dir: str, relative_path: str) -> None:
    path = _batch_failed_file(addons_dir)
    data = load_batch_reduce_failures(addons_dir)
    key = _batch_done_key(relative_path)
    if key not in data:
        return
    data.pop(key, None)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def is_whitelisted_model_path(relative_path: str) -> bool:
    parts = tuple(p.casefold() for p in Path(str(relative_path).replace("\\", "/")).parts)
    return any((parts[i], parts[i + 1]) in WHITELIST_MODEL_DIRS for i in range(len(parts) - 1))


def is_staticprop_mdl(mdl_bytes: bytes) -> bool:
    if len(mdl_bytes) < STATICPROP_FLAGS_OFFSET + 4 or mdl_bytes[:4] != b"IDST":
        return False
    try:
        flags = struct.unpack_from("<i", mdl_bytes, STATICPROP_FLAGS_OFFSET)[0]
        return bool(flags & STUDIOHDR_FLAGS_STATIC_PROP)
    except struct.error:
        return False


def _find_vtx_for_mdl(mdl_path: str) -> str | None:
    base = os.path.splitext(mdl_path)[0]
    for suffix in (".dx90.vtx", ".dx80.vtx", ".sw.vtx", ".vtx"):
        candidate = base + suffix
        if os.path.isfile(candidate):
            return candidate
    return None


def _safe_triangle_count(mdl_path: str) -> int:
    vtx = _find_vtx_for_mdl(mdl_path)
    if not vtx:
        return 0
    try:
        with open(vtx, "rb") as f:
            return int(parse_vtx_lod0_tris(f.read()))
    except OSError:
        return 0


def scan_batch_reduce_mdls(addons_dir: str, min_faces: int = 10) -> List[Dict[str, object]]:
    """Find eligible loose MDLs under Addons.

    Eligibility:
      * path must be inside a ``models`` tree;
      * models/v_models, models/w_models and models/weapons are excluded;
      * compiled MDL must carry STUDIOHDR_FLAGS_STATIC_PROP;
      * LOD0 triangle count must be strictly greater than ``min_faces``.
    """
    addons_dir = os.path.abspath(addons_dir)
    results: List[Dict[str, object]] = []
    if not os.path.isdir(addons_dir):
        return results

    for root, dirs, files in os.walk(addons_dir):
        dirs[:] = [d for d in dirs if d.casefold() != "temp" and not d.startswith(".")]
        rel_root = os.path.relpath(root, addons_dir)
        root_parts = tuple(p.casefold() for p in Path(rel_root).parts if p not in (".", ""))
        if any(root_parts[i:i + 2] in WHITELIST_MODEL_DIRS for i in range(len(root_parts) - 1)):
            dirs[:] = []
            continue
        if "models" not in root_parts:
            continue

        for name in files:
            if not name.casefold().endswith(".mdl"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, addons_dir).replace(os.sep, "/")
            if is_whitelisted_model_path(rel):
                continue
            try:
                with open(path, "rb") as f:
                    header = f.read(160)
                if not is_staticprop_mdl(header):
                    continue
                triangles = _safe_triangle_count(path)
                if triangles <= int(min_faces):
                    continue
                results.append({
                    "mdl_path": path,
                    "relative_path": rel,
                    "triangles": triangles,
                })
            except OSError:
                continue

    results.sort(key=lambda x: (-int(x["triangles"]), str(x["relative_path"]).casefold()))
    return results


def _candidate_3dsmax_exes() -> List[str]:
    candidates: List[str] = []
    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    ]
    for root in roots:
        candidates.extend([
            str(root / "Autodesk" / "3ds Max 2018" / "3dsmax.exe"),
            str(root / "Autodesk" / "3ds Max 2019" / "3dsmax.exe"),
            str(root / "Autodesk" / "3ds Max 2020" / "3dsmax.exe"),
        ])
        for vendor_root in (root / "Autodesk", root / "Autodesk 3ds Max"):
            try:
                if vendor_root.is_dir():
                    for child in vendor_root.iterdir():
                        if child.is_dir() and child.name.casefold().startswith("3ds max"):
                            candidates.append(str(child / "3dsmax.exe"))
            except OSError:
                pass

    # Common custom/portable layouts, including the user's previously verified
    # D:\\3dmax\\3ds Max 2018 installation style.
    for drive in "CDEFGH":
        base = Path(f"{drive}:/")
        candidates.extend([
            str(base / "3dsmax" / "3dsmax.exe"),
            str(base / "3dmax" / "3ds Max 2018" / "3dsmax.exe"),
            str(base / "3dmax" / "3ds Max 2019" / "3dsmax.exe"),
            str(base / "3dsmax" / "3ds Max 2018" / "3dsmax.exe"),
            str(base / "Autodesk" / "3ds Max 2018" / "3dsmax.exe"),
            str(base / "Program Files" / "Autodesk" / "3ds Max 2018" / "3dsmax.exe"),
            str(base / "Program Files (x86)" / "Autodesk" / "3ds Max 2018" / "3dsmax.exe"),
        ])
        for top in ("3dmax", "3dsmax", "Autodesk"):
            folder = base / top
            try:
                if not folder.is_dir():
                    continue
                for child in folder.iterdir():
                    if child.is_dir() and "3ds max" in child.name.casefold():
                        candidates.append(str(child / "3dsmax.exe"))
            except OSError:
                pass
    return candidates


def find_3dsmax_exe() -> str:
    seen: set[str] = set()
    for candidate in _candidate_3dsmax_exes():
        q = os.path.abspath(os.path.expandvars(os.path.expanduser(candidate)))
        key = os.path.normcase(q)
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(q):
            return q
    return ""


def _runtime_root() -> Path:
    for raw in (os.environ.get("ProgramData", r"C:\ProgramData"), r"C:\RBVP_Runtime"):
        root = Path(raw) / "RBVP" / "MaxReduce"
        try:
            root.mkdir(parents=True, exist_ok=True)
            return root
        except OSError:
            continue
    fallback = Path.cwd() / "_rbvp_max_reduce_runtime"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _b64(text: str) -> str:
    return base64.b64encode(str(text).encode("utf-8")).decode("ascii")


def _write_task(path: Path, *, input_smd: str, output_smd: str, reduce_percent: float,
                smooth_enabled: bool, smooth_auto: bool, smooth_angle: float) -> None:
    data = "\n".join([
        f"INPUT_B64={_b64(input_smd)}",
        f"OUTPUT_B64={_b64(output_smd)}",
        f"REDUCE={float(reduce_percent):.4f}",
        f"SMOOTH={1 if smooth_enabled else 0}",
        f"AUTO={1 if smooth_auto else 0}",
        f"ANGLE={float(smooth_angle):.4f}",
        "",
    ])
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _maxscript_escape_path(path: str) -> str:
    return path.replace("\\", "/").replace('"', '\\"')


def _write_max_worker_script(script_path: Path, queue_dir: Path, ready_path: Path) -> None:
    q = _b64(str(queue_dir))
    r = _b64(str(ready_path))
    script = f'''/* RBVP dedicated 3ds Max 2018 worker - ProOptimizer */
global rbvpQueueDirB64 = "{q}"
global rbvpReadyPathB64 = "{r}"

global rbvpConvert = dotNetClass "System.Convert"
global rbvpUtf8 = dotNetClass "System.Text.Encoding"
global rbvpQueueDir = rbvpUtf8.UTF8.GetString (rbvpConvert.FromBase64String rbvpQueueDirB64)
global rbvpReadyPath = rbvpUtf8.UTF8.GetString (rbvpConvert.FromBase64String rbvpReadyPathB64)
'''
    # MaxScript does not accept the typo-prone "nglobal" token; keep the actual
    # worker body separate to make the generated source easy to review.
    script = script.replace("nglobal rbvpReadyPath", "global rbvpReadyPath")
    script += r'''

fn rbvpReadAllLines p = (
    local f = openFile p mode:"r"
    local lines = #()
    if f != undefined do (
        while not eof f do append lines (readLine f)
        close f
    )
    lines
)

fn rbvpGetValue lines key = (
    local prefix = (key + "=")
    for line in lines do (
        if matchPattern line pattern:(prefix + "*") then
            return substring line (prefix.count + 1) (line.count - prefix.count)
    )
    ""
)

fn rbvpWriteStatus p text = (
    try (
        local tmp = p + ".tmp"
        local f = createFile tmp
        format "%" text to:f
        close f
        if doesFileExist p do deleteFile p
        renameFile tmp p
    ) catch()
)

fn rbvpProcessTask taskPath = (
    local base = getFilenameFile taskPath
    local statusPath = (getFilenamePath taskPath) + base + ".status"
    rbvpWriteStatus statusPath "RUNNING\n"
    local lines = rbvpReadAllLines taskPath
    local inputB64 = rbvpGetValue lines "INPUT_B64"
    local outputB64 = rbvpGetValue lines "OUTPUT_B64"
    local inputSmd = ""
    local outputSmd = ""
    try (if inputB64 != "" do inputSmd = rbvpUtf8.UTF8.GetString (rbvpConvert.FromBase64String inputB64)) catch()
    try (if outputB64 != "" do outputSmd = rbvpUtf8.UTF8.GetString (rbvpConvert.FromBase64String outputB64)) catch()
    local reducePercent = (rbvpGetValue lines "REDUCE") as float
    local smoothEnabled = ((rbvpGetValue lines "SMOOTH") as integer) != 0
    local smoothAuto = ((rbvpGetValue lines "AUTO") as integer) != 0
    local smoothAngle = (rbvpGetValue lines "ANGLE") as float

    try (
        rbvpWriteStatus statusPath "STEP=RESET\n"
        resetMaxFile #noPrompt

        rbvpWriteStatus statusPath "STEP=IMPORT\n"
        -- 记录导入前的几何对象，避免 3DSMAX 用户启动文件中已有对象干扰计数。
        -- 只在导入成功后从“新增对象”中寻找 SMD 几何体。
        local beforeGeometry = for o in objects where (superClassOf o == GeometryClass) collect o
        if not (importFile inputSmd #noPrompt) do throw "SMD_IMPORT_FAILED"

        local models = for o in objects where ((superClassOf o == GeometryClass) and (findItem beforeGeometry o == 0)) collect o
        if models.count == 0 do throw "SMD_GEOMETRY_COUNT=0"

        -- 只选择真正的模型网格：优先选择带 Skin 的新增几何体，
        -- 避免把 SMD 导入器生成的骨骼/辅助对象误当成减面目标。
        local skinnedModels = for o in models where (
            for m in o.modifiers where (classof m == Skin) collect m
        ).count > 0 collect o
        local candidateModels = if skinnedModels.count > 0 then skinnedModels else models
        local model = candidateModels[1]
        if candidateModels.count > 1 do (
            local bestVerts = -1
            for o in candidateModels do (
                local v = try (polyOp.getNumVerts o) catch (try (o.mesh.numVerts) catch (0))
                if v > bestVerts do (bestVerts = v; model = o)
            )
        )

        -- 严格按验证过的流程：先保存 Skin，再删除 Skin，之后才允许转换模型。
        local skinMod = undefined
        local skinIndex = 0
        for i = 1 to model.modifiers.count do (
            if (classof model.modifiers[i] == Skin) then (
                skinMod = model.modifiers[i]
                skinIndex = i
                exit
            )
        )
        local skinCopy = undefined
        if skinMod != undefined do skinCopy = copy skinMod
        if skinIndex > 0 do deleteModifier model skinIndex

        if not (classof model == Editable_Poly) do (
            try (
                convertToPoly model
            ) catch (
                rbvpWriteStatus statusPath "ERROR=MODEL_CONVERT_TO_POLY_FAILED\n"
                throw()
            )
        )

        rbvpWriteStatus statusPath "STEP=PROOPTIMIZER\n"
        local po = ProOptimizer()
        addModifier model po
        po = model.modifiers[1]
        po.KeepUV = true
        po.KeepNormals = true
        -- ProOptimizer 必须先 Calculate 初始化，之后 VertexPercent 才进入可调整状态；
        -- 设置目标比例后再次 Calculate 才真正生成优化结果。
        select model
        max modify mode
        try (modPanel.setCurrentObject po) catch()
        po.Calculate = true
        po.VertexPercent = reducePercent
        po.Calculate = true
        try (model.update()) catch()

        if smoothEnabled do (
            rbvpWriteStatus statusPath "STEP=SMOOTH\n"
            local sm = Smooth()
            sm.autosmooth = smoothAuto
            if smoothAuto do sm.threshold = smoothAngle
            addModifier model sm
        )

        rbvpWriteStatus statusPath "STEP=COLLAPSE\n"
        collapseStack model

        if skinCopy != undefined do (
            rbvpWriteStatus statusPath "STEP=RESTORE_SKIN\n"
            addModifier model skinCopy
        )

        rbvpWriteStatus statusPath "STEP=EXPORT\n"
        local parentDir = getFilenamePath outputSmd
        if not doesDirectoryExist parentDir do makeDir parentDir
        if doesFileExist outputSmd do deleteFile outputSmd

        -- 只导出减面后的模型 + 当前 Skin 实际引用的原骨骼。
        -- 绝不导出其它辅助对象，也绝不把骨骼转换成 Editable Poly。
        clearSelection()
        select model
        local exportSkin = undefined
        for m in model.modifiers do (
            if (classof m == Skin) then (exportSkin = m; exit)
        )
        if exportSkin != undefined do (
            local boneCount = try (skinOps.GetNumberBones exportSkin) catch (0)
            for bi = 1 to boneCount do (
                local boneName = try (skinOps.GetBoneName exportSkin bi 1) catch ("")
                if boneName != "" do (
                    local boneNode = getNodeByName boneName
                    if boneNode != undefined do selectMore boneNode
                )
            )
        )
        if not (exportFile outputSmd #noPrompt selectedOnly:true) do throw "SMD_EXPORT_FAILED"
        if not doesFileExist outputSmd do throw "SMD_EXPORT_FILE_MISSING"

        rbvpWriteStatus statusPath "DONE\n"
    ) catch err (
        local enc = dotNetClass "System.Convert"
        local errText = try (err as string) catch ("UNKNOWN MAXSCRIPT ERROR")
        local errB64 = enc.ToBase64String ((dotNetClass "System.Text.Encoding").UTF8.GetBytes errText)
        rbvpWriteStatus statusPath ("ERROR_B64=" + errB64 + "\n")
    )

    try (deleteFile taskPath) catch()
)

try (rbvpWriteStatus rbvpReadyPath "READY\n") catch()

while true do (
    try (
        local tasks = getFiles (rbvpQueueDir + "\\*.task")
        if tasks.count > 0 then (
            sort tasks
            rbvpProcessTask tasks[1]
        )
    ) catch err ()
    try (windows.processPostedMessages()) catch()
    sleep 0.03
)
'''
    script_path.write_text(script, encoding="utf-8", newline="\n")


def _start_max_worker(exe_path: str, runtime_dir: Path) -> tuple[subprocess.Popen, Path, Path]:
    queue_dir = runtime_dir / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    # Clear stale task/status files from previous interrupted sessions.
    for p in queue_dir.glob("*"):
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            pass
    script_path = runtime_dir / "rbvp_max_worker.ms"
    ready_path = runtime_dir / "worker.ready"
    try:
        ready_path.unlink()
    except OSError:
        pass
    _write_max_worker_script(script_path, queue_dir, ready_path)
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(
        [exe_path, "-U", "MAXScript", str(script_path)],
        cwd=os.path.dirname(exe_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        if ready_path.exists():
            return proc, queue_dir, ready_path
        if proc.poll() is not None:
            raise RuntimeError(f"3DSMAX 启动失败（返回码 {proc.returncode}）。")
        time.sleep(0.10)
    raise RuntimeError("3DSMAX 已启动，但在 90 秒内没有建立 MaxScript 工作会话。")


def _terminate_max_worker(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        pid = int(proc.pid)
    except Exception:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
            )
        else:
            proc.terminate()
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def _read_max_status(status_path: Path) -> tuple[str, str]:
    try:
        text = status_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""
    if text.startswith("DONE"):
        return "DONE", ""
    if text.startswith("ERROR_B64="):
        b64 = text.split("=", 1)[1].strip().splitlines()[0]
        try:
            return "ERROR", base64.b64decode(b64).decode("utf-8", errors="replace")
        except Exception:
            return "ERROR", "MaxScript 处理失败。"
    line = text.splitlines()[0] if text.splitlines() else ""
    return "RUNNING", line



def _find_max_assertion_dialogs(max_pid: int | None) -> int:
    """Return number of visible assertion/fatal dialogs owned by the active Max PID."""
    if os.name != "nt" or not max_pid:
        return 0
    try:
        user32 = ctypes.windll.user32
        from ctypes import wintypes
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        EnumChildProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        get_title = user32.GetWindowTextW
        get_title.argtypes = [ctypes.c_void_p, wintypes.LPWSTR, ctypes.c_int]
        get_title.restype = ctypes.c_int
        get_class = user32.GetClassNameW
        get_class.argtypes = [ctypes.c_void_p, wintypes.LPWSTR, ctypes.c_int]
        get_class.restype = ctypes.c_int
        get_pid = user32.GetWindowThreadProcessId
        get_pid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        get_pid.restype = ctypes.c_uint32
        is_visible = user32.IsWindowVisible
        is_visible.argtypes = [ctypes.c_void_p]
        is_visible.restype = ctypes.c_bool
        assertion_hits = []

        def read_text(hwnd):
            buf = ctypes.create_unicode_buffer(2048)
            try:
                get_title(hwnd, buf, len(buf))
                return buf.value
            except Exception:
                return ""

        def enum_cb(hwnd, _):
            if not is_visible(hwnd):
                return True
            pid = ctypes.c_uint32()
            get_pid(hwnd, ctypes.byref(pid))
            if int(pid.value) != int(max_pid):
                return True
            cls_buf = ctypes.create_unicode_buffer(128)
            get_class(hwnd, cls_buf, 128)
            cls = cls_buf.value.casefold()
            if cls != "#32770":
                return True
            texts = [read_text(hwnd)]
            def child_cb(chwnd, __):
                if is_visible(chwnd):
                    txt = read_text(chwnd)
                    if txt:
                        texts.append(txt)
                return True
            try:
                user32.EnumChildWindows(hwnd, EnumChildProc(child_cb), 0)
            except Exception:
                pass
            blob = " ".join(texts).casefold()
            hints = (
                "assertion error", "assertion failed", "assertion", "断言错误", "断言失败",
                "tab<class inode", "i>=0", "try to save and exit"
            )
            if any(h in blob for h in hints):
                assertion_hits.append(hwnd)
            return True

        user32.EnumWindows(EnumWindowsProc(enum_cb), 0)
        return len(assertion_hits)
    except Exception:
        return 0


def _max_assertion_watchdog(max_pid: int | None, proc, stop_event=None, interval: float = 0.12):
    """Detect a 3ds Max assertion and immediately terminate that Max session."""
    done = threading.Event()
    assertion = threading.Event()

    def worker():
        while not done.is_set():
            if stop_event is not None and stop_event.is_set():
                break
            if _find_max_assertion_dialogs(max_pid):
                assertion.set()
                try:
                    _terminate_max_worker(proc)
                except Exception:
                    pass
                break
            done.wait(interval)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return done, t, assertion



def reduce_smd_with_max(worker_queue: Path, job_id: str, input_smd: str, output_smd: str,
                        reduce_percent: float, smooth_enabled: bool, smooth_auto: bool, smooth_angle: float,
                        timeout: int = 900, stop_event=None, max_pid: int | None = None, max_proc=None) -> None:
    task_path = worker_queue / f"{job_id}.task"
    status_path = worker_queue / f"{job_id}.status"
    for p in (task_path, status_path):
        try:
            p.unlink()
        except OSError:
            pass
    _write_task(
        task_path,
        input_smd=input_smd,
        output_smd=output_smd,
        reduce_percent=reduce_percent,
        smooth_enabled=smooth_enabled,
        smooth_auto=smooth_auto,
        smooth_angle=smooth_angle,
    )
    watchdog_done, watchdog_thread, assertion_event = _max_assertion_watchdog(max_pid, max_proc, stop_event=stop_event)
    deadline = time.time() + max(60, int(timeout))
    try:
        while time.time() < deadline:
            if stop_event is not None and stop_event.is_set():
                raise RuntimeError("用户已终止处理。")
            if assertion_event.is_set():
                raise MaxAssertionError("3DSMAX ASSERTION ERROR：已终止当前模型并重建工作会话。")
            state, detail = _read_max_status(status_path)
            if state == "DONE":
                if not os.path.isfile(output_smd):
                    raise RuntimeError("3DSMAX 报告完成，但没有找到导出的 SMD。")
                return
            if state == "ERROR":
                raise RuntimeError(detail or "3DSMAX 批量减面失败。")
            if stop_event is not None:
                stop_event.wait(0.05)
            else:
                time.sleep(0.05)
        raise RuntimeError("3DSMAX 单个模型处理超时。")
    finally:
        watchdog_done.set()
        try:
            watchdog_thread.join(timeout=0.3)
        except Exception:
            pass



def _read_qc_text(qc_path: str) -> str:
    qc = Path(qc_path).resolve()
    try:
        return qc.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise CrowbarError(f"无法读取反编译 QC：{qc}") from exc


def _resolve_qc_smd_token(qc: Path, root: Path, token: str) -> Path | None:
    token = token.replace("\\", "/").strip()
    if not token or "*" in token or "?" in token:
        return None
    candidate = (qc.parent / token).resolve()
    candidates = [candidate]
    if not candidate.is_file() and root in qc.parents:
        candidates.append((root / token).resolve())
    for c in candidates:
        if c.is_file():
            return c
    return None


def resolve_model_smds_from_qc(qc_path: str, decompiled_root: str) -> list[str]:
    """Resolve every model SMD referenced by $body/$model/$bodygroup.

    $body/$model carry the SMD on their directive line; $bodygroup carries
    ``studio "...smd"`` inside its brace block.  Animation/sequence SMDs are
    intentionally *not* returned for ProOptimizer processing, but are still
    validated by Crowbar's decompile-complete check elsewhere.
    """
    qc = Path(qc_path).resolve()
    root = Path(decompiled_root).resolve()
    text = _read_qc_text(qc_path)
    lines = text.splitlines()
    out: list[str] = []
    seen: set[str] = set()
    in_bodygroup = False
    bodygroup_balance = 0

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        low = stripped.casefold()
        if low.startswith("$lod"):
            # LOD blocks are removed before model-SMD resolution.
            in_bodygroup = False
            bodygroup_balance = 0
            continue

        if re.match(r"^\s*\$bodygroup\b", line, flags=re.IGNORECASE):
            in_bodygroup = True
            bodygroup_balance = line.count("{") - line.count("}")
            # Most QC files put the opening brace on the following line. In
            # that case keep the bodygroup state alive until its closing brace.
            if bodygroup_balance < 0:
                in_bodygroup = False
                bodygroup_balance = 0
            continue

        if in_bodygroup:
            smds = re.findall(r'"([^\"]+?\.smd)"', stripped, flags=re.IGNORECASE)
            if re.match(r"^\s*studio\b", line, flags=re.IGNORECASE):
                for token in smds:
                    picked = _resolve_qc_smd_token(qc, root, token)
                    if picked is not None:
                        key = os.path.normcase(str(picked))
                        if key not in seen:
                            seen.add(key)
                            out.append(str(picked))
            bodygroup_balance += line.count("{") - line.count("}")
            if bodygroup_balance <= 0:
                in_bodygroup = False
            continue

        if re.match(r"^\s*\$(?:body|model)\b", line, flags=re.IGNORECASE):
            for token in re.findall(r'"([^\"]+?\.smd)"', stripped, flags=re.IGNORECASE):
                picked = _resolve_qc_smd_token(qc, root, token)
                if picked is not None:
                    key = os.path.normcase(str(picked))
                    if key not in seen:
                        seen.add(key)
                        out.append(str(picked))

    if not out:
        raise CrowbarError(f"QC 已找到，但无法从 $body/$model/$bodygroup 中找到实际模型 SMD：{qc.name}。")
    return out


def resolve_model_smd_from_qc(qc_path: str, decompiled_root: str) -> str:
    """Backward-compatible first-model resolver."""
    return resolve_model_smds_from_qc(qc_path, decompiled_root)[0]

def _strip_qc_lod_blocks(qc_path: str) -> list[str]:
    """Remove explicit QC $lod blocks and their replacemodel target SMDs."""
    qc = Path(qc_path).resolve()
    try:
        text = qc.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise CrowbarError(f"无法读取 QC 进行 LOD 清理：{qc}") from exc

    lines = text.splitlines(keepends=True)
    if not any(re.match(r"^\s*\$lod\b", line, flags=re.IGNORECASE) for line in lines):
        return []

    kept: list[str] = []
    lod_targets: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not re.match(r"^\s*\$lod\b", line, flags=re.IGNORECASE):
            kept.append(line)
            i += 1
            continue

        block = [line]
        balance = line.count("{") - line.count("}")
        j = i + 1
        if balance <= 0:
            while j < len(lines) and "{" not in "".join(block):
                block.append(lines[j])
                balance += lines[j].count("{") - lines[j].count("}")
                j += 1
        while j < len(lines) and balance > 0:
            block.append(lines[j])
            balance += lines[j].count("{") - lines[j].count("}")
            j += 1

        for block_line in block:
            if not re.match(r"^\s*replacemodel\b", block_line, flags=re.IGNORECASE):
                continue
            smds = re.findall(r'"([^\"]+?\.smd)"', block_line, flags=re.IGNORECASE)
            if len(smds) >= 2:
                target = smds[1].replace("\\", os.sep).replace("/", os.sep)
                lod_targets.append(str((qc.parent / target).resolve()))
        i = max(j, i + 1)

    tmp = qc.with_suffix(qc.suffix + ".lod.tmp")
    try:
        tmp.write_text("".join(kept), encoding="utf-8", newline="")
        os.replace(tmp, qc)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise CrowbarError(f"无法写回清理后的 QC：{qc}") from exc

    removed: list[str] = []
    seen: set[str] = set()
    for target in lod_targets:
        key = os.path.normcase(target)
        if key in seen:
            continue
        seen.add(key)
        try:
            if os.path.isfile(target):
                os.remove(target)
                removed.append(target)
        except OSError as exc:
            raise CrowbarError(f"无法删除 LOD SMD：{target}") from exc
    return removed

def prepare_staging_smd(source_smd: str, runtime_dir: Path, job_id: str) -> tuple[str, str]:
    # 反编译出的主 SMD 已经是本地文件，Max 仅执行读取/导入；无需再次复制一份输入。
    # 仍将输出放入独立任务目录，避免 Max 写回原始 SMD 时与后续覆盖逻辑产生竞争。
    job_dir = runtime_dir / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(source_smd).stem
    output_smd = job_dir / f"{stem}_rbvp_out.smd"
    return str(Path(source_smd).resolve()), str(output_smd)
