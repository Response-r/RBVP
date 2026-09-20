import os
import threading
from tkinter import messagebox

from core.texturegroup import scan_addons_texturegroups
from core.paths import format_path


class TextureGroupActionsMixin:
    def _start_texturegroup_scan(self):
        addons_dir = self.addons_dir_path.get().strip()
        if not os.path.isdir(addons_dir):
            messagebox.showerror("错误", "找不到 Addons 目录，请先在设置中确认路径。")
            self._show_page("settings")
            return

        for item in self.texturegroup_tree.get_children():
            self.texturegroup_tree.delete(item)
        self.texturegroup_status_var.set("扫描 MDL 是否存在 $texturegroup…")
        self.texturegroup_count_var.set("0")
        self.top_status_var.set("纹理组检测运行中")
        threading.Thread(
            target=self._worker_texturegroup_scan,
            args=(format_path(addons_dir),),
            daemon=True,
        ).start()

    def _worker_texturegroup_scan(self, addons_dir):
        try:
            hits = scan_addons_texturegroups(addons_dir)
        except Exception as exc:
            self.root.after(0, lambda exc=exc: self.texturegroup_status_var.set(f"扫描失败：{exc}"))
            self.root.after(0, lambda: self.top_status_var.set("环境就绪"))
            return

        def publish():
            for hit in hits:
                vpk_rel = os.path.relpath(hit["vpk"], addons_dir)
                self.texturegroup_tree.insert(
                    "", "end",
                    values=(vpk_rel, hit["mdl_path"]),
                )
            self.texturegroup_count_var.set(f"{len(hits):,}")
            if hits:
                self.texturegroup_status_var.set(
                    f"扫描完成 · 找到 {len(hits):,} 个含 $texturegroup 的 MDL"
                )
            else:
                self.texturegroup_status_var.set(
                    "扫描完成 · 未发现含 $texturegroup 的 MDL"
                )
            self.top_status_var.set("环境就绪")

        self.root.after(0, publish)
