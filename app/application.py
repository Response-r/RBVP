import tkinter as tk
import multiprocessing
import threading
import sys
from pathlib import Path

from .lifecycle import LifecycleMixin
from .layout import LayoutMixin
from .page_builders import PagesMixin
from .actions.vpk import VPKActionsMixin
from .actions.models import ModelActionsMixin
from .actions.conflict import ConflictActionsMixin
from .actions.vmt import VMTActionsMixin
from .actions.texturegroup import TextureGroupActionsMixin
from .actions.texturegroup_clear import TextureGroupClearActionsMixin
from .actions.batch_reduce import BatchReduceActionsMixin

class RBVPApplication(LifecycleMixin, LayoutMixin, PagesMixin, VPKActionsMixin, ModelActionsMixin, ConflictActionsMixin, VMTActionsMixin, TextureGroupActionsMixin, TextureGroupClearActionsMixin, BatchReduceActionsMixin):
    @staticmethod
    def _resource_path(*parts):
        """Return a path to a bundled asset in both source and PyInstaller builds."""
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            base = Path(sys._MEIPASS)
        else:
            base = Path(__file__).resolve().parents[1]
        return base.joinpath(*parts)

    def _set_application_icon(self):
        """Use the RBVP feather icon for the window and Tk default icon."""
        icon_path = self._resource_path("assets", "RBVP.ico")
        if not icon_path.is_file():
            return
        try:
            icon = str(icon_path)
            # -default sets the application-wide Tk default when supported.
            self.root.iconbitmap(default=icon)
            self.root.iconbitmap(icon)
        except Exception:
            # A missing/unsupported icon must never prevent RBVP from starting.
            pass

    def __init__(self, root):
        self.root = root
        self._set_application_icon()
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
        self._load_saved_settings()
        # 任意设置方式（选择、粘贴或手动输入）都保持路径持久化。
        for _var in (self.vpk_exe_path, self.vtfedit_exe_path, self.crowbar_exe_path, self.nekomdl_exe_path, self.addons_dir_path, self.threedsmax_exe_path):
            try:
                _var.trace_add("write", lambda *_args: self._save_settings())
            except Exception:
                pass
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
        self.texturegroup_status_var = tk.StringVar(value="等待扫描 Addons 中的 $texturegroup")
        self.texturegroup_count_var = tk.StringVar(value="0")
        self.texturegroup_clear_status_var = tk.StringVar(value="等待扫描 Addons 中的 $texturegroup")
        self.top_status_var = tk.StringVar(value="环境就绪")
        self.batch_reduce_status_var = tk.StringVar(value="等待扫描符合条件的模型")

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

