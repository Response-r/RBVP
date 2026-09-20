import os
import shutil
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

from core.crowbar import (CrowbarError, decompile_mdl, compile_qc_via_nekomdl, find_nekomdl_exe, _extract_modelname, close_all_crowbar)
from core.paths import format_path
from core.texturegroup_clear import scan_addons_texturegroup_mdls, remove_texturegroups_from_qc


class TextureGroupClearActionsMixin:
    def _set_texturegroup_clear_button_running(self, running):
        def update():
            btn = getattr(self, "texturegroup_clear_action_btn", None)
            if not btn:
                return
            if running:
                btn.set_text("终止处理")
                btn.set_command(self._stop_texturegroup_clear)
                btn.set_enabled(True)
            else:
                btn.set_text("开始清除 $texturegroup")
                btn.set_command(self._start_texturegroup_clear)
                btn.set_enabled(True)
        try:
            self.root.after(0, update)
        except Exception:
            pass

    def _stop_texturegroup_clear(self):
        event = getattr(self, "_texturegroup_clear_stop_event", None)
        if event is not None:
            event.set()
        self.texturegroup_clear_status_var.set("正在终止当前任务…")
        self.top_status_var.set("正在终止纹理组清除")

    def _start_texturegroup_clear(self):
        addons_dir = format_path(self.addons_dir_path.get().strip())
        crowbar = format_path(self.crowbar_exe_path.get().strip())
        nekomdl = format_path(self.nekomdl_exe_path.get().strip())
        if not os.path.isdir(addons_dir):
            messagebox.showerror("错误", "找不到 Addons 目录，请先在设置中确认路径。")
            self._show_page("settings")
            return
        if not os.path.isfile(crowbar):
            messagebox.showerror("错误", "找不到有效的 Crowbar / Crowbar 命令行解码器，请先在设置中配置。")
            self._show_page("settings")
            return

        for item in self.texturegroup_clear_tree.get_children():
            self.texturegroup_clear_tree.delete(item)
        if getattr(self, "_texturegroup_clear_thread", None) is not None and self._texturegroup_clear_thread.is_alive():
            return
        self._texturegroup_clear_stop_event = threading.Event()
        self.texturegroup_clear_status_var.set("正在扫描 Addons 中的 MDL…")
        self.top_status_var.set("纹理组清除运行中")
        self._set_texturegroup_clear_button_running(True)
        self._texturegroup_clear_thread = threading.Thread(target=self._worker_texturegroup_clear, args=(addons_dir, crowbar, nekomdl, self._texturegroup_clear_stop_event), daemon=True)
        self._texturegroup_clear_thread.start()

    def _worker_texturegroup_clear(self, addons_dir, crowbar, nekomdl, stop_event):
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        decompiled_root = os.path.join(desktop, "decompiled")
        if not nekomdl:
            nekomdl = find_nekomdl_exe()
        try:
            hits = scan_addons_texturegroup_mdls(addons_dir)
            if stop_event.is_set():
                self.root.after(0, lambda: self.texturegroup_clear_status_var.set("已终止"))
                self.root.after(0, lambda: self._set_texturegroup_clear_button_running(False))
                self.root.after(0, lambda: self.top_status_var.set("环境就绪"))
                return
        except Exception as exc:
            self.root.after(0, lambda exc=exc: self.texturegroup_clear_status_var.set(f"扫描失败：{exc}"))
            self.root.after(0, lambda: self._set_texturegroup_clear_button_running(False))
            self.root.after(0, lambda: self.top_status_var.set("环境就绪"))
            return

        def init_tree():
            for hit in hits:
                self.texturegroup_clear_tree.insert("", "end", values=("待处理", hit["relative_path"]))
            self.texturegroup_clear_status_var.set(f"扫描完成 · 找到 {len(hits):,} 个含 $texturegroup 的 MDL")
        self.root.after(0, init_tree)

        if not hits:
            self.root.after(0, lambda: self._set_texturegroup_clear_button_running(False))
            self.root.after(0, lambda: self.top_status_var.set("环境就绪"))
            return
        if not nekomdl:
            self.root.after(0, lambda: self._write_log(self.texturegroup_clear_log,
                "[错误] 未找到 nekomdl.exe，无法执行自动编译。请在设置中指定 NekoMDL。\n\n"))
            self.root.after(0, lambda: self.texturegroup_clear_status_var.set("扫描完成，但未找到 nekomdl.exe"))
            self.root.after(0, lambda: self._set_texturegroup_clear_button_running(False))
            self.root.after(0, lambda: self.top_status_var.set("环境就绪"))
            return

        success = 0
        failed = 0
        crowbar_completed_count = 0
        crowbar_batch_limit = 30
        for index, hit in enumerate(hits, 1):
            if stop_event.is_set():
                break
            src_mdl = hit["mdl_path"]
            rel = hit["relative_path"].replace("/", os.sep)
            # relative_path 是相对于 Addons 的路径，例如：
            # [优化]杂草\models\props_foliage\hedge_256.mdl
            # 先定位这个 MDL 路径中的 models 目录；models 之前的部分就是 Mod 根目录。
            parts = Path(rel).parts
            model_index = next((i for i, part in enumerate(parts[:-1])
                                if part.casefold() == "models"), -1)
            if model_index < 0:
                raise CrowbarError(f"无法从 MDL 路径确定 Mod 根目录：{rel}")
            mod_root = os.path.join(addons_dir, *parts[:model_index])
            model_subdir = Path(*parts[model_index + 1:-1])
            addon_model_dir = os.path.join(mod_root, "models", *model_subdir.parts)
            try:
                self._write_log(self.texturegroup_clear_log,
                    f"[处理 {index}/{len(hits)}]\nMDL：{rel}\nCrowbar MDL input：{src_mdl}\n步骤 1/3：Crowbar 反编译…\n\n")
                qc_path = decompile_mdl(crowbar, src_mdl, decompiled_root, stop_event=stop_event)
                if not qc_path or not os.path.isfile(qc_path):
                    raise CrowbarError("反编译完成但没有找到对应 QC 文件。")

                if stop_event.is_set():
                    raise CrowbarError("用户已终止处理。")
                self._write_log(self.texturegroup_clear_log, "步骤 2/3：删除 QC 中的 $texturegroup…\n\n")
                with open(qc_path, "r", encoding="utf-8-sig", errors="replace") as f:
                    qc_text = f.read()
                new_qc, removed = remove_texturegroups_from_qc(qc_text)
                if removed <= 0:
                    raise CrowbarError("QC 中没有找到可移除的 $texturegroup 区块。")
                with open(qc_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(new_qc)

                self._write_log(self.texturegroup_clear_log,
                    f"已删除 {removed} 个 $texturegroup 区块。\n"
                    "步骤 3/3：调用 Crowbar Compile（使用 Crowbar 当前配置的 NekoMDL）并覆盖原解包 VPK…\n\n")
                if stop_event.is_set():
                    raise CrowbarError("用户已终止处理。")
                _, _, desktop_output_dir = compile_qc_via_nekomdl(
                    crowbar, nekomdl, "", qc_path, stop_event=stop_event
                )

                # 完成 Compile 后，严格模拟用户手动操作：
                # 1) 找到桌面的 models 文件夹；
                # 2) 进入本次 Mod 根目录（例如 [优化]杂草）；
                # 3) 把 models 中本次模型目录里的编译产物直接覆盖过去。
                # 不再把 Mod 名称重复拼接，也不再根据 $modelname 重建 Addons 路径。
                desktop_models_root = os.path.join(os.path.expanduser("~"), "Desktop", "models")
                source_model_dir = os.path.join(desktop_models_root, *model_subdir.parts)
                if not os.path.isdir(source_model_dir):
                    raise CrowbarError(f"找不到 Crowbar 编译输出目录：{source_model_dir}")

                os.makedirs(addon_model_dir, exist_ok=True)
                target_stem = Path(parts[-1]).stem.casefold()
                valid_suffixes = (
                    ".mdl", ".vvd", ".phy", ".ani",
                    ".dx80.vtx", ".dx90.vtx", ".sw.vtx"
                )
                copied = 0
                for name in os.listdir(source_model_dir):
                    src = os.path.join(source_model_dir, name)
                    if not os.path.isfile(src):
                        continue
                    low = name.casefold()
                    if not low.startswith(target_stem + "."):
                        continue
                    if not low.endswith(valid_suffixes):
                        continue
                    shutil.copy2(src, os.path.join(addon_model_dir, name))
                    copied += 1
                if copied == 0:
                    raise CrowbarError(f"编译成功但未找到可覆盖的模型文件：{parts[-1]}")

                self._write_log(self.texturegroup_clear_log,
                    f"编译输出：{desktop_output_dir}\n"
                    f"覆盖目标：{mod_root}\n"
                    f"写回：{addon_model_dir}\n"
                    f"覆盖文件：{copied}\n\n")
                success += 1
                self._set_texturegroup_clear_row(index - 1, "完成", rel)
            except Exception as exc:
                failed += 1
                self._write_log(self.texturegroup_clear_log, f"[失败] {rel}\n{exc}\n\n")
                self._set_texturegroup_clear_row(index - 1, "失败", rel)
            finally:
                # 每完成一个 MDL 的完整 Crowbar 任务计一次；达到 30 次后
                # 主动关闭系统中所有 Crowbar，让下一项重新建立干净会话。
                # 这样不会改变任务串行性，也不会引入并发串档。
                if not stop_event.is_set():
                    crowbar_completed_count += 1
                    if crowbar_completed_count >= crowbar_batch_limit:
                        closed = close_all_crowbar(force=True)
                        self._write_log(
                            self.texturegroup_clear_log,
                            f"[Crowbar] 已完成 {crowbar_completed_count} 个任务，关闭所有 Crowbar"
                            + (f"（{closed} 个进程）" if closed else "")
                            + "，下一项将重新启动。\n\n"
                        )
                        crowbar_completed_count = 0

        stopped = stop_event.is_set()
        if stopped:
            self._write_log(self.texturegroup_clear_log, "[已终止] 用户终止了批量处理，后续 MDL 不再调用 Crowbar。\n\n")
        self.root.after(0, lambda: self.texturegroup_clear_status_var.set(
            (f"已终止 · 已成功 {success:,} · 失败 {failed:,} / 共 {len(hits):,} 个 MDL") if stopped else
            f"处理完成 · 成功 {success:,} · 失败 {failed:,} / 共 {len(hits):,} 个 MDL"))
        self.root.after(0, lambda: self._set_texturegroup_clear_button_running(False))
        self.root.after(0, lambda: self.top_status_var.set("环境就绪"))

    def _set_texturegroup_clear_row(self, index, status, rel):
        def update():
            children = self.texturegroup_clear_tree.get_children()
            if 0 <= index < len(children):
                self.texturegroup_clear_tree.item(children[index], values=(status, rel))
        self.root.after(0, update)
