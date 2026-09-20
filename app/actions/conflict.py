import os
import threading
import subprocess
import concurrent.futures
import time
import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


from core.paths import format_path
from core.vpk import parse_vpk_entries
from core.conflict import is_mod_folder, collect_mod_folders

class ConflictActionsMixin:
    def _collect_mod_folders(self,parent_dir):
        """扫描父目录的直接子目录，使用与 VPK 打包相同的 Mod 判定规则。"""
        target_dirs=[]
        try:
            for name in os.listdir(parent_dir):
                full=os.path.join(parent_dir,name)
                if not os.path.isdir(full):
                    continue
                if name.casefold()=="temp" or name.startswith("."):
                    continue
                if self._is_mod_folder(full):
                    target_dirs.append(format_path(full))
        except OSError as e:
            raise OSError(f"读取父目录失败：{e}") from e
        target_dirs.sort(key=lambda p:os.path.basename(os.path.normpath(p)).casefold())
        return target_dirs


    def _is_mod_folder(self, target_dir):
        """与 VPK 打包模块保持一致：存在任一 Mod 特征即视为 Mod。"""
        try:
            names={name.casefold() for name in os.listdir(target_dir)}
        except OSError:
            return False
        markers={"addoninfo.txt","materials","models","scripts","particles","sound"}
        return bool(names & markers)


    def _start_conflict_detect(self):
            addons_dir = self.addons_dir_path.get().strip()
            if not os.path.exists(addons_dir):
                messagebox.showerror("错误", "找不到 Addons 目录，请先在设置中确认路径。")
                self._show_page("settings")
                return
            for item in self.conflict_tree.get_children():
                self.conflict_tree.delete(item)
            self.conflict_status_var.set("扫描中…")
            self.top_status_var.set("冲突扫描运行中")
            threading.Thread(target=self._worker_conflict_detect, args=(addons_dir,), daemon=True).start()


    def _worker_conflict_detect(self, addons_dir):
            file_map = {}
            try:
                vpk_files = sorted(os.path.join(addons_dir, f) for f in os.listdir(addons_dir) if f.lower().endswith(".vpk"))
                for vpk_path in vpk_files:
                    vpk_name = os.path.basename(vpk_path)
                    entries = parse_vpk_entries(vpk_path)
                    for rel_file in entries.keys():
                        clean_name = os.path.basename(rel_file).lower()
                        stem = os.path.splitext(clean_name)[0]
                        # 规则：除了 addoninfo.* 与 addonimage.*，其余资源全部纳入冲突扫描。
                        if stem in ("addoninfo", "addonimage"):
                            continue
                        file_map.setdefault(rel_file, []).append(vpk_name)
            except OSError as e:
                self.root.after(0, lambda: self.conflict_status_var.set(f"扫描失败: {e}"))
                self.top_status_var.set("环境就绪")
                return
            conflicts = [(rel, vpks) for rel, vpks in file_map.items() if len(vpks) > 1]
            conflicts.sort(key=lambda x: (-len(x[1]), x[0].lower()))
            def publish():
                for rel_file, vpks in conflicts:
                    self.conflict_tree.insert("", "end", values=(rel_file, "  <==>  ".join(vpks)))
                self.conflict_status_var.set(f"扫描完成 · {len(conflicts)} 个冲突文件")
                self.top_status_var.set("环境就绪")
                if not conflicts:
                    messagebox.showinfo("扫描结果", "未检测到任何 MOD 文件冲突。")
            self.root.after(0, publish)

