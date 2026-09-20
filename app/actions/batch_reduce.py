from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path
from tkinter import messagebox

from core.batch_reduce import (
    _runtime_root,
    _start_max_worker,
    _terminate_max_worker,
    prepare_staging_smd,
    reduce_smd_with_max,
    _strip_qc_lod_blocks,
    resolve_model_smd_from_qc,
    resolve_model_smds_from_qc,
    scan_batch_reduce_mdls,
    is_batch_reduce_done, mark_batch_reduce_done,
    mark_batch_reduce_failed, clear_batch_reduce_failure, MaxAssertionError,
)
from core.crowbar import close_all_crowbar, CrowbarError, decompile_mdl, compile_qc_via_nekomdl
from core.paths import format_path


class BatchReduceActionsMixin:
    """Batch reduction controller: Crowbar decompile/compile + one Max session."""

    def _set_batch_reduce_button_running(self, running: bool):
        def update():
            try:
                if running:
                    self.batch_reduce_scan_btn.set_enabled(False)
                    self.batch_reduce_start_btn.set_text("终止批量减面")
                    self.batch_reduce_start_btn.set_command(self._stop_batch_reduce)
                    self.batch_reduce_start_btn.set_enabled(True)
                else:
                    self.batch_reduce_scan_btn.set_enabled(True)
                    self.batch_reduce_start_btn.set_text("开始批量减面")
                    self.batch_reduce_start_btn.set_command(self._start_batch_reduce)
                    self.batch_reduce_start_btn.set_enabled(True)
            except Exception:
                pass
        try:
            self.root.after(0, update)
        except Exception:
            pass

    def _start_batch_reduce_scan(self):
        addons_dir = format_path(self.addons_dir_path.get().strip())
        if not os.path.isdir(addons_dir):
            messagebox.showerror("错误", "找不到 Addons 目录，请先在设置中确认路径。")
            self._show_page("settings")
            return
        if getattr(self, "_batch_reduce_scan_thread", None) is not None and self._batch_reduce_scan_thread.is_alive():
            return
        for item in self.batch_reduce_tree.get_children():
            self.batch_reduce_tree.delete(item)
        self._batch_reduce_hits = []
        self.batch_reduce_status_var.set("正在扫描符合条件的 MDL…")
        self.top_status_var.set("批量减面预检中")
        self._batch_reduce_scan_thread = threading.Thread(
            target=self._worker_batch_reduce_scan, args=(addons_dir,), daemon=True
        )
        self._batch_reduce_scan_thread.start()

    def _worker_batch_reduce_scan(self, addons_dir: str):
        try:
            hits = scan_batch_reduce_mdls(addons_dir, min_faces=10)
        except Exception as exc:
            self.root.after(0, lambda exc=exc: self.batch_reduce_status_var.set(f"扫描失败：{exc}"))
            self.root.after(0, lambda: self.top_status_var.set("环境就绪"))
            return

        # 扫描阶段就剔除已经成功完成、且当前 MDL 指纹仍匹配的模型。
        # 这样“重新扫描符合条件的模型”的数量本身就是待处理数量，
        # 不会把历史已减面的 MDL 再算进去。
        pending_hits = [
            hit for hit in hits
            if not is_batch_reduce_done(
                addons_dir, str(hit.get("relative_path", "")), str(hit.get("mdl_path", ""))
            )
        ]
        self._batch_reduce_hits = pending_hits

        def publish():
            for item in self.batch_reduce_tree.get_children():
                self.batch_reduce_tree.delete(item)
            for hit in pending_hits:
                self.batch_reduce_tree.insert(
                    "", "end",
                    values=("待处理", f"{int(hit['triangles']):,}", hit["relative_path"]),
                )
            self.batch_reduce_status_var.set(
                f"扫描完成 · 找到 {len(pending_hits):,} 个待减面静态模型（面数 > 10）"
                + (f" · 已自动排除 {len(hits) - len(pending_hits):,} 个已完成模型" if len(hits) != len(pending_hits) else "")
            )
            self.top_status_var.set("环境就绪")

        self.root.after(0, publish)

    def _parse_batch_reduce_params(self):
        try:
            reduce_percent = float(self.batch_reduce_ratio_var.get().strip())
        except (ValueError, TypeError):
            raise ValueError("目标保留比例必须是数字。")
        if not 0.1 <= reduce_percent <= 100.0:
            raise ValueError("目标保留比例必须在 0.1～100 之间。")

        smooth_enabled = bool(self.batch_reduce_smooth_var.get())
        smooth_auto = bool(self.batch_reduce_smooth_auto_var.get())
        try:
            smooth_angle = float(self.batch_reduce_smooth_angle_var.get().strip())
        except (ValueError, TypeError):
            raise ValueError("平滑角度阈值必须是数字。")
        if not 0.0 <= smooth_angle <= 180.0:
            raise ValueError("平滑角度阈值必须在 0～180° 之间。")
        return reduce_percent, smooth_enabled, smooth_auto, smooth_angle

    def _start_batch_reduce(self):
        if getattr(self, "_batch_reduce_thread", None) is not None and self._batch_reduce_thread.is_alive():
            return
        addons_dir = format_path(self.addons_dir_path.get().strip())
        crowbar = format_path(self.crowbar_exe_path.get().strip())
        nekomdl = format_path(self.nekomdl_exe_path.get().strip())
        max_exe = format_path(self.threedsmax_exe_path.get().strip())

        if not os.path.isdir(addons_dir):
            messagebox.showerror("错误", "找不到 Addons 目录，请先在设置中确认路径。")
            self._show_page("settings")
            return
        if not os.path.isfile(crowbar):
            messagebox.showerror("错误", "找不到有效的 Crowbar，请先在设置中配置。")
            self._show_page("settings")
            return
        if not os.path.isfile(max_exe):
            messagebox.showerror("错误", "找不到有效的 3DSMAX.exe，请先在设置中配置。")
            self._show_page("settings")
            return
        if not nekomdl:
            messagebox.showerror("错误", "未找到 NekoMDL，请先在设置中配置。")
            self._show_page("settings")
            return

        try:
            reduce_percent, smooth_enabled, smooth_auto, smooth_angle = self._parse_batch_reduce_params()
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        raw_hits = list(getattr(self, "_batch_reduce_hits", []) or [])
        profile = {
            "reduce_percent": reduce_percent,
            "smooth_enabled": smooth_enabled,
            "smooth_auto": smooth_auto,
            "smooth_angle": smooth_angle,
        }
        hits = [
            h for h in raw_hits
            if not is_batch_reduce_done(
                addons_dir, str(h.get("relative_path", "")), str(h.get("mdl_path", "")), profile=profile
            )
        ]
        skipped = len(raw_hits) - len(hits)
        if skipped:
            self._write_log(self.batch_reduce_log, f"[断点续跑] 已跳过 {skipped:,} 个此前已成功完成的 MDL。\n\n")
        if not raw_hits:
            messagebox.showinfo("提示", "请先点击“扫描符合条件的模型”，确认目标列表后再开始批量减面。")
            return
        if not hits:
            messagebox.showinfo("提示", "扫描到的模型已经全部完成，无需重复减面。")
            return

        self._batch_reduce_stop_event = threading.Event()
        self._batch_reduce_thread = threading.Thread(
            target=self._worker_batch_reduce,
            args=(addons_dir, crowbar, nekomdl, max_exe, hits, reduce_percent, smooth_enabled, smooth_auto, smooth_angle, self._batch_reduce_stop_event),
            daemon=True,
        )
        self._set_batch_reduce_button_running(True)
        self.batch_reduce_status_var.set(f"准备处理 {len(hits):,} 个 MDL…")
        self.top_status_var.set("批量减面运行中")
        self._batch_reduce_thread.start()

    def _stop_batch_reduce(self):
        event = getattr(self, "_batch_reduce_stop_event", None)
        if event is not None:
            event.set()
        self.batch_reduce_status_var.set("正在终止当前任务…")
        self.top_status_var.set("正在终止批量减面")
        proc = getattr(self, "_batch_reduce_max_proc", None)
        if proc is not None:
            _terminate_max_worker(proc)

    @staticmethod
    def _set_tree_row(tree, index: int, status: str):
        def update():
            children = tree.get_children()
            if 0 <= index < len(children):
                values = list(tree.item(children[index], "values"))
                if values:
                    values[0] = status
                    tree.item(children[index], values=tuple(values))
        return update

    def _worker_batch_reduce(self, addons_dir, crowbar, nekomdl, max_exe, hits,
                             reduce_percent, smooth_enabled, smooth_auto, smooth_angle, stop_event):
        overall_start = time.perf_counter()
        success = 0
        failed = 0
        completed_crowbar_tasks = 0
        crowbar_batch_limit = 30
        max_proc = None
        runtime_root = _runtime_root() / f"session_{os.getpid()}_{int(time.time() * 1000)}"
        runtime_root.mkdir(parents=True, exist_ok=True)
        max_queue = None
        max_start_thread = None
        max_start_result = {"value": None, "error": None}
        max_session_broken = False
        failed_details = []

        def _prewarm_max():
            try:
                result = _start_max_worker(max_exe, runtime_root)
                max_start_result["value"] = result
            except Exception as exc:
                max_start_result["error"] = exc

        def log(text: str):
            self.root.after(0, lambda text=text: self._write_log(self.batch_reduce_log, text))

        try:
            desktop = Path.home() / "Desktop"
            decompiled_root = desktop / "decompiled"
            decompiled_root.mkdir(parents=True, exist_ok=True)

            # 每个批次只启动一个 3DSMAX 工作会话；异步预热，不阻塞第一个 Crowbar Decompile。
            log("[3DSMAX] 后台启动一次工作会话；Crowbar 立即开始反编译。\n\n")
            max_start_thread = threading.Thread(target=_prewarm_max, daemon=True)
            max_start_thread.start()

            prefetch_thread = None
            prefetch_result = None
            prefetch_index = None

            def _start_prefetch(next_index, next_hit):
                result = {"value": None, "error": None}

                def worker():
                    try:
                        next_src = str(next_hit["mdl_path"])
                        next_rel = str(next_hit["relative_path"]).replace("/", os.sep)
                        next_parts = Path(next_rel).parts
                        next_model_index = next((i for i, part in enumerate(next_parts[:-1]) if part.casefold() == "models"), -1)
                        if next_model_index < 0:
                            raise CrowbarError("无法从预取 MDL 路径确定 Mod 根目录。")
                        next_model_subdir = Path(*next_parts[next_model_index + 1:-1])
                        next_mod_root = os.path.join(addons_dir, *next_parts[:next_model_index])
                        next_addon_model_dir = os.path.join(next_mod_root, "models", *next_model_subdir.parts)
                        # 不让相同 stem 的模型互相清掉 Desktop\decompiled 产物；这种情况回退为串行。
                        current_stem = Path(str(hits[next_index - 1]["mdl_path"])).stem.casefold()
                        if Path(next_src).stem.casefold() == current_stem:
                            result["value"] = None
                            return
                        qc = decompile_mdl(crowbar, next_src, str(decompiled_root), stop_event=stop_event)
                        if not qc or not os.path.isfile(qc):
                            raise CrowbarError("预取反编译完成但没有找到 QC 文件。")
                        lod_removed_next = _strip_qc_lod_blocks(str(qc))
                        refs = [Path(x) for x in resolve_model_smds_from_qc(qc, str(decompiled_root))]
                        result["value"] = {
                            "qc_path": qc,
                            "reference_smds": [str(x) for x in refs],
                            "model_subdir": next_model_subdir,
                            "addon_model_dir": next_addon_model_dir,
                            "relative_path": next_rel,
                            "parts": next_parts,
                            "lod_removed": len(lod_removed_next),
                        }
                    except Exception as exc:
                        result["error"] = exc
                    finally:
                        # 预取只需要产物，不需要占着 Crowbar GUI；当前模型 Compile 前后均可安全重新启动。
                        try:
                            close_all_crowbar(force=True)
                        except Exception:
                            pass

                t = threading.Thread(target=worker, daemon=True)
                t.start()
                return t, result

            for index, hit in enumerate(hits, 1):
                if stop_event.is_set():
                    break
                src_mdl = str(hit["mdl_path"])
                rel = str(hit["relative_path"]).replace("/", os.sep)
                parts = Path(rel).parts
                model_index = next((i for i, part in enumerate(parts[:-1]) if part.casefold() == "models"), -1)
                if model_index < 0:
                    failed += 1
                    log(f"[失败] {rel}\n无法从 MDL 路径确定 Mod 根目录。\n\n")
                    continue
                mod_root = os.path.join(addons_dir, *parts[:model_index])
                model_subdir = Path(*parts[model_index + 1:-1])
                addon_model_dir = os.path.join(mod_root, "models", *model_subdir.parts)
                self.root.after(0, self._set_tree_row(self.batch_reduce_tree, index - 1, "处理中"))
                log(
                    f"[处理 {index}/{len(hits)}]\n"
                    f"MDL：{rel}\n"
                    f"步骤 1/3：Crowbar 反编译\n\n"
                )
                # 3DSMAX 已在本批次开头异步启动；这里绝不等待它，直接执行 Crowbar。
                try:
                    # 第一个模型正常反编译；后续模型优先复用上一轮后台预取的结果。
                    use_prefetched = prefetch_index == index and prefetch_thread is not None and prefetch_result is not None
                    if use_prefetched:
                        prefetch_thread.join()
                        if prefetch_result.get("error") is not None:
                            log(f"[Crowbar] 后台预取失败，回退为当前模型串行反编译：{prefetch_result['error']}\n\n")
                    if use_prefetched and prefetch_result.get("value") is not None:
                        pv = prefetch_result["value"]
                        qc_path = pv["qc_path"]
                        reference_smds = [Path(x) for x in pv.get("reference_smds", [])]
                        lod_removed = pv.get("lod_removed", 0)
                        log(f"[Crowbar] 已提前完成本样本反编译，共 {len(reference_smds)} 个模型 SMD。\n\n")
                        prefetch_thread = None
                        prefetch_result = None
                        prefetch_index = None
                    else:
                        qc_path = decompile_mdl(crowbar, src_mdl, str(decompiled_root), stop_event=stop_event)
                        if not qc_path or not os.path.isfile(qc_path):
                            raise CrowbarError("反编译完成但没有找到 QC 文件。")
                        lod_removed = _strip_qc_lod_blocks(str(qc_path))
                        reference_smds = [Path(x) for x in resolve_model_smds_from_qc(qc_path, str(decompiled_root))]
                        prefetch_thread = None
                        prefetch_result = None
                        prefetch_index = None

                    log("反编译模型 SMD：" + ", ".join(p.name for p in reference_smds) + "\n\n")
                    if lod_removed:
                        log(f"[LOD] 检测到 LOD，已删除 {len(lod_removed)} 个 LOD SMD，并清理 QC 对应 $lod 段。\n\n")

                    if max_queue is None:
                        if max_session_broken:
                            # 上一个 MDL 触发了 Assertion；旧 Max 已强制结束，这里必须建立全新会话。
                            restart_root = runtime_root / f"max_restart_{index:04d}"
                            self._batch_reduce_max_proc, max_queue, _ = _start_max_worker(max_exe, restart_root)
                            max_proc = self._batch_reduce_max_proc
                            max_session_broken = False
                            log("[3DSMAX] 已重建干净工作会话，继续下一个 MDL。\n\n")
                        else:
                            if max_start_thread is not None:
                                max_start_thread.join()
                            if max_start_result["error"] is not None:
                                raise RuntimeError(f"3DSMAX 工作会话启动失败：{max_start_result['error']}")
                            if max_start_result["value"] is None:
                                raise RuntimeError("3DSMAX 工作会话未建立。")
                            self._batch_reduce_max_proc, max_queue, _ = max_start_result["value"]
                            max_proc = self._batch_reduce_max_proc
                            log("[3DSMAX] 工作会话已就绪。\n\n")

                    if stop_event.is_set():
                        raise CrowbarError("用户已终止处理。")

                    # 当前模型进入 Max 减面后，立即后台预取下一模型的 Crowbar 反编译；
                    # 这样 Crowbar 与 Max 的耗时可以重叠。只预取一项，并在下一模型开始时消费结果。
                    if index < len(hits) and prefetch_thread is None and not stop_event.is_set():
                        prefetch_thread, prefetch_result = _start_prefetch(index + 1, hits[index])
                        prefetch_index = index + 1

                    log("步骤 2/3：3DSMAX ProOptimizer 减面" + (" + Smooth 平滑\n\n" if smooth_enabled else "\n\n"))
                    smd_failed = []
                    smd_succeeded = 0
                    for smd_pos, reference_smd in enumerate(reference_smds, 1):
                        log(f"[3DSMAX] 处理 SMD {smd_pos}/{len(reference_smds)}：{reference_smd.name}\n\n")
                        job_id = f"{index:06d}_{smd_pos:03d}"
                        input_stage, output_stage = prepare_staging_smd(str(reference_smd), runtime_root, job_id)
                        try:
                            reduce_smd_with_max(
                                max_queue,
                                job_id,
                                input_stage,
                                output_stage,
                                reduce_percent,
                                smooth_enabled,
                                smooth_auto,
                                smooth_angle,
                                timeout=1200,
                                stop_event=stop_event,
                                max_pid=max_proc.pid if max_proc is not None else None,
                                max_proc=max_proc,
                            )
                            if os.path.normcase(os.path.splitdrive(output_stage)[0]) == os.path.normcase(os.path.splitdrive(str(reference_smd))[0]):
                                os.replace(output_stage, str(reference_smd))
                            else:
                                shutil.copy2(output_stage, reference_smd)
                                try:
                                    os.remove(output_stage)
                                except OSError:
                                    pass
                            smd_succeeded += 1
                        except MaxAssertionError as smd_exc:
                            log(f"[致命] 3DSMAX Assertion Error：{reference_smd.name}\n{str(smd_exc)}\n立即终止当前 MDL 的全部步骤，不再处理剩余 SMD。\n\n")
                            # 该 Max 会话已经被 watchdog 强制终止；当前 MDL 不再 Compile。
                            raise
                        except Exception as smd_exc:
                            smd_failed.append((reference_smd.name, str(smd_exc)))
                            log(f"[跳过] SMD 导入/处理失败：{reference_smd.name}\n原因：{smd_exc}\n保留原始 SMD，继续处理下一个 SMD。\n\n")
                        finally:
                            try:
                                if os.path.isfile(output_stage):
                                    os.remove(output_stage)
                            except OSError:
                                pass

                    if smd_succeeded == 0:
                        raise RuntimeError("当前 MDL 的所有模型 SMD 均处理失败，已跳过该 MDL。")

                    # 当前模型即将 Compile；预取模型的反编译即便尚未结束也必须先收尾，
                    # 确保 Crowbar 不会与当前 QC Compile 发生 GUI 会话冲突。
                    if prefetch_thread is not None:
                        prefetch_thread.join()
                        # 保留 prefetch_thread / prefetch_result 到下一轮，让下一模型直接消费已经完成的反编译结果。

                    if stop_event.is_set():
                        raise CrowbarError("用户已终止处理。")
                    log("步骤 3/3：Crowbar Compile，并覆盖原 MDL\n\n")
                    _, _, desktop_output_dir = compile_qc_via_nekomdl(
                        crowbar, nekomdl, "", qc_path, stop_event=stop_event
                    )

                    source_model_dir = os.path.join(desktop, "models", *model_subdir.parts)
                    if not os.path.isdir(source_model_dir):
                        raise CrowbarError(f"找不到 Crowbar 编译输出目录：{source_model_dir}")
                    os.makedirs(addon_model_dir, exist_ok=True)
                    target_stem = Path(parts[-1]).stem.casefold()
                    valid_suffixes = (".mdl", ".vvd", ".phy", ".ani", ".dx80.vtx", ".dx90.vtx", ".sw.vtx")
                    copied = 0
                    for name in os.listdir(source_model_dir):
                        src = os.path.join(source_model_dir, name)
                        if not os.path.isfile(src):
                            continue
                        low = name.casefold()
                        if not low.startswith(target_stem + ".") or not low.endswith(valid_suffixes):
                            continue
                        shutil.copy2(src, os.path.join(addon_model_dir, name))
                        copied += 1
                    if copied == 0:
                        raise CrowbarError(f"编译完成但未找到可覆盖的模型文件：{parts[-1]}")

                    # 只有覆盖成功后才写入持久化记录。记录的是覆盖后的 MDL SHA-256，
                    # 因此程序关闭/重启后仍可准确判断这个 MDL 是否就是最近一次成功处理的结果。
                    source_before = {}
                    try:
                        import hashlib
                        st_before = os.stat(src_mdl)
                        source_before = {
                            "size": int(st_before.st_size),
                            "mtime_ns": int(getattr(st_before, "st_mtime_ns", int(st_before.st_mtime * 1_000_000_000))),
                        }
                    except OSError:
                        pass
                    profile = {
                        "reduce_percent": reduce_percent,
                        "smooth_enabled": smooth_enabled,
                        "smooth_auto": smooth_auto,
                        "smooth_angle": smooth_angle,
                    }
                    mark_batch_reduce_done(addons_dir, rel, src_mdl, profile=profile, source_before=source_before)
                    clear_batch_reduce_failure(addons_dir, rel)
                    success += 1
                    self.root.after(0, self._set_tree_row(self.batch_reduce_tree, index - 1, "完成"))
                    status_suffix = f" · 跳过 {len(smd_failed)} 个 SMD" if smd_failed else ""
                    log(
                        f"[完成] {rel}{status_suffix}\n"
                        f"保留比例：{reduce_percent:g}%"
                        + (f" · 平滑 {smooth_angle:g}°" + (" · 自动平滑" if smooth_auto else " · 手动平滑") if smooth_enabled else " · 未启用平滑")
                        + f"\n覆盖文件：{copied}\n\n"
                    )
                except MaxAssertionError as exc:
                    failed += 1
                    failed_details.append((rel, "3DSMAX ASSERTION ERROR", "SMD_IMPORT", str(exc)))
                    mark_batch_reduce_failed(addons_dir, rel, "3DSMAX_ASSERTION_ERROR", stage="SMD_IMPORT", detail=str(exc))
                    self.root.after(0, self._set_tree_row(self.batch_reduce_tree, index - 1, "失败"))
                    log(f"[失败] {rel}\n原因：3DSMAX ASSERTION ERROR\n{exc}\n已终止当前样本全部执行步骤。\n将重建 3DSMAX 工作会话后继续下一个样本。\n\n")
                    max_queue = None
                    max_proc = None
                    max_session_broken = True
                    self._batch_reduce_max_proc = None
                    # 失败模型绝不进入完成记录；下一轮可以人工决定是否重试。
                except Exception as exc:
                    failed += 1
                    stage = "CROWBAR" if isinstance(exc, CrowbarError) else "UNKNOWN"
                    failed_details.append((rel, "PROCESS_ERROR", stage, str(exc)))
                    mark_batch_reduce_failed(addons_dir, rel, "PROCESS_ERROR", stage=stage, detail=str(exc))
                    self.root.after(0, self._set_tree_row(self.batch_reduce_tree, index - 1, "失败"))
                    log(f"[失败] {rel}\n{exc}\n\n")
                finally:
                    if not stop_event.is_set():
                        completed_crowbar_tasks += 1
                        if completed_crowbar_tasks >= crowbar_batch_limit:
                            closed = close_all_crowbar(force=True)
                            log(
                                f"[Crowbar] 已完成 {completed_crowbar_tasks} 个模型任务，关闭所有 Crowbar"
                                + (f"（{closed} 个进程）" if closed else "")
                                + "，下一项将重新启动。\n\n"
                            )
                            completed_crowbar_tasks = 0

            stopped = stop_event.is_set()
            elapsed = time.perf_counter() - overall_start
            if stopped:
                log("[已终止] 用户终止了批量减面，后续 MDL 不再处理。\n\n")
            log(
                f"[完成] {'已终止 · ' if stopped else ''}"
                f"成功 {success:,} · 失败 {failed:,} / 共 {len(hits):,} 个 MDL\n"
                f"[用时] 总用时 {elapsed:.2f} 秒\n\n"
            )
            if failed_details:
                log("失败列表（失败记录已持久化，不会被永久跳过）：\n")
                for n, (path, reason, stage, detail) in enumerate(failed_details, 1):
                    log(f"{n}. {path}\n原因：{reason}\n阶段：{stage}\n{detail}\n\n")
            self.root.after(0, lambda: self.batch_reduce_status_var.set(
                f"{'已终止' if stopped else '处理完成'} · 成功 {success:,} · 失败 {failed:,} / 共 {len(hits):,} 个 MDL"
            ))
        except Exception as exc:
            elapsed = time.perf_counter() - overall_start
            log(f"[错误] 批量减面初始化/运行失败：{exc}\n[用时] 总用时 {elapsed:.2f} 秒\n\n")
            self.root.after(0, lambda exc=exc: self.batch_reduce_status_var.set(f"运行失败：{exc}"))
        finally:
            if max_proc is not None:
                _terminate_max_worker(max_proc)
            try:
                self._batch_reduce_max_proc = None
            except Exception:
                pass
            self._set_batch_reduce_button_running(False)
            self.root.after(0, lambda: self.top_status_var.set("环境就绪"))
