import os
import threading
import subprocess
import concurrent.futures
import time
import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


from ui.widgets import AARoundedPanel, AARoundedButton, AARoundedEntry

class PagesMixin:
    def _build_conflict_page(self):
        page=self._new_page("conflict","Mod 冲突检测","除 addoninfo / addonimage 外，所有重复路径都会列出，并按覆盖数量降序显示。")
        body=tk.Frame(page,bg=self.colors["bg"]); body.grid(row=0,column=0,sticky="nsew",padx=18,pady=18); body.grid_rowconfigure(1,weight=1); body.grid_columnconfigure(0,weight=1)
        bar=tk.Frame(body,bg=self.colors["bg"]); bar.grid(row=0,column=0,sticky="ew",pady=(0,12)); bar.grid_columnconfigure(1,weight=1)
        AARoundedButton(bar,"开始扫描 Addons",self._start_conflict_detect,146,40,radius=10).grid(row=0,column=0,sticky="w")
        tk.Label(bar,textvariable=self.conflict_status_var,bg=self.colors["bg"],fg=self.colors["subtext"],font=("Microsoft YaHei UI",8)).grid(row=0,column=1,sticky="e")
        p,self.conflict_tree=self._build_table_v4(body,("filepath","conflict_vpks"),{"filepath":"冲突文件内部相对路径","conflict_vpks":"涉及冲突的 VPK 文件"},{"filepath":470,"conflict_vpks":820},[],[0.42,0.58]); p.grid(row=1,column=0,sticky="nsew")


    def _build_home_page(self):
        page=self._new_page("home","工具箱","集中管理 VPK 处理、模型统计和 Mod 诊断。")
        body=tk.Frame(page,bg=self.colors["bg"])
        body.grid(row=0,column=0,sticky="nsew",padx=14,pady=14)
        body.grid_columnconfigure(0,weight=1)
        body.grid_rowconfigure(4,weight=0)

        hero=self._hero_card(body,"RBVP 工作台","从这里进入最常用的模型分析、冲突扫描与 VPK 工作流。","打开模型检测",lambda:self._show_page("model_stats"))
        hero.grid(row=0,column=0,sticky="ew",pady=(0,2))
        hero.configure(height=132)

        tk.Label(body,text="核心工具",bg=self.colors["bg"],fg=self.colors["text"],font=("Microsoft YaHei UI",12,"bold")).grid(row=1,column=0,sticky="w",pady=(18,8))
        grid=tk.Frame(body,bg=self.colors["bg"])
        grid.grid(row=2,column=0,sticky="ew")
        # 6 等分网格：顶部三张卡各占 2 格；第二行两张卡各占 2 格并居中。
        # 这样新增“批量减面”后，核心工具区左右对称，不会在右侧留下突兀的大块空位。
        for i in range(6):
            grid.grid_columnconfigure(i,weight=1,uniform="core")
        self._home_tool_card(grid,0,0,"模型精度统计","统计 LOD0 顶点与三角面，并支持降序排行。","打开检测",lambda:self._show_page("model_stats"),self.colors["cyan"],span=2)
        self._home_tool_card(grid,0,2,"冲突检测","除 addoninfo / addonimage 外，所有重复路径都会参与检测。","开始扫描",lambda:self._show_page("conflict"),self.colors["warning"],span=2)
        self._home_tool_card(grid,0,4,"纹理组检测","遍历 Addons 中的 VPK，检测 MDL 是否有 $texturegroup。","开始检测",lambda:self._show_page("texturegroup"),self.colors["success"],span=2)
        self._home_tool_card(grid,1,1,"纹理组清除","对 Addons 中命中的 MDL 反编译、移除 QC 的 $texturegroup 后重新编译并覆盖。","开始清除",lambda:self._show_page("texturegroup_clear"),self.colors["danger"],span=2)
        self._home_tool_card(grid,1,3,"批量减面（仅限3DSMAX）","筛选 models 下 $staticprop 且面数 > 10 的 MDL，使用 3DSMAX ProOptimizer 批量减面。","打开工具",lambda:self._show_page("batch_reduce"),self.colors["cyan"],span=2)

        tk.Label(body,text="文件工具",bg=self.colors["bg"],fg=self.colors["text"],font=("Microsoft YaHei UI",12,"bold")).grid(row=3,column=0,sticky="w",pady=(18,8))
        util=tk.Frame(body,bg=self.colors["bg"])
        util.grid(row=4,column=0,sticky="ew")
        for i in range(4): util.grid_columnconfigure(i,weight=1,uniform="utility")

        utilities=[
            ("VPK 解包","批量解包","解包",lambda:self._show_page("unpack"),self.colors["cyan"],"⇩"),
            ("VPK 打包","生成 VPK","打包",lambda:self._show_page("pack"),self.colors["accent"],"⇧"),
            ("VMT 修改","追加参数","修改",lambda:self._show_page("vmt"),self.colors["success"],"≋"),
            ("VTFEdit","快速启动","启动",lambda:self._show_page("vtfedit"),self.colors["danger"],"▣"),
        ]
        for i,(title,desc,action,cmd,color,icon_text) in enumerate(utilities):
            card=AARoundedPanel(util,self.colors["soft"],radius=13,pad=6)
            card.grid(row=0,column=i,sticky="ew",padx=(0 if i==0 else 5,5 if i<3 else 0))
            inner=card.inner
            inner.grid_columnconfigure(1,weight=1)
            badge=tk.Canvas(inner,width=34,height=34,bg=self.colors["soft"],highlightthickness=0,bd=0)
            badge.grid(row=0,column=0,rowspan=2,padx=(3,8),pady=4)
            self._canvas_round_rect(badge,1,1,33,33,9,self.colors["accent_soft"])
            badge.create_text(17,17,text=icon_text,fill=color,font=("Segoe UI Symbol",12,"bold"))
            tk.Label(inner,text=title,bg=self.colors["soft"],fg=self.colors["text"],font=("Microsoft YaHei UI",9,"bold")).grid(row=0,column=1,sticky="w",pady=(4,0))
            tk.Label(inner,text=desc,bg=self.colors["soft"],fg=self.colors["muted"],font=("Microsoft YaHei UI",7)).grid(row=1,column=1,sticky="w",pady=(1,4))
            AARoundedButton(inner,action,cmd,62,28,radius=9,bg=color,hover_bg=self.colors["accent_hover"],font=("Microsoft YaHei UI",8,"bold")).grid(row=0,column=2,rowspan=2,padx=(6,2),pady=3)


    def _build_texturegroup_page(self):
        page=self._new_page(
            "texturegroup",
            "纹理组检测",
            "遍历 Addons 文件夹中的 VPK，检测 MDL 是否有 $texturegroup。"
        )
        body=tk.Frame(page,bg=self.colors["bg"])
        body.grid(row=0,column=0,sticky="nsew",padx=18,pady=14)
        body.grid_rowconfigure(1,weight=1)
        body.grid_columnconfigure(0,weight=1)

        # 顶部仅保留操作按钮与扫描状态；命中数量直接并入状态文本，避免重复统计。
        bar=tk.Frame(body,bg=self.colors["bg"],height=40)
        bar.grid(row=0,column=0,sticky="ew",pady=(0,10))
        bar.grid_propagate(False)
        bar.grid_columnconfigure(1,weight=1)

        AARoundedButton(
            bar,"开始检测 Addons",self._start_texturegroup_scan,146,40,
            radius=10,bg=self.colors["success"],hover_bg=self.colors["accent_hover"]
        ).grid(row=0,column=0,sticky="w")

        tk.Label(
            bar,textvariable=self.texturegroup_status_var,bg=self.colors["bg"],
            fg=self.colors["subtext"],font=("Microsoft YaHei UI",8)
        ).grid(row=0,column=1,sticky="w",padx=12)

        p,self.texturegroup_tree=self._build_table_v4(
            body,("vpk","mdl"),
            {"vpk":"VPK 文件","mdl":"MDL 相对路径"},
            {"vpk":420,"mdl":760},[],[0.40,0.60]
        )
        p.grid(row=1,column=0,sticky="nsew")




    def _build_texturegroup_clear_page(self):
        page=self._new_page(
            "texturegroup_clear",
            "纹理组清除",
            "遍历 Addons 文件夹中的实际 MDL，排除 models/v_models、models/w_models、models/weapons，使用 Crowbar 工具链反编译并清除 QC 中的 $texturegroup。"
        )
        body=tk.Frame(page,bg=self.colors["bg"])
        body.grid(row=0,column=0,sticky="nsew",padx=18,pady=14)
        body.grid_rowconfigure(1,weight=1)
        body.grid_columnconfigure(0,weight=1)
        bar=tk.Frame(body,bg=self.colors["bg"],height=40)
        bar.grid(row=0,column=0,sticky="ew",pady=(0,10))
        bar.grid_propagate(False)
        bar.grid_columnconfigure(1,weight=1)
        self.texturegroup_clear_action_btn=AARoundedButton(bar,"开始清除 $texturegroup",self._start_texturegroup_clear,166,40,radius=10,bg=self.colors["danger"],hover_bg=self.colors["accent_hover"])
        self.texturegroup_clear_action_btn.grid(row=0,column=0,sticky="w")
        tk.Label(bar,textvariable=self.texturegroup_clear_status_var,bg=self.colors["bg"],fg=self.colors["subtext"],font=("Microsoft YaHei UI",8)).grid(row=0,column=1,sticky="w",padx=12)
        p,self.texturegroup_clear_tree=self._build_table_v4(
            body,("status","mdl"),
            {"status":"状态","mdl":"MDL 相对路径"},
            {"status":100,"mdl":1100},["status"],[0.16,0.84]
        )
        p.grid(row=1,column=0,sticky="nsew")
        self.texturegroup_clear_log=self._create_log(body)
        self.texturegroup_clear_log.grid(row=2,column=0,sticky="ew",pady=(10,0))
        body.grid_rowconfigure(2,weight=0)

    def _build_batch_reduce_page(self):
        page=self._new_page(
            "batch_reduce",
            "批量减面（仅限3DSMAX）",
            "在解包文件夹中筛选models下的 $staticprop模型，并使用3DSMAX ProOptimizer批量减面。"
        )
        body=tk.Frame(page,bg=self.colors["bg"])
        body.grid(row=0,column=0,sticky="nsew",padx=18,pady=14)
        body.grid_columnconfigure(0,weight=4)
        body.grid_columnconfigure(1,weight=1)
        # 参数卡占满内容区横向空间；下方列表为主、控制台为辅。
        body.grid_rowconfigure(0,weight=0)
        body.grid_rowconfigure(1,weight=0,minsize=40)
        body.grid_rowconfigure(2,weight=7)
        body.grid_rowconfigure(3,weight=3,minsize=120)

        # 参数区：单行紧凑布局，横向占满内容区，避免初始分辨率下说明文字被截断。
        param=AARoundedPanel(body,self.colors["card"],radius=9,pad=3,height=66)
        param.grid(row=0,column=0,columnspan=2,sticky="ew",pady=(0,2))
        param.pack_propagate(False)
        inner=param.inner
        for c in range(10):
            inner.grid_columnconfigure(c,weight=0)
        inner.grid_columnconfigure(9,weight=1)

        tk.Label(
            inner,text="减面",bg=self.colors["card"],fg=self.colors["text"],
            font=("Microsoft YaHei UI",9,"bold")
        ).grid(row=0,column=0,sticky="w",padx=(0,8))

        tk.Label(
            inner,text="目标顶点保留比例",bg=self.colors["card"],fg=self.colors["subtext"],
            font=("Microsoft YaHei UI",8,"bold")
        ).grid(row=0,column=1,sticky="w",padx=(0,5))
        self.batch_reduce_ratio_var=tk.StringVar(value="50")
        AARoundedEntry(
            inner,value="50",textvariable=self.batch_reduce_ratio_var,width=54,
            radius=7,height=24,bg=self.colors["input"],border=self.colors["border"],
            focus_border=self.colors["accent"]
        ).grid(row=0,column=2,sticky="w")
        tk.Label(inner,text="%",bg=self.colors["card"],fg=self.colors["subtext"],
                 font=("Microsoft YaHei UI",8,"bold")).grid(row=0,column=3,sticky="w",padx=(4,9))

        self.batch_reduce_smooth_var=tk.BooleanVar(value=False)
        tk.Checkbutton(
            inner,text="启用 Smooth",variable=self.batch_reduce_smooth_var,
            bg=self.colors["card"],fg=self.colors["text"],
            activebackground=self.colors["card"],activeforeground=self.colors["text"],
            selectcolor=self.colors["input"],highlightthickness=0,bd=0,
            font=("Microsoft YaHei UI",8,"bold")
        ).grid(row=0,column=4,sticky="w",padx=(0,8))

        self.batch_reduce_smooth_auto_var=tk.BooleanVar(value=True)
        tk.Checkbutton(
            inner,text="自动平滑",variable=self.batch_reduce_smooth_auto_var,
            bg=self.colors["card"],fg=self.colors["text"],
            activebackground=self.colors["card"],activeforeground=self.colors["text"],
            selectcolor=self.colors["input"],highlightthickness=0,bd=0,
            font=("Microsoft YaHei UI",8,"bold")
        ).grid(row=0,column=5,sticky="w",padx=(0,8))

        tk.Label(
            inner,text="平滑角度阈值",bg=self.colors["card"],fg=self.colors["subtext"],
            font=("Microsoft YaHei UI",8,"bold")
        ).grid(row=0,column=6,sticky="w",padx=(0,5))
        self.batch_reduce_smooth_angle_var=tk.StringVar(value="45")
        AARoundedEntry(
            inner,value="45",textvariable=self.batch_reduce_smooth_angle_var,width=54,
            radius=7,height=24,bg=self.colors["input"],border=self.colors["border"],
            focus_border=self.colors["accent"]
        ).grid(row=0,column=7,sticky="w")
        tk.Label(inner,text="°",bg=self.colors["card"],fg=self.colors["subtext"],
                 font=("Microsoft YaHei UI",8,"bold")).grid(row=0,column=8,sticky="w",padx=(4,9))

        tk.Label(
            inner,
            text="说明：三角面最终比例可能会比你给的这个顶点比例大。可能会被电脑休眠影响，启动大批次任务时请注意检查休眠设置。",
            bg=self.colors["card"],fg=self.colors["muted"],
            font=("Microsoft YaHei UI",7),anchor="w",justify="left"
        ).grid(row=1,column=0,columnspan=10,sticky="w",padx=(0,0),pady=(2,0))

        smooth_note=tk.Label(
            inner,
            text="启用 Smooth 后才会添加 Smooth Modifier；自动平滑开启时才使用右侧角度阈值。",
            bg=self.colors["card"],fg=self.colors["muted"],
            font=("Microsoft YaHei UI",6),anchor="w",justify="left"
        )
        smooth_note.grid(row=0,column=9,sticky="w",padx=(8,0),pady=(0,0))

        bar=tk.Frame(body,bg=self.colors["bg"],height=40)
        bar.grid(row=1,column=0,columnspan=2,sticky="ew",pady=(0,8))
        bar.grid_propagate(False)
        bar.grid_columnconfigure(2,weight=1)
        self.batch_reduce_scan_btn=AARoundedButton(
            bar,"扫描符合条件的模型",self._start_batch_reduce_scan,160,40,
            radius=10,bg=self.colors["cyan"],hover_bg=self.colors["accent_hover"]
        )
        self.batch_reduce_scan_btn.grid(row=0,column=0,sticky="w",padx=(0,10))
        self.batch_reduce_start_btn=AARoundedButton(
            bar,"开始批量减面",self._start_batch_reduce,146,40,
            radius=10,bg=self.colors["success"],hover_bg=self.colors["accent_hover"]
        )
        self.batch_reduce_start_btn.grid(row=0,column=1,sticky="w",padx=(10,0))
        tk.Label(
            bar,textvariable=self.batch_reduce_status_var,bg=self.colors["bg"],
            fg=self.colors["subtext"],font=("Microsoft YaHei UI",8),anchor="w"
        ).grid(row=0,column=2,sticky="ew",padx=(14,0))

        content=tk.Frame(body,bg=self.colors["bg"])
        content.grid(row=2,column=0,columnspan=2,sticky="nsew")
        content.grid_rowconfigure(0,weight=1)
        content.grid_columnconfigure(0,weight=1)
        p,self.batch_reduce_tree=self._build_table_v4(
            content,
            ("status","triangles","mdl"),
            {"status":"状态","triangles":"LOD0 三角面","mdl":"MDL 相对路径"},
            {"status":110,"triangles":150,"mdl":1100},
            ["status","triangles"],[0.12,0.18,0.70]
        )
        p.grid(row=0,column=0,sticky="nsew")

        self.batch_reduce_log=self._create_log(body)
        self.batch_reduce_log.grid(row=3,column=0,columnspan=2,sticky="nsew",pady=(8,0))

    def _build_model_page(self):
        page=self._new_page("model_stats","Mod 模型精度统计","按 VPK 汇总模型总量；汇总/明细与排行均按三角面数优先降序。")
        body=tk.Frame(page,bg=self.colors["bg"])
        body.grid(row=0,column=0,sticky="nsew",padx=18,pady=14)
        body.grid_rowconfigure(3,weight=1)
        body.grid_columnconfigure(0,weight=1)

        bar=tk.Frame(body,bg=self.colors["bg"])
        bar.grid(row=0,column=0,sticky="ew",pady=(0,8))
        bar.grid_columnconfigure(1,weight=1)
        AARoundedButton(bar,"开始分析 Addons",self._analyze_models,146,40,radius=10).grid(row=0,column=0,sticky="w")
        tk.Label(bar,textvariable=self.model_status_var,bg=self.colors["bg"],fg=self.colors["subtext"],font=("Microsoft YaHei UI",8)).grid(row=0,column=1,sticky="w",padx=12)

        metrics=tk.Frame(body,bg=self.colors["bg"])
        metrics.grid(row=1,column=0,sticky="ew",pady=(0,8))
        for i in range(3): metrics.grid_columnconfigure(i,weight=1,uniform="metric")
        self._metric_card_v4(metrics,0,"VPK / MOD",self.model_count_var,"参与统计的资源包数量",self.colors["accent"])
        self._metric_card_v4(metrics,1,"LOD0 顶点",self.model_vertex_var,"所有模型顶点总量",self.colors["cyan"])
        self._metric_card_v4(metrics,2,"LOD0 三角形",self.model_tri_var,"所有模型三角面总量",self.colors["success"])

        tabbar=tk.Frame(body,bg=self.colors["bg"])
        tabbar.grid(row=2,column=0,sticky="w",pady=(0,6))
        host=tk.Frame(body,bg=self.colors["bg"])
        host.grid(row=3,column=0,sticky="nsew")
        host.grid_rowconfigure(0,weight=1)
        host.grid_columnconfigure(0,weight=1)

        summary=tk.Frame(host,bg=self.colors["bg"])
        summary.grid(row=0,column=0,sticky="nsew")
        summary.grid_rowconfigure(0,weight=1)
        summary.grid_rowconfigure(1,weight=1)
        summary.grid_columnconfigure(0,weight=1)
        rank=tk.Frame(host,bg=self.colors["bg"])
        rank.grid(row=0,column=0,sticky="nsew")
        rank.grid_rowconfigure(0,weight=1)
        rank.grid_columnconfigure(0,weight=1)

        def set_tab(name):
            (summary if name=="summary" else rank).tkraise()
            for k,b in self.model_tabs.items():
                b._bg=self.colors["accent"] if k==name else self.colors["soft"]
                b._hover=self.colors["accent_hover"] if k==name else self.colors["card2"]
                b._draw(b._bg)
        self.model_tabs={}
        for key,text in (("summary","汇总 / 明细"),("rank","模型排行")):
            b=AARoundedButton(tabbar,text,lambda k=key:set_tab(k),126 if key=="summary" else 116,34,radius=9,bg=self.colors["accent"] if key=="summary" else self.colors["soft"],hover_bg=self.colors["accent_hover"] if key=="summary" else self.colors["card2"],fg=self.colors["text"],outline=self.colors["border"] if key!="summary" else None)
            b.pack(side="left",padx=(0,6)); self.model_tabs[key]=b

        p,self.summary_tree=self._build_table_v4(summary,("vpk","mdl_count","vpk_verts","vpk_tris"),
            {"vpk":"VPK 文件名","mdl_count":"MDL 模型数","vpk_verts":"总顶点数","vpk_tris":"总三角面数"},
            {"vpk":420,"mdl_count":110,"vpk_verts":130,"vpk_tris":140},["mdl_count","vpk_verts","vpk_tris"],[0.50,0.16,0.17,0.17])
        p.grid(row=0,column=0,sticky="nsew",pady=(0,4))
        self.summary_tree.bind("<<TreeviewSelect>>",self._on_vpk_summary_select)

        dp=AARoundedPanel(summary,self.colors["card"],radius=14,pad=8)
        dp.grid(row=1,column=0,sticky="nsew",pady=(4,0))
        dp.inner.grid_rowconfigure(1,weight=1)
        dp.inner.grid_columnconfigure(0,weight=1)
        tk.Label(dp.inner,text="当前 VPK 内部模型 · 三角面数降序",bg=self.colors["card"],fg=self.colors["subtext"],font=("Microsoft YaHei UI",8,"bold")).grid(row=0,column=0,sticky="w",padx=4,pady=(1,4))
        dd,self.detail_tree=self._build_table_v4(dp.inner,("mdl_path","verts","tris"),{"mdl_path":"模型内部相对路径","verts":"顶点数","tris":"三角面数"},{"mdl_path":540,"verts":120,"tris":120},["verts","tris"],[0.64,0.18,0.18])
        dd.grid(row=1,column=0,sticky="nsew")

        rp,self.compare_tree=self._build_table_v4(rank,("rank","vpk","mdl_path","verts","tris"),{"rank":"排名","vpk":"所属 VPK","mdl_path":"MDL 相对路径","verts":"顶点数量","tris":"三角面数量"},{"rank":66,"vpk":230,"mdl_path":420,"verts":110,"tris":120},["rank","verts","tris"],[0.07,0.20,0.45,0.14,0.14])
        rp.grid(row=0,column=0,sticky="nsew")
        set_tab("summary")


    def _build_operation_page(self,page_key,title,desc,button_text,command,extra_text=None,extra_command=None):
        page=self._new_page(page_key,title,desc)
        body=tk.Frame(page,bg=self.colors["bg"]); body.grid(row=0,column=0,sticky="nsew",padx=18,pady=18); body.grid_rowconfigure(1,weight=1); body.grid_columnconfigure(0,weight=1)
        bar=tk.Frame(body,bg=self.colors["bg"]); bar.grid(row=0,column=0,sticky="ew",pady=(0,12)); bar.grid_columnconfigure(2,weight=1)
        AARoundedButton(bar,button_text,command,144,40,radius=10).grid(row=0,column=0,sticky="w")
        col=1
        if extra_text:
            AARoundedButton(bar,extra_text,extra_command,118,40,radius=10,bg=self.colors["soft"],hover_bg=self.colors["card2"],fg=self.colors["text"],outline=self.colors["border"]).grid(row=0,column=col,padx=(8,0)); col+=1
        status = getattr(self, "unpack_log", None) if page_key=="unpack" else getattr(self,"pack_log",None)
        log=self._create_log(body); log.grid(row=1,column=0,sticky="nsew")
        if page_key=="unpack": self.unpack_log=log
        elif page_key=="pack": self.pack_log=log
        return log


    def _build_pack_page(self):
        self._build_operation_page("pack","VPK 打包","选择一个目录生成 VPK，并自动输出到同级 temp 目录。","选择并打包",self._start_pack_vpk)


    def _build_settings_page(self):
        page=self._new_page("settings","设置","配置 VPK.exe、VTFEdit、Crowbar 和 Addons 路径；其它页面自动复用这些设置。")
        body=tk.Frame(page,bg=self.colors["bg"]); body.grid(row=0,column=0,sticky="nsew",padx=14,pady=12); body.grid_columnconfigure(0,weight=1)
        panel=AARoundedPanel(body,self.colors["card"],radius=16,pad=18); panel.pack(fill="x")
        inner=panel.inner; inner.grid_columnconfigure(1,weight=1)
        rows=[("VPK.exe",self.vpk_exe_path,"exe",[("Executable","vpk.exe")]),("VTFEdit",self.vtfedit_exe_path,"exe",[("Executable","*.exe")]),("Crowbar",self.crowbar_exe_path,"exe",[("Executable","Crowbar*.exe")]),("NekoMDL",self.nekomdl_exe_path,"exe",[("Executable","nekomdl.exe")]),("Addons",self.addons_dir_path,"dir",None),("3DSMAX",self.threedsmax_exe_path,"exe",[("Executable","3dsmax.exe")]) ]
        for r,(label,var,kind,ftypes) in enumerate(rows):
            tk.Label(inner,text=label,bg=self.colors["card"],fg=self.colors["subtext"],font=("Microsoft YaHei UI",9,"bold")).grid(row=r,column=0,sticky="w",padx=(0,12),pady=7)
            entry=AARoundedEntry(inner,textvariable=var,radius=10,height=40,bg=self.colors["input"],border=self.colors["border"],focus_border=self.colors["accent"])
            entry.grid(row=r,column=1,sticky="ew",pady=7)
            cmd=(lambda v=var,t=label,ft=ftypes:self._browse_file(v,t,ft)) if kind in ("exe","file") else (lambda v=var:self._browse_folder(v))
            AARoundedButton(inner,"浏览",cmd,78,34,radius=9,bg=self.colors["soft"],hover_bg=self.colors["card2"],fg=self.colors["text"],outline=self.colors["border"]).grid(row=r,column=2,padx=(10,0),pady=7)
        tk.Label(inner,text="纹理组清除使用 Crowbar / NekoMDL；批量减面使用 3DSMAX ProOptimizer。工具路径支持自动搜索，也可手动指定。",bg=self.colors["card"],fg=self.colors["muted"],font=("Microsoft YaHei UI",7)).grid(row=len(rows),column=0,columnspan=3,sticky="w",pady=(2,6))


    def _build_table_v4(self,parent,columns,headings,widths,center_cols,ratios):
        panel=AARoundedPanel(parent,self.colors["card"],radius=14,pad=10)
        inner=panel.inner; inner.grid_rowconfigure(0,weight=1); inner.grid_columnconfigure(0,weight=1)
        tree=ttk.Treeview(inner,columns=columns,show="headings",style="RBVP.Treeview")
        vs=ttk.Scrollbar(inner,orient="vertical",command=tree.yview,style="RBVP.Vertical.TScrollbar")
        hs=ttk.Scrollbar(inner,orient="horizontal",command=tree.xview,style="RBVP.Horizontal.TScrollbar")
        tree.configure(yscrollcommand=vs.set,xscrollcommand=hs.set)
        tree.grid(row=0,column=0,sticky="nsew"); vs.grid(row=0,column=1,sticky="ns"); hs.grid(row=1,column=0,sticky="ew")
        cset=set(center_cols)
        mins=[int(widths.get(c,110)) for c in columns]
        def resize_columns(_e=None):
            total=max(100,tree.winfo_width()-18)
            min_total=sum(mins)
            if total>=min_total:
                extra=total-min_total
                for c,m,r in zip(columns,mins,ratios): tree.column(c,width=max(m,int(m+extra*r)),anchor="center" if c in cset else "w",stretch=False)
            else:
                for c,m in zip(columns,mins): tree.column(c,width=m,anchor="center" if c in cset else "w",stretch=False)
        for c in columns: tree.heading(c,text=headings[c]); tree.column(c,width=mins[columns.index(c)],anchor="center" if c in cset else "w",stretch=False)
        tree.bind("<Configure>",resize_columns)
        tree.after_idle(resize_columns)
        return panel,tree


    def _build_unpack_page(self):
        self._build_operation_page("unpack","VPK 解包","批量调用本机 VPK.exe 解包 Mod 资源。","选择 VPK 文件",self._start_unpack_vpk,"设置 VPK.exe",lambda:self._show_page("settings"))


    def _build_vmt_page(self):
        page=self._new_page("vmt","VMT 批量修改","按 VPK 打包模块的 Mod 判定规则，批量扫描多个 Mod 的 materials 并修改 VMT。")
        body=tk.Frame(page,bg=self.colors["bg"])
        body.grid(row=0,column=0,sticky="nsew",padx=18,pady=18)
        body.grid_rowconfigure(1,weight=1); body.grid_columnconfigure(0,weight=1)
        panel=AARoundedPanel(body,self.colors["card"],radius=16,pad=16)
        panel.grid(row=0,column=0,sticky="ew",pady=(0,12)); panel.inner.grid_columnconfigure(0,weight=1)
        tk.Label(panel.inner,text="追加参数",bg=self.colors["card"],fg=self.colors["text"],font=("Microsoft YaHei UI",9,"bold")).grid(row=0,column=0,sticky="w")
        tk.Label(panel.inner,text="选择一个父目录后，RBVP 自动识别其中的 Mod。判定条件：addoninfo.txt、materials、models、scripts、particles、sound 至少存在一个。",bg=self.colors["card"],fg=self.colors["muted"],font=("Microsoft YaHei UI",8),wraplength=1050,justify="left").grid(row=1,column=0,sticky="w",pady=(3,10))
        row=tk.Frame(panel.inner,bg=self.colors["card"]); row.grid(row=2,column=0,sticky="ew"); row.grid_columnconfigure(0,weight=1)
        self.vmt_text_entry=AARoundedEntry(row,'"$nodecal" "1"',radius=11,height=46,bg=self.colors["input"],border=self.colors["border"],focus_border=self.colors["accent"])
        self.vmt_text_entry.grid(row=0,column=0,sticky="ew")
        AARoundedButton(row,"选择 Mod 父目录",self._start_modify_vmt,150,46,radius=12,bg=self.colors["soft"],hover_bg=self.colors["card2"],fg=self.colors["text"],outline=self.colors["border"]).grid(row=0,column=1,padx=(12,0))
        self.vmt_folder_status=tk.StringVar(value="尚未选择 Mod 父目录")
        tk.Label(panel.inner,textvariable=self.vmt_folder_status,bg=self.colors["card"],fg=self.colors["subtext"],font=("Microsoft YaHei UI",8)).grid(row=3,column=0,sticky="w",pady=(8,0))
        tk.Label(panel.inner,text='示例：  "$nodecal" "1"',bg=self.colors["card"],fg=self.colors["muted"],font=("Consolas",8)).grid(row=4,column=0,sticky="w",pady=(4,0))
        self.vmt_selected_folders=[]
        self.vmt_log=self._create_log(body); self.vmt_log.grid(row=1,column=0,sticky="nsew")


    def _build_vtfedit_page(self):
        page=self._new_page("vtfedit","VTFEdit","快速启动当前配置中的 VTFEdit。")
        body=tk.Frame(page,bg=self.colors["bg"]); body.grid(row=0,column=0,sticky="nsew",padx=18,pady=18); body.grid_columnconfigure(0,weight=1)
        panel=AARoundedPanel(body,self.colors["card"],radius=16,pad=18); panel.pack(fill="x")
        panel.inner.grid_columnconfigure(0,weight=1)
        tk.Label(panel.inner,text="当前程序路径",bg=self.colors["card"],fg=self.colors["subtext"],font=("Microsoft YaHei UI",8,"bold")).grid(row=0,column=0,sticky="w")
        tk.Label(panel.inner,textvariable=self.vtfedit_exe_path,bg=self.colors["card"],fg=self.colors["text"],font=("Consolas",9),anchor="w",justify="left",wraplength=900).grid(row=1,column=0,sticky="ew",pady=(5,14))
        row=tk.Frame(panel.inner,bg=self.colors["card"]); row.grid(row=2,column=0,sticky="w")
        AARoundedButton(row,"立即启动 VTFEdit",self._launch_vtfedit,152,40,radius=10).pack(side="left")
        AARoundedButton(row,"更改路径",lambda:self._show_page("settings"),104,40,radius=10,bg=self.colors["soft"],hover_bg=self.colors["card2"],fg=self.colors["text"],outline=self.colors["border"]).pack(side="left",padx=8)


    def _metric_card_v4(self,parent,col,title,var,desc,accent):
        p=AARoundedPanel(parent,self.colors["card"],radius=14,pad=15); p.grid(row=0,column=col,sticky="ew",padx=(0 if col==0 else 6,6 if col<2 else 0)); p.configure(height=118)
        tk.Label(p.inner,text=title,bg=self.colors["card"],fg=self.colors["subtext"],font=("Microsoft YaHei UI",8,"bold")).pack(anchor="w")
        tk.Label(p.inner,textvariable=var,bg=self.colors["card"],fg=self.colors["text"],font=("Segoe UI",19,"bold")).pack(anchor="w",pady=(6,0))
        tk.Label(p.inner,text=desc,bg=self.colors["card"],fg=accent,font=("Microsoft YaHei UI",8)).pack(anchor="w",pady=(2,0))

