"""Crowbar / Source Model toolchain helpers for texture-group cleanup.

The official Crowbar GUI is primarily a desktop application. For unattended
workflows this module can automate the standard Crowbar GUI, while using the
configured NekoMDL compiler for the compile phase.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
import threading
from pathlib import Path
from typing import Iterable, Optional, Tuple


class CrowbarError(RuntimeError):
    pass


# Crowbar 是 GUI 单实例程序；重复启动/退出是整个批处理最昂贵的固定开销之一。
# 缓存当前会话的主 PID，只在失效时重新发现。整个清除任务仍然是串行的，因此
# 不需要复杂的多实例调度。
_CROWBAR_SESSION_LOCK = threading.RLock()
_CROWBAR_SESSION_PIDS: dict[str, int] = {}

# Win32 DLL/函数缓存：这些对象在 300~1000 个样本批处理时会被调用数万次。
_USER32 = None
_KERNEL32 = None

def _win32():
    """返回缓存的 user32/kernel32，避免在每次轮询时重复 WinDLL 加载。"""
    global _USER32, _KERNEL32
    import ctypes
    if _USER32 is None:
        _USER32 = ctypes.WinDLL("user32", use_last_error=True)
    if _KERNEL32 is None:
        _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    return _USER32, _KERNEL32


def _crowbar_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expandvars(os.path.expanduser(path))))


def _pid_alive(pid: int) -> bool:
    try:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        _user32, kernel32 = _win32()
        OpenProcess = kernel32.OpenProcess
        GetExitCodeProcess = kernel32.GetExitCodeProcess
        CloseHandle = kernel32.CloseHandle
        h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = bool(GetExitCodeProcess(h, ctypes.byref(code)))
        CloseHandle(h)
        return ok and int(code.value) == STILL_ACTIVE
    except Exception:
        return False


def _get_cached_crowbar_pid(crowbar_path: str) -> int:
    key = _crowbar_key(crowbar_path)
    with _CROWBAR_SESSION_LOCK:
        pid = int(_CROWBAR_SESSION_PIDS.get(key, 0) or 0)
        if pid and _pid_alive(pid):
            return pid
        _CROWBAR_SESSION_PIDS.pop(key, None)
        return 0


def _remember_crowbar_pid(crowbar_path: str, pid: int) -> None:
    if not pid:
        return
    with _CROWBAR_SESSION_LOCK:
        _CROWBAR_SESSION_PIDS[_crowbar_key(crowbar_path)] = int(pid)


def _forget_crowbar_pid(crowbar_path: str, pid: int | None = None) -> None:
    key = _crowbar_key(crowbar_path)
    with _CROWBAR_SESSION_LOCK:
        old = _CROWBAR_SESSION_PIDS.get(key)
        if pid is None or old == pid:
            _CROWBAR_SESSION_PIDS.pop(key, None)


def _bring_window_to_front(hwnd: int) -> bool:
    """尽最大可能把 Crowbar 主窗口唤到前台。"""
    try:
        import ctypes
        from ctypes import wintypes
        user32, _ = _win32()
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        # AttachThreadInput 可以绕过部分 Windows 前台窗口限制。
        fg = int(user32.GetForegroundWindow() or 0)
        target_tid = int(user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wintypes.DWORD())))
        current_tid = int(user32.GetCurrentThreadId())
        attached = False
        if fg and target_tid and current_tid and target_tid != current_tid:
            fg_tid = int(user32.GetWindowThreadProcessId(fg, ctypes.byref(wintypes.DWORD())))
            if fg_tid and user32.AttachThreadInput(current_tid, fg_tid, True):
                attached = True
            if target_tid and user32.AttachThreadInput(current_tid, target_tid, True):
                attached = True
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if attached:
            try:
                if fg_tid:
                    user32.AttachThreadInput(current_tid, fg_tid, False)
                if target_tid:
                    user32.AttachThreadInput(current_tid, target_tid, False)
            except Exception:
                pass
        return True
    except Exception:
        return False


def _wake_existing_crowbar(pid: int) -> bool:
    """唤醒现有 Crowbar 实例；Crowbar 通常是单实例 GUI。"""
    try:
        windows = _enum_windows_for_pid(pid)
        if not windows:
            return False
        # 优先选择有标题的主窗口，避免盲目操作隐藏子窗体。
        ranked = sorted(windows, key=lambda h: (1 if _window_text(h).strip() else 0), reverse=True)
        return _bring_window_to_front(ranked[0])
    except Exception:
        return False


def _launch_crowbar(crowbar_path: str, input_path: str):
    """启动/唤醒 Crowbar，并把本次新的 MDL/QC 参数真正传给它。

    Crowbar 是单实例 GUI。已有实例时仍然必须再次 Popen，并把新的 input_path
    作为参数交给 Crowbar 的单实例 IPC；不能只把旧窗口 BringToFront，否则批处理
    后续项目会继续停留在上一份模型上。这里把等待窗口的时间压到很短，只用于让
    单实例参数转发完成。
    """
    abs_path = os.path.abspath(input_path)
    existing = _find_running_crowbar_pids()
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        proc = subprocess.Popen(
            [os.path.abspath(crowbar_path), abs_path],
            cwd=os.path.dirname(os.path.abspath(crowbar_path)),
            creationflags=creationflags,
        )
    except OSError as exc:
        raise CrowbarError(f"启动 Crowbar 失败：{exc}") from exc

    # 新实例或单实例 IPC 转发通常在极短时间内完成；不再固定等 12 秒。
    deadline = time.time() + (1.50 if existing else 2.50)
    while time.time() < deadline:
        if proc.poll() is not None:
            pids = _find_running_crowbar_pids()
            if pids:
                _wake_existing_crowbar(pids[0])
                return _ExistingProcessProxy(pids[0])
            break
        pids = _find_running_crowbar_pids()
        if pids:
            # 优先缓存/现有 PID；唤醒即可，不再做长时间轮询。
            pid = pids[0]
            _wake_existing_crowbar(pid)
            if pid != proc.pid:
                return _ExistingProcessProxy(pid)
            return proc
        time.sleep(0.03)
    pids = _find_running_crowbar_pids()
    if pids:
        _wake_existing_crowbar(pids[0])
        return _ExistingProcessProxy(pids[0])
    return proc


class _ExistingProcessProxy:
    """最小 Popen 兼容代理：只提供 decompile 流程实际使用的 poll/pid。"""
    def __init__(self, pid: int):
        self.pid = int(pid)

    def poll(self):
        return None if _pid_alive(self.pid) else 0


def _unique_existing(paths: Iterable[str]) -> list[str]:
    out = []
    seen = set()
    for p in paths:
        if not p:
            continue
        p = os.path.abspath(os.path.expandvars(os.path.expanduser(p)))
        key = os.path.normcase(p)
        if key in seen:
            continue
        if os.path.exists(p):
            seen.add(key)
            out.append(p)
    return out


def find_crowbar_exe() -> str:
    """Best-effort auto discovery for Crowbar / CLI decompiler binaries."""
    home = Path.home()
    candidates: list[str] = []
    which_names = (
        "CrowbarCommandLineDecomp.exe",
        "CrowbarDecompiler.exe",
        "Crowbar.exe",
    )
    for name in which_names:
        hit = shutil.which(name)
        if hit:
            candidates.append(hit)

    # Portable Crowbar installs are frequently placed directly in a drive root.
    for drive in "CDEFGH":
        for name in which_names:
            candidates.append(str(Path(f"{drive}:/") / name))

    common_dirs = [
        home / "Desktop",
        home / "Downloads",
        home / "Documents",
        home / "AppData" / "Local" / "Crowbar",
        home / "AppData" / "Roaming" / "Crowbar",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Crowbar",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Crowbar",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    ]
    for base in common_dirs:
        for name in which_names:
            candidates.append(str(base / name))

    # One level below common Crowbar locations is common for portable installs.
    for base in list(common_dirs):
        try:
            if not base.is_dir():
                continue
            for child in base.iterdir():
                if child.is_dir():
                    for name in which_names:
                        candidates.append(str(child / name))
        except OSError:
            pass

    existing = _unique_existing(candidates)
    # Prefer an unattended decompiler when present.
    existing.sort(key=lambda p: (0 if "commandline" in os.path.basename(p).casefold() else 1 if "decompiler" in os.path.basename(p).casefold() else 2, p.casefold()))
    return existing[0] if existing else ""


def is_cli_decompiler(path: str) -> bool:
    name = os.path.basename(path).casefold()
    return "commandlinedecomp" in name or "decompiler" in name



def _find_running_crowbar_pids() -> list[int]:
    """返回当前运行中的 Crowbar.exe PID，兼容 Crowbar 单实例模式。"""
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Crowbar.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="mbcs", errors="replace", timeout=5,
        )
        pids = []
        for line in proc.stdout.splitlines():
            if not line.strip() or line.startswith("INFO:"):
                continue
            parts = [x.strip('"') for x in line.split('","')]
            if len(parts) >= 2:
                try:
                    pids.append(int(parts[1]))
                except ValueError:
                    pass
        return list(dict.fromkeys(pids))
    except Exception:
        return []

def _ps_encode(script: str) -> str:
    import base64
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def _enum_windows_for_pid(pid: int) -> list[int]:
    """使用 user32 枚举指定 PID 的顶层窗口，不依赖 PowerShell/UIAutomation。"""
    import ctypes
    from ctypes import wintypes
    user32, _kernel32 = _win32()
    result: list[int] = []
    EnumWindows = user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    GetWindowThreadProcessId = user32.GetWindowThreadProcessId
    IsWindowVisible = user32.IsWindowVisible
    @EnumWindowsProc
    def cb(hwnd, lparam):
        owner = wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if int(owner.value) == int(pid) and IsWindowVisible(hwnd):
            result.append(int(hwnd))
        return True
    EnumWindows(cb, 0)
    return result


def _window_text(hwnd: int) -> str:
    import ctypes
    from ctypes import wintypes
    user32, _kernel32 = _win32()
    GetWindowTextLengthW = user32.GetWindowTextLengthW
    GetWindowTextW = user32.GetWindowTextW
    n = int(GetWindowTextLengthW(hwnd))
    buf = ctypes.create_unicode_buffer(max(256, n + 2))
    GetWindowTextW(hwnd, buf, len(buf))
    return buf.value


def _window_class(hwnd: int) -> str:
    import ctypes
    from ctypes import wintypes
    user32, _kernel32 = _win32()
    GetClassNameW = user32.GetClassNameW
    buf = ctypes.create_unicode_buffer(256)
    GetClassNameW(hwnd, buf, len(buf))
    return buf.value


def _enum_child_windows(hwnd_parent: int) -> list[int]:
    import ctypes
    from ctypes import wintypes
    user32, _kernel32 = _win32()
    result: list[int] = []
    EnumChildWindows = user32.EnumChildWindows
    Proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    @Proc
    def cb(hwnd, lparam):
        result.append(int(hwnd))
        return True
    EnumChildWindows(hwnd_parent, cb, 0)
    return result



def _crowbar_pid_matches_model(pid: int, model_path: str) -> bool:
    """判断当前 Crowbar 实例是否已经加载目标模型/QC。

    Crowbar 单实例模式下，启动 Crowbar.exe xxx.mdl 可能不会创建新 PID，
    而是把参数交给已有实例。因此不能只按 PID 顺序点击按钮，否则批量时
    很容易把 Decompile/Compile 点到上一份模型上。
    """
    stem = Path(model_path).stem.casefold()
    basename = Path(model_path).name.casefold()
    path_norm = os.path.abspath(model_path).replace('/', '\\').casefold()
    for top in _enum_windows_for_pid(pid):
        texts = [_window_text(top)]
        texts.extend(_window_text(h) for h in _enum_child_windows(top))
        for text in texts:
            norm = (text or '').strip().replace('/', '\\').casefold()
            if not norm:
                continue
            if path_norm in norm or basename in norm or stem in norm:
                return True
    return False

def _find_text_control(hwnd: int, text: str) -> tuple[int, int]:
    """查找指定文字的控件。返回 (最优控件句柄, 控件类型评分)。

    Crowbar 在不同版本/状态下可能先停留在 Compile 页，导致 Decompile
    按钮暂时不可见；因此除了 Button，还兼容 Tab/Label/其它 WinForms 控件，
    先把界面切回正确页面，再重新扫描真正的按钮。
    """
    import ctypes
    user32, _kernel32 = _win32()
    IsWindowVisible = user32.IsWindowVisible
    IsWindowEnabled = user32.IsWindowEnabled
    wanted = " ".join(text.split()).casefold()
    candidates = [hwnd] + _enum_child_windows(hwnd)
    ranked = []
    for h in candidates:
        if not IsWindowVisible(h):
            continue
        txt = _window_text(h).strip().replace("&", "")
        norm = " ".join(txt.split()).casefold()
        if norm != wanted:
            continue
        cls = _window_class(h).casefold()
        score = 0
        if IsWindowEnabled(h):
            score += 10
        if cls == "button":
            score += 100
        elif "button" in cls:
            score += 80
        elif "tab" in cls or "page" in cls:
            score += 30
        elif "link" in cls or "item" in cls:
            score += 20
        ranked.append((score, h))
    if not ranked:
        return 0, 0
    ranked.sort(reverse=True)
    return ranked[0][1], ranked[0][0]


def _find_decompile_button(hwnd: int) -> int:
    h, score = _find_text_control(hwnd, "Decompile")
    # 只有真正的按钮/高置信控件才视为最终执行按钮；低分控件由调用方先点击切页。
    if h and score >= 80:
        return h
    return 0


def _click_native_button(hwnd: int) -> bool:
    """通过 BM_CLICK 触发按钮；整个过程不移动真实鼠标。"""
    import ctypes
    user32, _kernel32 = _win32()
    BM_CLICK = 0x00F5
    IsWindow = user32.IsWindow
    IsWindowVisible = user32.IsWindowVisible
    IsWindowEnabled = user32.IsWindowEnabled
    if not IsWindow(hwnd) or not IsWindowVisible(hwnd) or not IsWindowEnabled(hwnd):
        return False

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long), ("top", ctypes.c_long),
            ("right", ctypes.c_long), ("bottom", ctypes.c_long),
        ]

    # 纯 Win32 控件消息点击：绝不移动真实鼠标，也不发送 mouse_event。
    # Crowbar 的按钮/WinForms 按钮可通过 BM_CLICK 直接触发。
    user32.SendMessageW(hwnd, BM_CLICK, 0, 0)
    time.sleep(0.12)
    return True


def _crowbar_click_decompile_ui(crowbar_pid: int, timeout: int = 45, stop_event: threading.Event | None = None) -> str:
    """纯 Python Win32 UI 自动点击 Crowbar 的 Decompile。

    处理 Crowbar 单实例/残留在 Compile 页的情况：先尝试直接点击按钮；
    如果只有“Decompile”页签/控件可见，则先点击它，再重新寻找按钮。
    """
    deadline = time.time() + timeout
    switched = False
    while time.time() < deadline:
        if stop_event is not None and stop_event.is_set():
            raise CrowbarError("用户已终止处理。")
        for top in _enum_windows_for_pid(crowbar_pid):
            btn = _find_decompile_button(top)
            if btn:
                if _click_native_button(btn):
                    return "DECOMPILE_CLICKED"

            # 只有页签/其它控件时，先切到 Decompile 页面。
            tab, score = _find_text_control(top, "Decompile")
            if tab and score < 80 and not switched:
                if _click_native_button(tab):
                    switched = True
                    time.sleep(0.35)
        time.sleep(0.10)
    raise CrowbarError("未找到可点击的 Crowbar Decompile 按钮；请确认 Crowbar 当前窗口已加载 MDL。")


def _decompile_artifact_names(model_stem: str) -> set[str]:
    """返回 Crowbar 对单个 MDL 常见的桌面 decompiled 产物名称。"""
    stem = Path(model_stem).stem
    return {
        f"{stem}.qc",
        f"{stem}_reference.smd",
        f"{stem}_physics.smd",
        f"{stem}_anims",
    }


def _remove_decompiled_artifacts(desktop_decompiled: str, model_stem: str) -> None:
    """只删除当前 stem 的旧产物，避免每轮扫描 decompiled 根目录。"""
    stem = Path(model_stem).stem
    for path in (
        os.path.join(desktop_decompiled, f"{stem}.qc"),
        os.path.join(desktop_decompiled, f"{stem}_reference.smd"),
        os.path.join(desktop_decompiled, f"{stem}_physics.smd"),
        os.path.join(desktop_decompiled, f"{stem}_anims"),
    ):
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, ignore_errors=True)
            elif os.path.exists(path) or os.path.islink(path):
                os.remove(path)
        except OSError:
            pass


def _snapshot_decompiled_artifacts(desktop_decompiled: str, model_stem: str) -> dict[str, tuple[bool, int, int]]:
    """记录指定模型的反编译产物时间/大小，供本轮完成判定使用。"""
    stem = Path(model_stem).stem.casefold()
    wanted = {
        f"{stem}.qc",
        f"{stem}_reference.smd",
        f"{stem}_physics.smd",
        f"{stem}_anims",
    }
    out: dict[str, tuple[bool, int, int]] = {}
    try:
        names = os.listdir(desktop_decompiled)
    except OSError:
        return out
    for name in names:
        if name.casefold() not in wanted:
            continue
        p = os.path.join(desktop_decompiled, name)
        try:
            st = os.stat(p)
            out[name.casefold()] = (os.path.isdir(p), int(st.st_mtime_ns), int(st.st_size))
        except OSError:
            pass
    return out


def _qc_expected_smds(qc_path: str) -> list[str]:
    """Return every explicit SMD file token referenced by the QC.

    This is used only for decompile completion validation.  It deliberately
    includes body/model/bodygroup, sequence/animation, and physics SMDs so a
    QC such as ``$sequence ... "xxx_anims\\idle.smd"`` cannot be compiled
    before Crowbar has finished writing the animation directory.
    """
    qc = Path(qc_path).resolve()
    root = qc.parent.resolve()
    try:
        text = qc.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    refs: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        for token in re.findall(r'"([^\"]+?\.smd)"', stripped, flags=re.IGNORECASE):
            token = token.replace("\\", os.sep).replace("/", os.sep)
            if "*" in token or "?" in token:
                continue
            candidate = (root / token).resolve()
            key = os.path.normcase(str(candidate))
            if key not in seen:
                seen.add(key)
                refs.append(str(candidate))
    return refs


def _decompiled_output_ready(desktop_decompiled: str, model_stem: str, start_ns: int) -> tuple[str, list[str]]:
    """确认 QC 以及 QC 明确引用的全部 SMD 已生成并稳定。"""
    stem = Path(model_stem).stem
    qc_path = os.path.join(desktop_decompiled, f"{stem}.qc")
    try:
        st = os.stat(qc_path)
        if st.st_mtime_ns < start_ns:
            return "", []
    except OSError:
        try:
            qc_name = f"{stem}.qc".casefold()
            match = next((n for n in os.listdir(desktop_decompiled) if n.casefold() == qc_name), None)
            if not match:
                return "", []
            qc_path = os.path.join(desktop_decompiled, match)
            if os.stat(qc_path).st_mtime_ns < start_ns:
                return "", []
        except OSError:
            return "", []

    expected = _qc_expected_smds(qc_path)
    if not expected:
        return "", []

    stat_snapshot: list[tuple[str, int, int]] = []
    for path in expected:
        try:
            st = os.stat(path)
            if not os.path.isfile(path) or st.st_mtime_ns < start_ns or st.st_size <= 0:
                return "", []
            stat_snapshot.append((path, int(st.st_mtime_ns), int(st.st_size)))
        except OSError:
            return "", []

    return qc_path, expected


def _wait_crowbar_settle(timeout: float = 0.5, stop_event: threading.Event | None = None) -> None:
    """仅保留极短让步时间；Crowbar 会话现在跨样本复用，不再固定等待。"""
    if stop_event is not None and stop_event.is_set():
        raise CrowbarError("用户已终止处理。")
    # GUI 单实例需要极短的消息泵让步，但不再强制 150ms。
    if timeout > 0:
        time.sleep(min(0.03, timeout))


def decompile_mdl(crowbar_path: str, mdl_path: str, output_dir: str, timeout: int = 300, stop_event: threading.Event | None = None) -> str:
    """使用干净 Crowbar GUI 会话反编译单个 MDL。

    设计原则：
    1. 每个 MDL 反编译前关闭旧 Crowbar，避免单实例 IPC 把新参数吞掉。
    2. 直接以 ``Crowbar.exe <当前MDL>`` 启动新会话；不向 WinForms 输入框注入路径。
    3. 启动后沿用已经实际验证过的 Win32 UI Decompile 点击链。
    4. 3ds Max 完全不受影响，仍由上层批处理复用单实例。
    """
    if stop_event is not None and stop_event.is_set():
        raise CrowbarError("用户已终止处理。")
    if not crowbar_path or not os.path.isfile(crowbar_path):
        raise CrowbarError("未找到有效的 Crowbar.exe。")
    if not mdl_path or not os.path.isfile(mdl_path):
        raise CrowbarError(f"MDL 文件不存在：{mdl_path}")

    desktop_decompiled = os.path.join(Path.home(), "Desktop", "decompiled")
    os.makedirs(desktop_decompiled, exist_ok=True)
    model_stem = Path(mdl_path).stem
    _remove_decompiled_artifacts(desktop_decompiled, model_stem)
    start_ns = time.time_ns()

    # 关键：每个模型都建立独立 Crowbar 会话，彻底绕开 Crowbar 单实例参数串档。
    try:
        stale = _find_running_crowbar_pids()
        if stale:
            close_all_crowbar(force=True)
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if not _find_running_crowbar_pids():
                    break
                if stop_event is not None and stop_event.is_set():
                    raise CrowbarError("用户已终止处理。")
                time.sleep(0.05)
    except CrowbarError:
        raise
    except Exception:
        pass

    proc = _launch_crowbar(crowbar_path, mdl_path)

    try:
        # 沿用 v17.68 已成功的点击逻辑：只负责找到真正的 Decompile 按钮并点击。
        click_deadline = time.time() + min(60, max(15, timeout))
        clicked = False
        last_error = None
        fallback_discover_at = time.time() + 1.0
        while time.time() < click_deadline and not clicked:
            if stop_event is not None and stop_event.is_set():
                raise CrowbarError("用户已终止处理。")

            candidates = []
            if proc.poll() is None:
                candidates.append(proc.pid)

            if not candidates or time.time() >= fallback_discover_at:
                for pid in _find_running_crowbar_pids():
                    if pid not in candidates:
                        candidates.append(pid)
                fallback_discover_at = time.time() + 1.5

            # 新会话应直接加载当前 MDL；仍保留匹配优先，防止系统有其它 Crowbar 实例。
            matched = []
            for pid in candidates:
                try:
                    if _crowbar_pid_matches_model(pid, mdl_path):
                        matched.append(pid)
                except Exception:
                    pass
            ordered = matched + [pid for pid in candidates if pid not in matched]

            for pid in ordered:
                try:
                    _crowbar_click_decompile_ui(pid, timeout=0.55, stop_event=stop_event)
                    clicked = True
                    _remember_crowbar_pid(crowbar_path, pid)
                    break
                except CrowbarError as exc:
                    last_error = exc

            if not clicked:
                if stop_event is not None and stop_event.wait(0.08):
                    raise CrowbarError("用户已终止处理。")
                time.sleep(0.05)

        if not clicked:
            raise CrowbarError(
                "Crowbar 已启动，但仍未找到可点击的 Decompile 按钮。"
                + (f"（{last_error}）" if last_error else "")
            )

        wait_deadline = time.time() + max(45, timeout - 25)
        stable_since = None
        while time.time() < wait_deadline:
            if stop_event is not None and stop_event.is_set():
                raise CrowbarError("用户已终止处理。")
            qc_path, artifacts = _decompiled_output_ready(desktop_decompiled, model_stem, start_ns)
            if qc_path and artifacts:
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since >= 0.12:
                    return qc_path
            else:
                stable_since = None
            time.sleep(0.08)

        raise CrowbarError(
            f"Crowbar 已执行 Decompile，但没有在桌面 decompiled 中完整检测到 {model_stem} 的本轮输出。"
        )
    finally:
        # 不在成功反编译后立即强杀，给 Crowbar 完成落盘留出空间；下一模型开始前再清理。
        with _CROWBAR_SESSION_LOCK:
            _CROWBAR_SESSION_PIDS.pop(_crowbar_key(crowbar_path), None)


def close_all_crowbar(force: bool = True) -> int:
    """关闭当前系统中所有 Crowbar.exe 实例，返回尝试关闭的进程数量。

    纹理组批处理会按固定批次重启 Crowbar，以避免长时间运行的 GUI 状态
    累积。该操作只在上层明确要求时调用，不影响普通单次 Decompile/Compile。
    """
    try:
        pids = _find_running_crowbar_pids()
        if not pids:
            return 0
        cmd = ["taskkill", "/T"]
        if force:
            cmd.append("/F")
        cmd.extend(["/IM", "Crowbar.exe"])
        subprocess.run(cmd, capture_output=True, text=True, encoding="mbcs", errors="replace", timeout=10)
        # 清掉本模块缓存，下一次任务必须重新建立 Crowbar 会话。
        with _CROWBAR_SESSION_LOCK:
            _CROWBAR_SESSION_PIDS.clear()
        return len(pids)
    except Exception:
        return 0

def find_nekomdl_exe() -> str:
    """Best-effort discovery for NekoMDL compiler."""
    candidates = []
    for name in ("nekomdl.exe", "NekoMdl.exe", "NekoMDL.exe"):
        hit = shutil.which(name)
        if hit:
            candidates.append(hit)
    home = Path.home()
    common_dirs = [
        home / "Desktop", home / "Downloads", home / "Documents",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    ]
    # Common portable/game-bin locations first.
    for base in list(common_dirs):
        for rel in (Path("nekomdl.exe"), Path("NekoMdl") / "nekomdl.exe", Path("NekoMDL") / "nekomdl.exe"):
            candidates.append(str(base / rel))
    # L4D2 bin is a particularly common Crowbar/NekoMDL setup location.
    steam_roots = []
    for d in "CDEFGH":
        steam_roots += [Path(f"{d}:/Steam"), Path(f"{d}:/Program Files (x86)/Steam"), Path(f"{d}:/Program Files/Steam") ]
    for base in steam_roots:
        candidates.append(str(base / "steamapps/common/Left 4 Dead 2/bin/nekomdl.exe"))
    seen=set(); existing=[]
    for c in candidates:
        try:
            q=os.path.abspath(os.path.expandvars(os.path.expanduser(c)))
        except Exception:
            continue
        if os.path.normcase(q) in seen:
            continue
        if os.path.isfile(q):
            seen.add(os.path.normcase(q)); existing.append(q)
    return existing[0] if existing else ""


def find_game_dir_from_addons(addons_dir: str):
    p = Path(addons_dir).resolve()
    # .../left4dead2/addons -> .../left4dead2
    if p.name.casefold() == "addons" and p.parent.is_dir():
        game = p.parent
        if (game / "gameinfo.txt").exists():
            return str(game)
    return ""


def find_studiomdl(game_dir: str) -> str:
    # Kept for backward compatibility; texture-group cleanup now uses NekoMDL.
    if not game_dir:
        return ""
    candidates = [Path(game_dir).parent / "bin" / "studiomdl.exe", Path(game_dir) / "bin" / "studiomdl.exe"]
    for p in candidates:
        if p.exists():
            return str(p.resolve())
    return ""

def _temporary_modelname(qc_text: str, relative_output_root: str) -> Tuple[str, str]:
    """Rewrite the first $modelname to a safe temporary models path."""
    import re
    pat = re.compile(r'(^\s*\$modelname\s+["\'])([^"\']+)(["\'])', re.IGNORECASE | re.MULTILINE)
    m = pat.search(qc_text)
    if not m:
        raise CrowbarError("QC 中没有找到 $modelname，无法安全编译。")
    old_model = m.group(2).replace("/", "\\")
    safe_name = old_model.replace("\\", "/").rsplit("/", 1)[-1]
    temp_model = os.path.join(relative_output_root, safe_name).replace("/", "\\")
    new_text = qc_text[:m.start(2)] + temp_model + qc_text[m.end(2):]
    return new_text, old_model


def compile_qc_via_studiomdl(studiomdl: str, game_dir: str, qc_path: str,
                             timeout: int = 300) -> tuple[str, str, str]:
    """Compile QC through the game's StudioMDL compiler with a safe temporary model path.

    Returns (stdout, stderr, temp_relative_model_path)."""
    if not os.path.isfile(studiomdl):
        raise CrowbarError("未找到 studiomdl.exe。")
    if not os.path.isdir(game_dir):
        raise CrowbarError("未找到有效的游戏目录。")

    with open(qc_path, "r", encoding="utf-8-sig", errors="replace") as f:
        qc_text = f.read()
    temp_root = r"models\__rbvp_texturegroup_temp"
    unique_name = f"run_{os.getpid()}_{abs(hash(os.path.abspath(qc_path))) & 0xFFFFFFFF:08X}"
    rel_root = f"{temp_root}\\{unique_name}"
    patched, _ = _temporary_modelname(qc_text, rel_root)
    original_qc = qc_path
    temp_qc = qc_path + ".rbvp_tmp.qc"
    with open(temp_qc, "w", encoding="utf-8", newline="\n") as f:
        f.write(patched)

    try:
        cmd = [studiomdl, "-game", game_dir, os.path.basename(temp_qc)]
        proc = subprocess.run(cmd, cwd=os.path.dirname(temp_qc), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
        temp_abs = os.path.join(game_dir, rel_root.replace("\\", os.sep))
        return proc.stdout or "", proc.stderr or "", temp_abs
    finally:
        try:
            os.remove(temp_qc)
        except OSError:
            pass


def _extract_modelname(qc_text: str) -> str:
    import re
    pat = re.compile(r'^\s*\$modelname\s+["\']([^"\']+)["\']', re.IGNORECASE | re.MULTILINE)
    m = pat.search(qc_text)
    if not m:
        raise CrowbarError("QC 中没有找到 $modelname，无法确定编译输出路径。")
    return m.group(1).replace("/", "\\").lstrip("\\")


def _find_compile_button(hwnd: int) -> int:
    h, score = _find_text_control(hwnd, "Compile")
    if h and score >= 80:
        return h
    return 0


def _crowbar_click_compile_ui(crowbar_pid: int, timeout: int = 45, stop_event: threading.Event | None = None) -> str:
    """使用纯 Python Win32 UI 自动点击 Crowbar 的 Compile。

    同样兼容 Crowbar 单实例残留在其它页面的情况。
    """
    deadline = time.time() + timeout
    switched = False
    while time.time() < deadline:
        if stop_event is not None and stop_event.is_set():
            raise CrowbarError("用户已终止处理。")
        for top in _enum_windows_for_pid(crowbar_pid):
            btn = _find_compile_button(top)
            if btn:
                if _click_native_button(btn):
                    return "COMPILE_CLICKED"
            tab, score = _find_text_control(top, "Compile")
            if tab and score < 80 and not switched:
                if _click_native_button(tab):
                    switched = True
                    time.sleep(0.35)
        time.sleep(0.10)
    raise CrowbarError("未找到可点击的 Crowbar Compile 按钮；请确认 Crowbar 当前窗口已加载 QC。")


def _find_expected_compiled_files(game_dir: str, modelname: str) -> list[str]:
    """固定文件名检查编译产物。"""
    model_path = os.path.join(game_dir, modelname.replace("\\", os.sep))
    stem = os.path.splitext(model_path)[0]
    return [p for p in (
        stem + ".mdl", stem + ".vvd", stem + ".phy", stem + ".ani",
        stem + ".dx80.vtx", stem + ".dx90.vtx", stem + ".sw.vtx"
    ) if os.path.isfile(p)]


def _copy_compiled_outputs(game_dir: str, modelname: str, addon_model_dir: str) -> tuple[int, str]:
    """把 Crowbar 编译得到的当前模型文件直接覆盖回原 Addons 模型目录。"""
    model_path = os.path.join(game_dir, modelname.replace("\\", os.sep))
    source_dir = os.path.dirname(model_path)
    stem_name = os.path.splitext(os.path.basename(model_path))[0]
    if not os.path.isdir(source_dir):
        raise CrowbarError(f"找不到编译输出目录：{source_dir}")
    os.makedirs(addon_model_dir, exist_ok=True)
    copied = 0
    for name in os.listdir(source_dir):
        low = name.casefold()
        base = stem_name.casefold()
        if not (low == base + ".mdl" or low == base + ".vvd" or low == base + ".phy" or low == base + ".ani" or low == base + ".dx80.vtx" or low == base + ".dx90.vtx" or low == base + ".sw.vtx"):
            continue
        src = os.path.join(source_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(addon_model_dir, name))
            copied += 1
    if copied == 0:
        raise CrowbarError(f"编译完成后未找到 {stem_name} 的模型输出文件。")
    return copied, source_dir


def compile_qc_via_nekomdl(crowbar: str, nekomdl: str, game_dir: str, qc_path: str,
                           timeout: int = 300, stop_event: threading.Event | None = None) -> tuple[str, str, str]:
    """严格模拟用户手动操作：Crowbar 打开真实 QC，点击 Compile。

    Crowbar 会话跨整个批处理复用，Model Compiler 仍完全由 Crowbar 自己
    调用用户已经配置好的 NekoMDL。不会直接启动 nekomdl.exe，也不会创建
    *.rbvp_tmp.qc。返回 Desktop\\models 中当前模型的输出目录。
    """
    if stop_event is not None and stop_event.is_set():
        raise CrowbarError("用户已终止处理。")
    if not crowbar or not os.path.isfile(crowbar):
        raise CrowbarError("未找到有效的 Crowbar.exe。")
    if nekomdl and not os.path.isfile(nekomdl):
        raise CrowbarError("设置中的 nekomdl.exe 不存在，请重新指定 NekoMDL。")
    if not os.path.isfile(qc_path):
        raise CrowbarError(f"QC 文件不存在：{qc_path}")

    with open(qc_path, "r", encoding="utf-8-sig", errors="replace") as f:
        qc_text = f.read()
    modelname = _extract_modelname(qc_text)

    desktop_models = os.path.join(Path.home(), "Desktop", "models")
    output_model = os.path.join(desktop_models, modelname.replace("\\", os.sep).replace("/", os.sep))
    output_dir = os.path.dirname(output_model)
    os.makedirs(desktop_models, exist_ok=True)

    before_files = {}
    if os.path.isdir(output_dir):
        for pth in _find_compiled_files_under_desktop_models(desktop_models, modelname):
            try:
                before_files[os.path.normcase(os.path.abspath(pth))] = os.path.getmtime(pth)
            except OSError:
                pass

    proc = _launch_crowbar(crowbar, qc_path)
    cached_pid = _get_cached_crowbar_pid(crowbar)
    try:
        click_deadline = time.time() + min(60, max(12, timeout))
        clicked = False
        last_error = None
        fallback_discover_at = time.time() + 1.0
        while time.time() < click_deadline and not clicked:
            if stop_event is not None and stop_event.is_set():
                raise CrowbarError("用户已终止处理。")
            candidates = []
            if cached_pid and _pid_alive(cached_pid):
                candidates.append(cached_pid)
            if proc.poll() is None and proc.pid not in candidates:
                candidates.append(proc.pid)
            if not candidates or time.time() >= fallback_discover_at:
                for pid in _find_running_crowbar_pids():
                    if pid not in candidates:
                        candidates.append(pid)
                fallback_discover_at = time.time() + 1.5
            matched = []
            for pid in candidates:
                try:
                    if _crowbar_pid_matches_model(pid, qc_path):
                        matched.append(pid)
                except Exception:
                    pass
            ordered = matched + [pid for pid in candidates if pid not in matched]
            for pid in ordered:
                try:
                    _crowbar_click_compile_ui(pid, timeout=0.55, stop_event=stop_event)
                    clicked = True
                    _remember_crowbar_pid(crowbar, pid)
                    break
                except CrowbarError as exc:
                    last_error = exc
            if not clicked:
                if stop_event is not None and stop_event.wait(0.08):
                    raise CrowbarError("用户已终止处理。")
                time.sleep(0.05)
        if not clicked:
            raise CrowbarError(
                "Crowbar 已启动，但仍未找到可点击的 Compile 按钮。"
                + (f"（{last_error}）" if last_error else "")
            )

        wait_deadline = time.time() + max(30, timeout - 25)
        # 只检查已知的模型文件路径，不再遍历整个 Desktop\\models。
        mdl_abs = os.path.normcase(os.path.abspath(output_model))
        while time.time() < wait_deadline:
            if stop_event is not None and stop_event.is_set():
                raise CrowbarError("用户已终止处理。")
            try:
                if os.path.isfile(output_model):
                    mtime = os.path.getmtime(output_model)
                    old = before_files.get(mdl_abs)
                    is_new = old is None or mtime > old + 0.10
                    if is_new:
                        return "", "", output_dir
            except OSError:
                pass
            time.sleep(0.08)
        raise CrowbarError(
            f"Crowbar 已执行 Compile，但没有在桌面 models 中检测到新的模型输出：{modelname}"
        )
    finally:
        pass

def _find_compiled_files_under_desktop_models(desktop_models: str, modelname: str) -> list[str]:
    """固定文件名检查 Desktop\models 下当前模型产物。"""
    model_path = os.path.join(desktop_models, modelname.replace("\\", os.sep).replace("/", os.sep))
    stem = os.path.splitext(model_path)[0]
    return [p for p in (
        stem + ".mdl", stem + ".vvd", stem + ".phy", stem + ".ani",
        stem + ".dx80.vtx", stem + ".dx90.vtx", stem + ".sw.vtx"
    ) if os.path.isfile(p)]
