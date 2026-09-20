import os
import threading
import subprocess
import concurrent.futures
import time
import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


from core.models import analyze_vpk_fast

class ModelActionsMixin:
    def _analyze_models(self):
            addons_dir = self.addons_dir_path.get().strip()
            if not os.path.exists(addons_dir):
                messagebox.showerror("错误", "找不到 Addons 目录路径，请先在设置中确认。")
                self._show_page("settings")
                return
            for tree in (self.summary_tree, self.detail_tree, self.compare_tree):
                for item in tree.get_children():
                    tree.delete(item)
            self.vpk_detail_data.clear()
            self.model_count_var.set("0")
            self.model_vertex_var.set("0")
            self.model_tri_var.set("0")
            self.model_status_var.set("扫描中…")
            self.top_status_var.set("模型分析运行中")
            threading.Thread(target=self._worker_analyze_models, args=(addons_dir,), daemon=True).start()


    def _load_model_disk_cache(self, addons_dir):
            try:
                with open(self._model_cache_file(addons_dir), 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('version') != 4:
                    return {}
                return data.get('items') or {}
            except (OSError, ValueError, TypeError):
                return {}


    def _model_cache_file(self, addons_dir):
            return os.path.join(addons_dir, '.rbvp_model_cache.json')


    def _save_model_disk_cache(self, addons_dir, cache):
            path=self._model_cache_file(addons_dir); tmp=path+'.tmp'
            try:
                with open(tmp,'w',encoding='utf-8') as f:
                    json.dump({'version':4,'items':cache},f,ensure_ascii=False,separators=(',',':'))
                os.replace(tmp,path)
            except OSError:
                try: os.remove(tmp)
                except OSError: pass


    def _worker_analyze_models(self, addons_dir):
            overall_start=time.perf_counter()
            try:
                vpk_files=sorted(os.path.join(addons_dir,e.name) for e in os.scandir(addons_dir)
                                 if e.is_file() and e.name.lower().endswith('.vpk'))
            except OSError as e:
                self.root.after(0,lambda e=e:self.model_status_var.set(f'读取 Addons 失败: {e}'))
                self.root.after(0,lambda:self.top_status_var.set('环境就绪'))
                return
            if not vpk_files:
                self.root.after(0,lambda:self.model_status_var.set('未找到 VPK 文件'))
                self.root.after(0,lambda:self.top_status_var.set('环境就绪'))
                return

            disk_cache=self._load_model_disk_cache(addons_dir)
            results=[]; missing=[]; live=set()
            for path in vpk_files:
                try: st=os.stat(path)
                except OSError: continue
                key=f'{os.path.abspath(path).lower()}|{st.st_mtime_ns}|{st.st_size}'
                live.add(key)
                item=disk_cache.get(key)
                if item: results.append(item)
                else: missing.append((path,key))

            cached_count=len(results)
            self.root.after(0,lambda:self.model_status_var.set(f'缓存命中 {cached_count}/{len(vpk_files)} · 剩余 {len(missing)} 个 VPK'))

            if missing:
                # 经验上 Windows 上反复 spawn Python 进程的启动成本很高；这里使用 mmap + I/O 并行线程，
                # 避免每次扫描都创建解释器，同时保持多个 VPK 并发读取。
                workers=min(len(missing), max(4,min(12,os.cpu_count() or 8)))
                done_count=0
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers,thread_name_prefix='RBVP-MODEL') as pool:
                    futures=[pool.submit(analyze_vpk_fast,path) for path,_ in missing]
                    for idx,(fut,(_,key)) in enumerate(zip(futures,missing),1):
                        try: item=fut.result()
                        except Exception: item=None
                        if item:
                            disk_cache[key]=item; results.append(item)
                        done_count=idx
                        if idx==len(missing) or idx%2==0:
                            self.root.after(0,lambda d=cached_count+done_count,t=len(vpk_files):self.model_status_var.set(f'分析中… {d}/{t}'))

            disk_cache={k:v for k,v in disk_cache.items() if k in live}
            self._save_model_disk_cache(addons_dir,disk_cache)
            self._model_result_cache=disk_cache

            results.sort(key=lambda x:(x['verts'],x['tris'],x['vpk'].lower()),reverse=True)
            self.vpk_detail_data={x['vpk']:x['models'] for x in results}
            all_mdl=[]; summary=[]; tv=tt=0
            for x in results:
                summary.append((x['vpk'],x['mdl_count'],x['verts'],x['tris']))
                tv+=x['verts']; tt+=x['tris']
                all_mdl.extend({'vpk':x['vpk'],**m} for m in x['models'])
            all_mdl.sort(key=lambda x:(x['tris'],x['verts'],x['mdl_path'].lower()),reverse=True)

            def publish():
                for tree in (self.summary_tree,self.detail_tree,self.compare_tree):
                    for iid in tree.get_children(): tree.delete(iid)
                for v,mc,verts,tris in summary:
                    self.summary_tree.insert('', 'end', values=(v,mc,f'{verts:,}',f'{tris:,}'))
                for i,x in enumerate(all_mdl,1):
                    self.compare_tree.insert('', 'end', values=(i,x['vpk'],x['mdl_path'],f'{x["verts"]:,}',f'{x["tris"]:,}'))
                self.model_count_var.set(f'{len(summary):,}'); self.model_vertex_var.set(f'{tv:,}'); self.model_tri_var.set(f'{tt:,}')
                self.model_status_var.set(f'完成 · {len(summary)} VPK / {len(all_mdl)} 模型 · {time.perf_counter()-overall_start:.2f}s')
                self.top_status_var.set('环境就绪')
                if summary:
                    first=self.summary_tree.get_children()[0]; self.summary_tree.selection_set(first); self.summary_tree.focus(first); self._on_vpk_summary_select()
            self.root.after(0,publish)

