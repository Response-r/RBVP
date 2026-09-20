import os
import threading
import subprocess
import shutil
import concurrent.futures
import time
import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


from core.paths import format_path
from core.vpk import parse_vpk_entries, _vpk_archive_path, pack_directory_internal_vpk
from core.conflict import collect_mod_folders

class VPKActionsMixin:
    def _on_vpk_summary_select(self, _event=None):
            selected = self.summary_tree.selection()
            if not selected:
                return
            values = self.summary_tree.item(selected[0], "values")
            if not values:
                return
            vpk_name = values[0]
            for item in self.detail_tree.get_children():
                self.detail_tree.delete(item)
            models = sorted(self.vpk_detail_data.get(vpk_name, []), key=lambda m: (int(m.get("tris",0)), int(m.get("verts",0)), str(m.get("mdl_path","")).lower()), reverse=True)
            for m in models:
                self.detail_tree.insert("", "end", values=(m["mdl_path"], f'{m["verts"]:,}', f'{m["tris"]:,}'))


    def _start_pack_vpk(self):
        vpk_exe = self.vpk_exe_path.get().strip()
        if not os.path.exists(vpk_exe):
            messagebox.showerror("错误", "未指定有效 VPK.exe 路径，请先在设置中配置。")
            self._show_page("settings")
            return

        # Windows/Tk 的 askdirectory 原生只支持单目录选择。
        # 为了避免之前 ctypes 方案导致 access violation，这里采用稳定的
        # “选择一个父目录，然后自动收集其直接子文件夹”方式进行批量打包。
        parent_dir = filedialog.askdirectory(title="选择包含多个待打包文件夹的父目录")
        if not parent_dir:
            return
        parent_dir = format_path(parent_dir)

        try:
            # 与 VMT 批量修改统一：只有真正符合 Mod 特征的子文件夹才进入打包队列。
            # 判定条件由 core.conflict.collect_mod_folders 集中维护：
            # addoninfo.txt / materials / models / particles / scripts / sound 至少存在一个。
            target_dirs = [format_path(p) for p in collect_mod_folders(parent_dir)]
        except OSError as e:
            messagebox.showerror("错误", f"读取父目录失败：\n{e}")
            return

        if not target_dirs:
            messagebox.showwarning(
                "没有找到 Mod",
                "所选父目录下没有检测到符合条件的 Mod 文件夹。\n\n"
                "判定条件：addoninfo.txt、materials、models、particles、scripts、sound 至少存在一个。"
            )
            return

        self._clear_log(self.pack_log)
        self.top_status_var.set(f"VPK 批量打包运行中 · 共 {len(target_dirs)} 个文件夹")
        threading.Thread(
            target=self._worker_pack_batch,
            args=(vpk_exe, target_dirs),
            daemon=True,
        ).start()


    def _start_unpack_vpk(self):
        # 解包现在使用 RBVP 内置 VPK 解析器，不依赖外部 vpk.exe。
        files = filedialog.askopenfilenames(title="选择要解包的 VPK 文件（支持多选）", filetypes=[("VPK 文件", "*.vpk")])
        if not files:
            return
        self._clear_log(self.unpack_log)
        self.top_status_var.set("VPK 解包运行中")
        threading.Thread(target=self._worker_unpack_internal, args=(list(files),), daemon=True).start()


    def _worker_pack_batch(self, vpk_exe, target_dirs):
        total = len(target_dirs)
        for index, target_dir in enumerate(target_dirs, 1):
            try:
                folder_name = os.path.basename(os.path.normpath(target_dir))
                self.root.after(0, lambda i=index, t=total, n=folder_name: 
                                self.top_status_var.set(f"VPK 批量打包运行中 · {i}/{t} · {n}"))
                self._worker_pack_robust(vpk_exe, target_dir, batch_index=index, batch_total=total)
            except Exception as e:
                folder_name = os.path.basename(os.path.normpath(target_dir))
                self._write_log(self.pack_log, f"[异常] {folder_name}: {e}\n\n")
        self.root.after(0, lambda: self.top_status_var.set("环境就绪"))


    def _worker_pack_robust(self, vpk_exe, target_dir, batch_index=None, batch_total=None):
        """调用官方 vpk.exe 打包；若官方工具未产生输出，再使用 RBVP 内置 VPK1 回退。"""
        parent_dir = os.path.dirname(target_dir)
        folder_name = os.path.basename(os.path.normpath(target_dir))
        temp_dir = os.path.join(parent_dir, "temp")
        os.makedirs(temp_dir, exist_ok=True)

        def output_candidates():
            items = []
            try:
                for fn in os.listdir(parent_dir):
                    low = fn.casefold()
                    base = folder_name.casefold()
                    if low == base + ".vpk" or low == base + "_dir.vpk" or (low.startswith(base + "_") and low.endswith(".vpk")):
                        items.append(os.path.join(parent_dir, fn))
            except OSError:
                pass
            return items

        # 记录旧输出，避免把历史文件误判成这一次的结果。
        before = {}
        for path in output_candidates():
            try:
                st = os.stat(path)
                before[path] = (st.st_mtime_ns, st.st_size)
            except OSError:
                pass

        prefix = f" ({batch_index}/{batch_total})" if batch_index and batch_total else ""
        self._write_log(self.pack_log, f"[打包中] {folder_name}{prefix}\n\n")

        try:
            res = subprocess.run(
                [vpk_exe, target_dir],
                cwd=parent_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace"
            )

            stdout = (res.stdout or "").strip()
            stderr = (res.stderr or "").strip()

            # 以实际生成文件为准，而不是单纯依赖返回码。
            after = output_candidates()
            produced = []
            for path in after:
                try:
                    st = os.stat(path)
                    now = (st.st_mtime_ns, st.st_size)
                    if path not in before or before[path] != now:
                        produced.append(path)
                except OSError:
                    pass

            # 官方工具已经产生输出：即便返回码异常，也直接使用实际生成结果。
            if produced:
                if res.returncode != 0:
                    self._write_log(
                        self.pack_log,
                        f"[提示] {folder_name}：vpk.exe 返回码 {res.returncode}，但已生成 VPK，使用实际输出。\n\n"
                    )
            else:
                # 官方工具没有生成可用输出，静默回退到内部 VPK1 打包器。
                fallback_name = folder_name + ".vpk"
                fallback_path = os.path.join(parent_dir, fallback_name)
                try:
                    count, total_bytes = pack_directory_internal_vpk(target_dir, fallback_path)
                    dst = os.path.join(temp_dir, fallback_name)
                    if os.path.exists(dst):
                        os.remove(dst)
                    shutil.move(fallback_path, dst)
                    self._write_log(
                        self.pack_log,
                        f"[完成] {folder_name}\n"
                        f"输出文件：\n{dst}\n"
                        f"共打包 {count:,} 个文件，{total_bytes:,} 字节。\n\n"
                    )
                    return True
                except Exception as fallback_error:
                    detail = stderr or stdout or f"vpk.exe 返回码 {res.returncode}，未生成输出文件。"
                    self._write_log(
                        self.pack_log,
                        f"[失败] {folder_name}\n"
                        f"vpk.exe：{detail}\n"
                        f"RBVP 内置打包器：{fallback_error}\n\n"
                    )
                    return False

            moved = []
            renamed = []
            low_base = folder_name.casefold()
            for src in sorted(produced, key=lambda p: os.path.basename(p).casefold()):
                src_name = os.path.basename(src)
                low_src = src_name.casefold()

                if low_src == low_base + ".vpk":
                    desired_name = folder_name + ".vpk"
                elif low_src == low_base + "_dir.vpk":
                    desired_name = folder_name + "_dir.vpk"
                elif low_src.startswith(low_base + "_") and low_src.endswith(".vpk"):
                    suffix = src_name[len(low_base):]
                    desired_name = folder_name + suffix
                else:
                    desired_name = src_name

                dst = os.path.join(temp_dir, desired_name)
                if os.path.exists(dst):
                    os.remove(dst)
                shutil.move(src, dst)
                moved.append(dst)
                if src_name != desired_name:
                    renamed.append(f"{src_name} → {desired_name}")

            lines = [f"[完成] {folder_name}", "输出文件："]
            lines.extend(moved)
            if renamed:
                lines.append("名称大小写已按文件夹恢复：")
                lines.extend(renamed)
            self._write_log(self.pack_log, "\n".join(lines) + "\n\n")
            return True
        except Exception as e:
            self._write_log(self.pack_log, f"[异常] {folder_name}\n{e}\n\n")
            return False


    def _worker_unpack_internal(self, files):
        """使用内置 VPK 解析器直接解包，避免依赖 vpk.exe 的目录行为和版本差异。"""
        import mmap as _mmap
        for raw_path in files:
            vpk_path = format_path(raw_path)
            name = os.path.basename(vpk_path)
            lower = name.lower()
            stem = name[:-8] if lower.endswith("_dir.vpk") else os.path.splitext(name)[0]
            out_dir = os.path.join(os.path.dirname(vpk_path), stem)
            try:
                entries = parse_vpk_entries(vpk_path)
                if not entries:
                    self._write_log(self.unpack_log, f"[失败] {name}: 无法解析 VPK 目录结构。\n")
                    continue
                os.makedirs(out_dir, exist_ok=True)
                mappings = {}
                handles = {}
                targets = {}
                def target_for(arch_idx):
                    if arch_idx in targets:
                        return targets[arch_idx]
                    target = _vpk_archive_path(vpk_path, arch_idx)
                    targets[arch_idx] = target
                    return target
                def mapping_for(arch_idx):
                    if arch_idx in mappings:
                        return mappings[arch_idx]
                    target = target_for(arch_idx)
                    fh = open(target, 'rb')
                    mm = _mmap.mmap(fh.fileno(), 0, access=_mmap.ACCESS_READ)
                    handles[arch_idx] = fh
                    mappings[arch_idx] = mm
                    return mm
                self._write_log(self.unpack_log, f"[解包中]\n{name}\n→ {out_dir} ({len(entries):,} 个文件)\n\n")
                count = 0
                try:
                    for rel_path, entry in entries.items():
                        rel_path = rel_path.replace('/', os.sep)
                        dest = os.path.join(out_dir, rel_path)
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        preload = entry.get('preload', b'') or b''
                        length = int(entry.get('length', 0))
                        if length:
                            arch_idx = int(entry.get('arch_idx', 0x7FFF))
                            mm = mapping_for(arch_idx)
                            offset = int(entry.get('offset', 0))
                            if arch_idx == 0x7FFF:
                                offset += int(entry.get('data_start', 0))
                            if offset < 0 or offset + length > len(mm):
                                raise ValueError(f"数据越界: {rel_path}")
                            with open(dest, 'wb') as out:
                                if preload:
                                    out.write(preload)
                                view = memoryview(mm)[offset:offset+length]
                                out.write(view)
                                view.release()
                        else:
                            with open(dest, 'wb') as out:
                                if preload:
                                    out.write(preload)
                        count += 1
                finally:
                    for mm in mappings.values():
                        try: mm.close()
                        except Exception: pass
                    for fh in handles.values():
                        try: fh.close()
                        except Exception: pass
                self._write_log(self.unpack_log, f"[完成]\n{name}\n→ {out_dir} ({count:,} 个文件)\n\n")
            except Exception as e:
                self._write_log(self.unpack_log, f"[异常] {name}: {e}\n")
        self.root.after(0, lambda: self.top_status_var.set("环境就绪"))

