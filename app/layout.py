import os
import threading
import subprocess
import concurrent.futures
import time
import json
import tkinter as tk
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, ttk


from ui.widgets import (
    AARoundedSurface, AARoundedPanel, AARoundedButton,
    _ROUND_IMAGE_CACHE, _ROUND_IMAGE_CACHE_MAX, _AA_SCALE, _aa_cached_image,
)

class LayoutMixin:
    def _add_nav_button(self,parent,key,label,kind,color):
        w=226; h=42
        canvas = tk.Canvas(parent,width=w,height=h,bg=self.colors["rail"],bd=0,highlightthickness=0,cursor="hand2")
        canvas.pack(fill="x",padx=0,pady=3)
        def render(active=False,hover=False):
            canvas.delete("all")
            if active:
                self._nav_bg_image = getattr(self,"_nav_bg_image",{})
                cache_key = ("nav", w, h, 10, self.colors["rail_active"])
                photo = _ROUND_IMAGE_CACHE.get(cache_key)
                if photo is None:
                    photo = _aa_cached_image(w, h, 10, self.colors["rail_active"], scale=_AA_SCALE)
                    _ROUND_IMAGE_CACHE[cache_key] = photo
                    while len(_ROUND_IMAGE_CACHE) > _ROUND_IMAGE_CACHE_MAX:
                        _ROUND_IMAGE_CACHE.popitem(last=False)
                else:
                    _ROUND_IMAGE_CACHE.move_to_end(cache_key)
                self._nav_bg_image[key]=photo
                canvas.create_image(0,0,image=photo,anchor="nw")
            self._draw_icon(canvas,kind,color,active)
            canvas.create_text(43,21,text=label,anchor="w",fill=self.colors["text"] if active else self.colors["subtext"],font=("Microsoft YaHei UI",9,"bold"))
        render(False)
        canvas.bind("<Enter>",lambda _e: self._paint_nav(key,True))
        canvas.bind("<Leave>",lambda _e: self._paint_nav(key,False))
        canvas.bind("<Button-1>",lambda _e: self._show_page(key))
        self.nav_buttons[key]=(canvas,kind,color,render)


    def _build_pages(self):
        # 延迟构建页面：启动阶段只创建 Shell / 导航，避免一次性实例化全部圆角 Canvas、Treeview、日志控件。
        self.page_specs = {
            "home": self._build_home_page,
            "model_stats": self._build_model_page,
            "conflict": self._build_conflict_page,
            "texturegroup": self._build_texturegroup_page,
            "texturegroup_clear": self._build_texturegroup_clear_page,
            "batch_reduce": self._build_batch_reduce_page,
            "unpack": self._build_unpack_page,
            "pack": self._build_pack_page,
            "vmt": self._build_vmt_page,
            "vtfedit": self._build_vtfedit_page,
            "settings": self._build_settings_page,
        }


    def _build_shell(self):
        self.sidebar = tk.Frame(self.root, bg=self.colors["rail"], width=244)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.grid_propagate(False)
        self.content_shell = tk.Frame(self.root, bg=self.colors["bg"])
        self.content_shell.grid(row=0, column=1, sticky="nsew")
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=1)
        self.content_shell.grid_rowconfigure(1, weight=1)
        self.content_shell.grid_columnconfigure(0, weight=1)

        self._build_sidebar()
        self._build_topbar()
        self.content_host = tk.Frame(self.content_shell, bg=self.colors["bg"])
        self.content_host.grid(row=1, column=0, sticky="nsew", padx=(18,24), pady=(0,18))
        self.content_host.grid_rowconfigure(0, weight=1)
        self.content_host.grid_columnconfigure(0, weight=1)


    def _build_sidebar(self):
        brand = tk.Frame(self.sidebar, bg=self.colors["rail"], height=88)
        brand.pack(fill="x")
        brand.pack_propagate(False)
        mark = AARoundedSurface(brand, self.colors["accent_soft"], radius=12, pad=0, width=40, height=40)
        mark.pack(side="left", padx=(18,10), pady=22)
        mark.create_text(20,20,text="R",fill=self.colors["accent"],font=("Segoe UI",17,"bold"))
        tk.Label(brand,text="RBVP",bg=self.colors["rail"],fg=self.colors["text"],font=("Segoe UI",15,"bold")).pack(side="left",pady=18)

        nav_wrap = tk.Frame(self.sidebar, bg=self.colors["rail"])
        nav_wrap.pack(fill="both", expand=True, padx=9, pady=(10,0))
        navs = [
            ("home","总览","home",self.colors["accent"]),
            ("model_stats","模型精度统计","model",self.colors["cyan"]),
            ("conflict","冲突检测","conflict",self.colors["warning"]),
            ("texturegroup","纹理组检测","texturegroup",self.colors["success"]),
            ("texturegroup_clear","纹理组清除","clear",self.colors["danger"]),
            ("batch_reduce","批量减面（仅限3DSMAX）","reduce",self.colors["cyan"]),
            ("unpack","VPK 解包","down",self.colors["cyan"]),
            ("pack","VPK 打包","up",self.colors["accent"]),
            ("vmt","VMT 修改","lines",self.colors["success"]),
            ("vtfedit","VTFEdit","doc",self.colors["danger"]),
        ]
        for key,label,kind,color in navs:
            self._add_nav_button(nav_wrap,key,label,kind,color)
        tk.Frame(nav_wrap,bg=self.colors["border"],height=1).pack(fill="x",padx=12,pady=(14,12))
        self._add_nav_button(nav_wrap,"settings","设置","gear",self.colors["accent"])

        footer = tk.Frame(self.sidebar,bg=self.colors["rail"],height=68)
        footer.pack(side="bottom",fill="x")
        footer.pack_propagate(False)
        tk.Label(footer,text="RBVP",bg=self.colors["rail"],fg=self.colors["accent"],font=("Segoe UI",8,"bold")).pack(anchor="w",padx=20,pady=(5,0))
        tk.Label(footer,text="Rick's Batch VPK Processing",bg=self.colors["rail"],fg=self.colors["muted"],font=("Segoe UI",6)).pack(anchor="w",padx=20,pady=(2,0))


    def _build_topbar(self):
        top = tk.Frame(self.content_shell, bg=self.colors["bg"], height=70)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_propagate(False)
        top.grid_columnconfigure(0, weight=1)
        left = tk.Frame(top, bg=self.colors["bg"])
        left.grid(row=0, column=0, sticky="sw", padx=(28,0), pady=(0,16))
        self.top_title_var = tk.StringVar(value="工具箱")
        self.top_subtitle_var = tk.StringVar(value="集中管理 VPK 处理、模型统计和 Mod 诊断。")
        tk.Label(left, textvariable=self.top_title_var, bg=self.colors["bg"], fg=self.colors["text"],
                 font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        tk.Label(left, textvariable=self.top_subtitle_var, bg=self.colors["bg"], fg=self.colors["subtext"],
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(3,0))
        right = tk.Frame(top, bg=self.colors["bg"])
        right.grid(row=0, column=1, sticky="se", padx=(0,24), pady=(0,16))
        self._make_status_pill(right)


    def _canvas_round_rect(self, canvas, x1, y1, x2, y2, r, fill, outline="", tag=None):
        """Canvas 兼容圆角绘制；仅用于小型图标/装饰，沿用统一无接缝绘制方案。"""
        r = max(2, min(int(r), int((x2 - x1) / 2), int((y2 - y1) / 2)))
        tags = () if tag is None else (tag,)
        items = []
        items.append(canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=fill, tags=tags))
        items.append(canvas.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline=fill, tags=tags))
        items.append(canvas.create_oval(x1, y1, x1 + 2*r, y1 + 2*r, fill=fill, outline=fill, tags=tags))
        items.append(canvas.create_oval(x2 - 2*r, y1, x2, y1 + 2*r, fill=fill, outline=fill, tags=tags))
        items.append(canvas.create_oval(x1, y2 - 2*r, x1 + 2*r, y2, fill=fill, outline=fill, tags=tags))
        items.append(canvas.create_oval(x2 - 2*r, y2 - 2*r, x2, y2, fill=fill, outline=fill, tags=tags))
        return items


    def _draw_icon(self, canvas, kind, color, active=False):
        x,y=18,22; c=color
        if kind=="home":
            canvas.create_polygon(x-8,y-1,x,y-8,x+8,y-1,fill="",outline=c,width=1.7)
            canvas.create_rectangle(x-6,y-1,x+6,y+8,fill="",outline=c,width=1.7)
            canvas.create_rectangle(x-2,y+3,x+2,y+8,fill=c,outline=c)
        elif kind=="model":
            canvas.create_polygon(x,y-9,x+8,y,x,y+9,x-8,y,fill="",outline=c,width=1.7)
            canvas.create_line(x-4,y,x+4,y,fill=c,width=1.4)
        elif kind=="conflict":
            canvas.create_polygon(x,y-9,x+8,y+7,x-8,y+7,fill="",outline=c,width=1.7)
            canvas.create_line(x,y-4,x,y+2,fill=c,width=1.6)
            canvas.create_oval(x-1.1,y+4,x+1.1,y+6.2,fill=c,outline=c)
        elif kind=="clear":
            canvas.create_line(x-6,y,x+6,y,fill=c,width=1.8)
            canvas.create_line(x,y-6,x,y+6,fill=c,width=1.8)
            canvas.create_line(x-4,y-4,x+4,y+4,fill=c,width=1.5)
            canvas.create_line(x-4,y+4,x+4,y-4,fill=c,width=1.5)
        elif kind=="down":
            canvas.create_line(x,y-8,x,y+6,fill=c,width=1.8)
            canvas.create_line(x-5,y+1,x,y+6,fill=c,width=1.8); canvas.create_line(x+5,y+1,x,y+6,fill=c,width=1.8)
        elif kind=="up":
            canvas.create_line(x,y+8,x,y-6,fill=c,width=1.8)
            canvas.create_line(x-5,y-1,x,y-6,fill=c,width=1.8); canvas.create_line(x+5,y-1,x,y-6,fill=c,width=1.8)
        elif kind=="texturegroup":
            canvas.create_rectangle(x-8,y-6,x+8,y+6,fill="",outline=c,width=1.5)
            canvas.create_line(x-5,y-2,x+5,y-2,fill=c,width=1.2)
            canvas.create_line(x-5,y+2,x+3,y+2,fill=c,width=1.2)
            canvas.create_oval(x+4,y+4,x+7,y+7,fill=c,outline=c)
        elif kind=="lines":
            canvas.create_line(x-7,y-6,x+7,y-3,fill=c,width=1.6); canvas.create_line(x-7,y-1,x+7,y+2,fill=c,width=1.6); canvas.create_line(x-7,y+4,x+7,y+7,fill=c,width=1.6)
        elif kind=="doc":
            canvas.create_rectangle(x-7,y-9,x+7,y+9,fill="",outline=c,width=1.6)
            canvas.create_line(x-4,y-4,x+4,y-4,fill=c,width=1.3); canvas.create_line(x-4,y,x+4,y,fill=c,width=1.3); canvas.create_line(x-4,y+4,x+2,y+4,fill=c,width=1.3)
        elif kind=="reduce":
            canvas.create_polygon(x-8,y+6,x-2,y+1,x-5,y-5,x+1,y-9,x+8,y-3,x+3,y+3,x+8,y+8,fill="",outline=c,width=1.5)
            canvas.create_line(x-3,y+3,x+4,y-4,fill=c,width=1.5)
        elif kind=="gear":
            canvas.create_oval(x-6,y-6,x+6,y+6,fill="",outline=c,width=1.5)
            canvas.create_oval(x-2,y-2,x+2,y+2,fill=c,outline=c)
            for dx,dy in ((0,-9),(0,9),(-9,0),(9,0),(-6,-6),(6,6),(-6,6),(6,-6)):
                canvas.create_line(x+dx*0.72,y+dy*0.72,x+dx,y+dy,fill=c,width=1.2)


    def _ensure_page(self,key):
        if key in self.pages:
            return True
        builder=self.page_specs.get(key)
        if not builder:
            return False
        builder()
        return key in self.pages


    def _hero_card(self,parent,title,desc,action_text,command,accent=None):
        panel=AARoundedPanel(parent,self.colors["card"],radius=16,pad=18)
        inner=panel.inner
        inner.grid_columnconfigure(0,weight=1)
        tk.Label(inner,text=title,bg=self.colors["card"],fg=self.colors["text"],font=("Microsoft YaHei UI",15,"bold")).grid(row=0,column=0,sticky="w")
        tk.Label(inner,text=desc,bg=self.colors["card"],fg=self.colors["subtext"],font=("Microsoft YaHei UI",9)).grid(row=1,column=0,sticky="w",pady=(5,0))
        AARoundedButton(inner,action_text,command,142,40,radius=11).grid(row=0,column=1,rowspan=2,sticky="e",padx=(20,0))
        return panel


    def _home_tool_card(self,parent,row,col,title,desc,action,cmd,accent,span=1):
        p=AARoundedPanel(parent,self.colors["card"],radius=14,pad=16)
        right = 6 if col + span < 6 else 0
        left = 6 if col > 0 else 0
        p.grid(row=row,column=col,columnspan=span,sticky="ew",padx=(left,right),pady=(0,6))
        p.configure(height=166)
        inner=p.inner; inner.grid_columnconfigure(0,weight=1)
        tk.Label(inner,text=title,bg=self.colors["card"],fg=self.colors["text"],font=("Microsoft YaHei UI",11,"bold")).grid(row=0,column=0,sticky="w")
        tk.Label(inner,text=desc,bg=self.colors["card"],fg=self.colors["subtext"],font=("Microsoft YaHei UI",8),wraplength=280,justify="left").grid(row=1,column=0,sticky="w",pady=(6,12))
        AARoundedButton(inner,action,cmd,100,34,radius=9,bg=accent,hover_bg=self.colors["accent_hover"]).grid(row=2,column=0,sticky="w")


    def _make_status_pill(self, parent):
        # 右上角全局状态胶囊：任何模块写入 top_status_var 时都保证单行可见，
        # 优先动态缩放字体，其次再做省略，绝不让文字越过 Canvas 边界。
        base_family = "Microsoft YaHei UI"
        base_size = 8
        min_size = 6
        min_width, hard_max_width = 150, 520
        text_x, right_pad = 27, 14

        pill = AARoundedSurface(
            parent, self.colors["soft"], radius=11, pad=0,
            width=min_width, height=32
        )
        pill.pack(anchor="e")
        text_id = pill.create_text(
            text_x, 16, text="", anchor="w", fill=self.colors["subtext"],
            font=(base_family, base_size, "bold"), tags="status_fg"
        )
        dot_id = pill.create_oval(
            13, 14, 17, 18, fill=self.colors["success"],
            outline=self.colors["success"], tags="status_fg"
        )

        def _available_width():
            # 右侧状态栏的最大安全宽度取当前窗口实际可用空间；
            # 太窄时缩到最小值，避免挤压左侧标题区域。
            try:
                root_w = max(1, self.root.winfo_width())
                sidebar_w = max(0, self.sidebar.winfo_width())
                safe = root_w - sidebar_w - 64
            except Exception:
                safe = hard_max_width
            return max(min_width, min(hard_max_width, safe))

        def _fit_text(raw, width):
            # 逐级降低字体，确保完整文本优先可见。
            for size in range(base_size, min_size - 1, -1):
                fnt = tkfont.Font(family=base_family, size=size, weight="bold")
                if fnt.measure(raw) <= width:
                    return raw, fnt

            # 连最小字号仍放不下时才省略；省略字符串本身也必须经过测量。
            fnt = tkfont.Font(family=base_family, size=min_size, weight="bold")
            ellipsis = "…"
            if fnt.measure(ellipsis) > width:
                return ellipsis, fnt
            lo, hi = 0, len(raw)
            best = ""
            while lo <= hi:
                mid = (lo + hi) // 2
                candidate = raw[:mid].rstrip() + ellipsis if mid else ellipsis
                if fnt.measure(candidate) <= width:
                    best = candidate
                    lo = mid + 1
                else:
                    hi = mid - 1
            return best or ellipsis, fnt

        def refresh_status(*_):
            try:
                raw = (self.top_status_var.get() or "").replace("\n", " ").strip()
                max_width = _available_width()

                # 胶囊内文字真正允许的像素宽度，预留左侧圆点与右边安全边距。
                text_avail = max(30, max_width - text_x - right_pad)
                display, font_obj = _fit_text(raw, text_avail)
                text_w = font_obj.measure(display)

                # 不让实际 Canvas 宽度超过当前窗口可用区域。
                width = max(min_width, min(max_width, text_x + text_w + right_pad))
                pill.configure(width=width)
                pill.itemconfigure(text_id, text=display, font=font_obj)
                pill.coords(text_id, text_x, 16)
                pill.coords(dot_id, 13, 14, 17, 18)
                pill.tag_raise("status_fg")
            except tk.TclError:
                pass

        def refresh_on_window_resize(_event=None):
            # 尺寸变化后重新计算，修复最大化/还原窗口时可能出现的旧宽度。
            try:
                self.root.after_idle(refresh_status)
            except tk.TclError:
                pass

        self.top_status_var.trace_add("write", refresh_status)
        self.root.bind("<Configure>", refresh_on_window_resize, add="+")
        refresh_status()


    def _new_page(self,key,title,subtitle):
        page=tk.Frame(self.content_host,bg=self.colors["bg"])
        page.grid(row=0,column=0,sticky="nsew")
        page.grid_rowconfigure(0,weight=1); page.grid_columnconfigure(0,weight=1)
        self.pages[key]=page; self.page_titles[key]=(title,subtitle)
        return page


    def _paint_nav(self,key,hover=False):
        if key not in self.nav_buttons:return
        canvas,kind,color,_=self.nav_buttons[key]
        active=self.current_page==key
        canvas.delete("all")
        w,h=226,42
        if active or hover:
            fill=self.colors["rail_active"] if active else self.colors["soft"]
            if not hasattr(self,"_nav_bg_image"): self._nav_bg_image={}
            cache_key = ("nav", w, h, 10, fill)
            photo = _ROUND_IMAGE_CACHE.get(cache_key)
            if photo is None:
                photo = _aa_cached_image(w, h, 10, fill, scale=_AA_SCALE)
                _ROUND_IMAGE_CACHE[cache_key] = photo
                while len(_ROUND_IMAGE_CACHE) > _ROUND_IMAGE_CACHE_MAX:
                    _ROUND_IMAGE_CACHE.popitem(last=False)
            else:
                _ROUND_IMAGE_CACHE.move_to_end(cache_key)
            self._nav_bg_image[key]=photo
            canvas.create_image(0,0,image=photo,anchor="nw")
        self._draw_icon(canvas,kind,color,active)
        label = next((x[1] for x in [("home","总览"),("model_stats","模型精度统计"),("conflict","冲突检测"),("texturegroup","纹理组检测"),("texturegroup_clear","纹理组清除"),("batch_reduce","批量减面（仅限3DSMAX）"),("unpack","VPK 解包"),("pack","VPK 打包"),("vmt","VMT 修改"),("vtfedit","VTFEdit"),("settings","设置")] if x[0]==key), key)
        canvas.create_text(43,21,text=label,anchor="w",fill=self.colors["text"] if active else self.colors["subtext"],font=("Microsoft YaHei UI",9,"bold"))


    def _section_toolbar(self,parent):
        bar=tk.Frame(parent,bg=self.colors["bg"])
        bar.grid(row=0,column=0,sticky="ew",pady=(0,12))
        bar.grid_columnconfigure(1,weight=1)
        return bar


    def _setup_styles(self):
        style = ttk.Style(self.root)
        try: style.theme_use("clam")
        except tk.TclError: pass
        style.configure("Modern.TEntry", fieldbackground=self.colors["input"], foreground=self.colors["text"],
                        insertcolor=self.colors["text"], bordercolor=self.colors["border"],
                        lightcolor=self.colors["accent"], darkcolor=self.colors["border"],
                        padding=10, font=("Consolas", 9))
        style.configure("RBVP.Treeview", background=self.colors["card"], fieldbackground=self.colors["card"],
                        foreground=self.colors["text"], rowheight=34, borderwidth=0, relief="flat",
                        font=("Microsoft YaHei UI", 9))
        try: style.layout("RBVP.Treeview", [("Treeview.treearea", {"sticky":"nswe"})])
        except tk.TclError: pass
        style.configure("RBVP.Treeview.Heading", background=self.colors["soft"], foreground="#C8D2E7",
                        borderwidth=0, relief="flat", padding=(9,8), font=("Microsoft YaHei UI", 9, "bold"))
        style.map("RBVP.Treeview", background=[("selected", self.colors["accent_soft"])],
                  foreground=[("selected", self.colors["text"])])
        style.configure("RBVP.Vertical.TScrollbar", background=self.colors["card2"], troughcolor=self.colors["soft"],
                        borderwidth=0, arrowsize=11)
        style.configure("RBVP.Horizontal.TScrollbar", background=self.colors["card2"], troughcolor=self.colors["soft"],
                        borderwidth=0, arrowsize=11)


    def _show_page(self,key):
        if not self._ensure_page(key): return
        self.current_page=key
        self.pages[key].tkraise()
        title,subtitle=self.page_titles.get(key,(key,""))
        self.top_title_var.set(title); self.top_subtitle_var.set(subtitle)
        for k in self.nav_buttons: self._paint_nav(k,False)

