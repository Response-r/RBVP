import sys
import os
import threading
import subprocess
import concurrent.futures
import time
import json
import tkinter as tk

from core.crowbar import find_crowbar_exe, find_nekomdl_exe
from core.batch_reduce import find_3dsmax_exe
from tkinter import filedialog, messagebox, ttk


from core.paths import auto_find_vpk_exe, auto_find_vtfedit_exe, auto_find_addons_dir, format_path
from ui.widgets import AARoundedText

class LifecycleMixin:
    def __init__(self, root):
        self.root = root
        self.root.report_callback_exception = self._report_tk_exception
        self.root.title("RBVP · Rick's Batch VPK Processing")
        self.root.geometry("1360x860")
        self.root.minsize(1120, 720)
        self.root.configure(bg="#091120")

        self.vpk_exe_path = tk.StringVar(value="")
        self.vtfedit_exe_path = tk.StringVar(value="")
        self.crowbar_exe_path = tk.StringVar(value="")
        self.nekomdl_exe_path = tk.StringVar(value="")
        self.addons_dir_path = tk.StringVar(value="")
        self.threedsmax_exe_path = tk.StringVar(value="")
        self.vpk_detail_data = {}
        self.pages = {}
        self.nav_buttons = {}
        self.current_page = None
        self.page_titles = {}

        self.model_status_var = tk.StringVar(value="等待开始扫描 Addons")
        self.model_count_var = tk.StringVar(value="0")
        self.model_vertex_var = tk.StringVar(value="0")
        self.model_tri_var = tk.StringVar(value="0")
        self.conflict_status_var = tk.StringVar(value="等待扫描")
        self.top_status_var = tk.StringVar(value="环境就绪")

        self.colors = {
            "bg":"#091120", "rail":"#0B1527", "rail_active":"#1A2B4B",
            "panel":"#0F1B2F", "card":"#17253D", "card2":"#1B2B46",
            "soft":"#121F34", "input":"#0B1629", "border":"#263A5B",
            "accent":"#7584FF", "accent_hover":"#8895FF", "accent_soft":"#283964",
            "text":"#F4F7FF", "subtext":"#AAB8D0", "muted":"#71809B",
            "success":"#38D6A5", "warning":"#F3B84E", "danger":"#FF7086", "cyan":"#4CC7FF",
        }
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._setup_styles()
        self._build_shell()
        self._build_pages()
        self._show_page("home")
        self.top_status_var.set("界面就绪 · 正在检测环境")
        threading.Thread(target=self._background_discover_paths, daemon=True).start()


    def _settings_file(self):
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        folder = os.path.join(base, "RBVP")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, "settings.json")

    def _load_saved_settings(self):
        try:
            with open(self._settings_file(), "r", encoding="utf-8") as f:
                data = json.load(f)
            for attr in ("vpk_exe_path", "vtfedit_exe_path", "crowbar_exe_path", "nekomdl_exe_path", "addons_dir_path", "threedsmax_exe_path"):
                value = data.get(attr, "")
                if value:
                    getattr(self, attr).set(format_path(value))
        except (OSError, ValueError, TypeError):
            pass

    def _save_settings(self):
        try:
            data = {
                "vpk_exe_path": self.vpk_exe_path.get().strip(),
                "vtfedit_exe_path": self.vtfedit_exe_path.get().strip(),
                "crowbar_exe_path": self.crowbar_exe_path.get().strip(),
                "nekomdl_exe_path": self.nekomdl_exe_path.get().strip(),
                "addons_dir_path": self.addons_dir_path.get().strip(),
                "threedsmax_exe_path": self.threedsmax_exe_path.get().strip(),
            }
            path = self._settings_file()
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except OSError:
            pass

    def _background_discover_paths(self):
        """后台发现工具路径，避免启动阶段阻塞主线程。"""
        try:
            vpk = auto_find_vpk_exe()
            vtf = auto_find_vtfedit_exe()
            addons = auto_find_addons_dir()
            crowbar = find_crowbar_exe()
            nekomdl = find_nekomdl_exe()
            threedsmax = find_3dsmax_exe()
            def publish():
                changed = False
                if not self.vpk_exe_path.get() and vpk:
                    self.vpk_exe_path.set(vpk); changed = True
                if not self.vtfedit_exe_path.get() and vtf:
                    self.vtfedit_exe_path.set(vtf); changed = True
                if not self.addons_dir_path.get() and addons:
                    self.addons_dir_path.set(addons); changed = True
                if not self.crowbar_exe_path.get() and crowbar:
                    self.crowbar_exe_path.set(crowbar); changed = True
                if not self.nekomdl_exe_path.get() and nekomdl:
                    self.nekomdl_exe_path.set(nekomdl); changed = True
                if not self.threedsmax_exe_path.get() and threedsmax:
                    self.threedsmax_exe_path.set(threedsmax); changed = True
                if changed:
                    self._save_settings()
                self.top_status_var.set("环境就绪")
            self.root.after(0, publish)
        except Exception:
            self.root.after(0, lambda: self.top_status_var.set("环境就绪 · 请检查设置"))


    def _browse_file(self, var, title, filetypes):
        p = filedialog.askopenfilename(title=f"选择 {title}", filetypes=filetypes)
        if p:
            var.set(format_path(p))
            self._save_settings()


    def _browse_folder(self, var):
        p = filedialog.askdirectory(title="选择文件夹")
        if p:
            var.set(format_path(p))
            self._save_settings()


    def _clear_log(self,widget): widget.delete("1.0",tk.END)


    def _create_log(self,parent):
        return AARoundedText(parent, self.colors["card"], self.colors["input"], radius=14, pad=10,
                              fg="#B9C7E2", insertbackground=self.colors["text"],
                              selectbackground=self.colors["accent_soft"], font=("Consolas",9),
                              wrap="word", padx=14, pady=12)


    def _launch_vtfedit(self):
            vtf_exe = self.vtfedit_exe_path.get().strip()
            if not os.path.exists(vtf_exe):
                messagebox.showerror("启动失败", "未找到有效的 VTFEdit.exe，请先在设置中配置。")
                self._show_page("settings")
                return
            try:
                subprocess.Popen([vtf_exe])
            except Exception as e:
                messagebox.showerror("启动异常", f"无法启动 VTFEdit: {e}")


    def _on_close(self):
        try:
            event = getattr(self, "_batch_reduce_stop_event", None)
            if event is not None:
                event.set()
            proc = getattr(self, "_batch_reduce_max_proc", None)
            if proc is not None:
                try:
                    from core.batch_reduce import _terminate_max_worker
                    _terminate_max_worker(proc)
                except Exception:
                    pass
            self._save_settings()
        finally:
            self.root.destroy()


    def _report_tk_exception(self, exc, val, tb):
        import traceback
        text = "".join(traceback.format_exception(exc, val, tb))
        try:
            print(text, file=sys.stderr)
        except Exception:
            pass
        # Avoid crashing the event loop for known late Tcl callbacks; surface diagnostics in status.
        try:
            self.top_status_var.set("界面刷新异常 · 请查看控制台")
        except Exception:
            pass


    def _write_log(self, text_widget, message):
        """线程安全地向日志控件追加文本，后台线程只通过 Tk after 调度到主线程。"""
        def publish():
            try:
                if text_widget is not None and text_widget.winfo_exists():
                    text_widget.insert(tk.END, message)
                    text_widget.see(tk.END)
            except Exception:
                pass
        try:
            self.root.after(0, publish)
        except tk.TclError:
            pass

