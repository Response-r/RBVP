import os
import threading
import subprocess
import concurrent.futures
import time
import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


from core.paths import format_path
from core.conflict import collect_mod_folders, is_mod_folder
from core.vmt import scan_mod_vmt_files, modify_vmt_file

class VMTActionsMixin:
    def _start_modify_vmt(self):
        content_to_add=self.vmt_text_entry.get()
        if not content_to_add.strip():
            messagebox.showwarning("警告","请输入要追加的参数。")
            return

        # 与 VPK 打包统一：选择父目录，自动识别其中符合条件的 Mod 子目录。
        parent_dir=filedialog.askdirectory(title="选择包含多个 Mod 文件夹的父目录")
        if not parent_dir:
            return
        parent_dir=format_path(parent_dir)
        try:
            folders=self._collect_mod_folders(parent_dir)
        except OSError as e:
            messagebox.showerror("错误",str(e)); return
        if not folders:
            messagebox.showwarning("没有找到 Mod","所选父目录下没有检测到符合条件的 Mod 文件夹。\n\n判定条件：addoninfo.txt、materials、models、scripts、particles、sound 至少存在一个。")
            return

        self.vmt_selected_folders=folders
        self.vmt_folder_status.set(f"已识别 {len(folders)} 个 Mod 文件夹")
        self._clear_log(self.vmt_log)
        self.top_status_var.set(f"VMT 批量修改运行中 · 共 {len(folders)} 个 Mod")
        threading.Thread(target=self._worker_modify_vmt_multi,args=(folders,content_to_add),daemon=True).start()


    def _worker_modify_vmt_multi(self, folders, custom_text):
        """按 VPK 打包模块相同的 Mod 集合规则，批量扫描各 Mod 的 materials/*.vmt。"""
        total_folders = len(folders)
        folder_counts = []
        all_files = []
        seen = set()

        for target_dir in folders:
            folder_name = os.path.basename(os.path.normpath(target_dir))
            try:
                files = scan_mod_vmt_files(target_dir)
                unique = []
                for path in files:
                    key = os.path.abspath(path).casefold()
                    if key not in seen:
                        seen.add(key)
                        unique.append(path)
                        all_files.append(path)
                folder_counts.append((target_dir, len(unique)))
            except Exception as e:
                folder_counts.append((target_dir, 0))
                self._write_log(self.vmt_log, f"[扫描失败] {folder_name}\n{e}\n\n")

        for index, (folder, count) in enumerate(folder_counts, 1):
            folder_name = os.path.basename(os.path.normpath(folder))
            self._write_log(self.vmt_log, f"[Mod {index}/{total_folders}] {folder_name}\nVMT 文件：{count:,}\n\n")

        if not all_files:
            self._write_log(self.vmt_log, "[提示] 已识别 Mod，但没有找到可修改的 VMT 文件。\n\n")
            self.root.after(0, lambda: self.top_status_var.set("环境就绪"))
            return

        def edit_one(vmt_file):
            ok, err = modify_vmt_file(vmt_file, custom_text)
            return ok, vmt_file, err

        workers = min(len(all_files), max(4, min(16, os.cpu_count() or 8)))
        modified = 0
        errors = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="RBVP-VMT") as pool:
            for ok, path, err in pool.map(edit_one, all_files):
                if ok:
                    modified += 1
                else:
                    errors.append((path, err))

        def folder_for_path(path):
            path_case = os.path.abspath(path).casefold()
            best = None
            best_len = -1
            for folder in folders:
                root_path = os.path.abspath(folder).casefold().rstrip("\\") + "\\"
                if path_case.startswith(root_path) and len(root_path) > best_len:
                    best = folder
                    best_len = len(root_path)
            return best

        result_map = {}
        for path, err in errors:
            result_map.setdefault(folder_for_path(path), []).append((path, err))

        for index, (folder, scanned) in enumerate(folder_counts, 1):
            folder_name = os.path.basename(os.path.normpath(folder))
            folder_errors = result_map.get(folder, [])
            failed = len(folder_errors)
            success = max(0, scanned - failed)
            self._write_log(
                self.vmt_log,
                f"[完成] {folder_name}（{index}/{total_folders}）\n"
                f"扫描 VMT：{scanned:,}\n"
                f"成功修改：{success:,}\n"
                f"失败：{failed:,}\n"
            )
            for path, err in folder_errors[:20]:
                self._write_log(self.vmt_log, f"[错误] {path}\n{err}\n")
            if failed > 20:
                self._write_log(self.vmt_log, f"[提示] 该 Mod 其余 {failed - 20:,} 个错误已省略。\n")
            self._write_log(self.vmt_log, "\n")

        self._write_log(
            self.vmt_log,
            f"[批量完成]\nMod：{total_folders:,}\nVMT：{len(all_files):,}\n"
            f"成功修改：{modified:,}\n失败：{len(errors):,}\n\n"
        )
        self.root.after(0, lambda: self.top_status_var.set("环境就绪"))

