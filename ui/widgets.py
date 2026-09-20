import subprocess
import sys
from collections import OrderedDict
import tkinter as tk
from tkinter import ttk

def _ensure_pillow():
    try:
        from PIL import Image, ImageDraw, ImageTk
        return Image, ImageDraw, ImageTk
    except ImportError:
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "Pillow"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            from PIL import Image, ImageDraw, ImageTk
            return Image, ImageDraw, ImageTk
        except Exception as exc:
            raise RuntimeError("RBVP 的抗锯齿圆角界面需要 Pillow。\n\n请在当前虚拟环境执行：\npython -m pip install Pillow\n\n" + f"原始错误：{exc}") from exc

Image, ImageDraw, ImageTk = _ensure_pillow()
_ROUND_IMAGE_CACHE=OrderedDict()
_ROUND_IMAGE_CACHE_MAX=256
_AA_SCALE=2
_AA_RESAMPLE=Image.Resampling.BILINEAR

def _aa_cached_image(w,h,radius,fill,outline=None,outline_width=0,scale=_AA_SCALE):
    key=(int(w),int(h),int(radius),str(fill),str(outline),int(outline_width),int(scale))
    hit=_ROUND_IMAGE_CACHE.get(key)
    if hit is not None:
        _ROUND_IMAGE_CACHE.move_to_end(key); return hit
    sw,sh=max(8,int(w*scale)),max(8,int(h*scale))
    img=Image.new("RGBA",(sw,sh),(0,0,0,0)); d=ImageDraw.Draw(img)
    r=min(int(radius*scale),sw//2,sh//2); stroke=int(outline_width*scale) if outline else 0
    d.rounded_rectangle((0,0,sw-1,sh-1),radius=r,fill=fill,outline=outline,width=max(1,stroke) if outline else 1)
    photo=ImageTk.PhotoImage(img.resize((max(2,int(w)),max(2,int(h))),_AA_RESAMPLE))
    _ROUND_IMAGE_CACHE[key]=photo
    if len(_ROUND_IMAGE_CACHE)>_ROUND_IMAGE_CACHE_MAX: _ROUND_IMAGE_CACHE.popitem(last=False)
    return photo

class RoundedButton(tk.Canvas):
    """稳定的自绘圆角按钮：使用椭圆+矩形组合，避免 Canvas arc 填充造成的白边/缺口。"""
    def __init__(self, parent, text, command=None, width=132, height=40,
                 bg="#6675F5", hover_bg="#7B88FF", fg="#FFFFFF",
                 radius=10, font=("Microsoft YaHei UI", 9, "bold"),
                 outline="", disabled_bg="#27324A", disabled_fg="#70809D"):
        super().__init__(parent, width=width, height=height, bd=0, highlightthickness=0,
                         bg=parent.cget("bg"), cursor="hand2")
        self._btn_width = width
        self._btn_height = height
        self._radius = max(4, min(radius, min(width, height) // 2))
        self._text = text
        self._command = command
        self._bg = bg
        self._hover_bg = hover_bg
        self._fg = fg
        self._font = font
        self._outline = outline
        self._disabled_bg = disabled_bg
        self._disabled_fg = disabled_fg
        self._enabled = True
        self._draw(bg)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    @staticmethod
    def _round_shape(canvas, x1, y1, x2, y2, r, fill, outline="", width=1, tag=None):
        r = max(2, min(int(r), int((x2-x1)/2), int((y2-y1)/2)))
        tags = () if tag is None else (tag,)
        items = []
        # outer body - no arc/pieslice outline, therefore no seam lines
        items.append(canvas.create_rectangle(x1+r, y1, x2-r, y2, fill=fill, outline=fill, tags=tags))
        items.append(canvas.create_rectangle(x1, y1+r, x2, y2-r, fill=fill, outline=fill, tags=tags))
        items.append(canvas.create_oval(x1, y1, x1+2*r, y1+2*r, fill=fill, outline=fill, tags=tags))
        items.append(canvas.create_oval(x2-2*r, y1, x2, y1+2*r, fill=fill, outline=fill, tags=tags))
        items.append(canvas.create_oval(x1, y2-2*r, x1+2*r, y2, fill=fill, outline=fill, tags=tags))
        items.append(canvas.create_oval(x2-2*r, y2-2*r, x2, y2, fill=fill, outline=fill, tags=tags))
        if outline:
            # thin inset border using a second clean rounded shape
            inset = max(1, int(width))
            ix1, iy1, ix2, iy2 = x1+inset, y1+inset, x2-inset, y2-inset
            ir = max(2, r-inset)
            items.append(canvas.create_rectangle(ix1+ir, iy1, ix2-ir, iy2, fill=fill, outline=outline, width=1, tags=tags))
            # intentionally omitted: a full outline loop is visually heavier than the source style
        return items

    def _draw(self, fill):
        self.delete("all")
        pad = 1
        self._round_shape(self, pad, pad, self._btn_width-pad, self._btn_height-pad,
                          self._radius, fill, self._outline)
        self.create_text(self._btn_width / 2, self._btn_height / 2, text=self._text,
                         fill=self._fg if self._enabled else self._disabled_fg,
                         font=self._font)

    def _on_enter(self, _):
        if self._enabled:
            self._draw(self._hover_bg)

    def _on_leave(self, _):
        if self._enabled:
            self._draw(self._bg)

    def _on_click(self, _):
        if self._enabled and self._command:
            self._command()

    def set_enabled(self, enabled=True):
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw(self._bg if enabled else self._disabled_bg)


class RoundedText(tk.Canvas):
    """圆角日志/文本区域，避免传统 Text/Frame 矩形块破坏整体视觉。"""
    def __init__(self, parent, **text_kwargs):
        self._parent = parent
        self._fill = text_kwargs.pop("panel_bg", "#17243A")
        self._radius = text_kwargs.pop("radius", 14)
        self._padding = text_kwargs.pop("padding", 10)
        super().__init__(parent, bd=0, highlightthickness=0, bg=parent.cget("bg"))
        self.text = tk.Text(self, relief="flat", bd=0, wrap="word", **text_kwargs)
        self._window_id = self.create_window(self._padding, self._padding, anchor="nw", window=self.text)
        self.bind("<Configure>", self._on_resize)
        self.after_idle(self._on_resize)

    def _on_resize(self, _event=None):
        w = max(2, self.winfo_width())
        h = max(2, self.winfo_height())
        tk.Canvas.delete(self, "shape")
        # Clean round fill; no arc outline seams.
        RoundedButton._round_shape(self, 0, 0, w, h, self._radius, self._fill, tag="shape")
        self.tag_lower("shape")
        inner_w = max(2, w - self._padding*2)
        inner_h = max(2, h - self._padding*2)
        self.itemconfigure(self._window_id, width=inner_w, height=inner_h)

    def insert(self, *args, **kwargs):
        return self.text.insert(*args, **kwargs)

    def delete(self, *args, **kwargs):
        return self.text.delete(*args, **kwargs)

    def see(self, *args, **kwargs):
        return self.text.see(*args, **kwargs)

    def get(self, *args, **kwargs):
        return self.text.get(*args, **kwargs)


class AARoundedSurface(tk.Canvas):
    """基于 2x supersampling + BILINEAR 的高性能抗锯齿圆角表面。"""
    def __init__(self, parent, fill, radius=14, outline=None, outline_width=0, pad=None, **kwargs):
        super().__init__(parent, bg=parent.cget("bg"), bd=0, highlightthickness=0, **kwargs)
        self.fill = fill
        self.radius = radius
        self.outline = outline
        self.outline_width = outline_width
        self.scale = _AA_SCALE
        self.pad = max(radius, 10) if pad is None else int(pad)
        self.image = None
        self._after_id = None
        self.bind("<Configure>", self._redraw)
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, _event=None):
        if self._after_id:
            try: self.after_cancel(self._after_id)
            except Exception: pass
            self._after_id = None
        self.image = None

    def _make_image(self, w, h):
        return _aa_cached_image(w,h,self.radius,self.fill,self.outline,self.outline_width,self.scale)

    def _redraw(self, _event=None):
        if not self.winfo_exists():
            return
        w, h = max(2, self.winfo_width()), max(2, self.winfo_height())
        if getattr(self, "_pending_size", None) == (w, h) and self._after_id:
            return
        self._pending_size = (w, h)
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
        self._after_id = self.after(35, self._draw_now, w, h)

    def _draw_now(self, w, h):
        if not self.winfo_exists():
            self._after_id = None
            return
        self._after_id = None
        self._pending_size = None
        if getattr(self, "_last_draw_size", None) == (w, h) and getattr(self, "image", None) is not None:
            return
        self._last_draw_size = (w, h)
        self.delete("bg")
        self.image = self._make_image(w, h)
        self.create_image(0, 0, image=self.image, anchor="nw", tags="bg")
        self.tag_lower("bg")


class AARoundedPanel(tk.Canvas):
    """抗锯齿圆角容器；内部内容避开圆角四角，避免方形子控件盖住圆角。"""
    def __init__(self, parent, fill, radius=14, pad=None, outline=None, outline_width=0, **kwargs):
        super().__init__(parent, bg=parent.cget("bg"), bd=0, highlightthickness=0, **kwargs)
        self.fill = fill
        self.radius = radius
        self.pad = max(radius, 10) if pad is None else int(pad)
        self.outline = outline
        self.outline_width = outline_width
        self.scale = _AA_SCALE
        self.bg_image = None
        self.inner = tk.Frame(self, bg=fill, bd=0, highlightthickness=0)
        self.window_id = self.create_window(self.pad, self.pad, window=self.inner, anchor="nw")
        # 内容尺寸变化时重新检查面板最小高度，避免固定高度的圆角面板把内部控件裁掉。
        self.inner.bind("<Configure>", self._content_configure, add="+")
        self.bind("<Configure>", self._redraw)
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, _event=None):
        rid = getattr(self, "_redraw_id", None)
        if rid:
            try: self.after_cancel(rid)
            except Exception: pass
            self._redraw_id = None
        self.bg_image = None

    def _rounded_image(self, w, h):
        return _aa_cached_image(
            max(2, int(w)), max(2, int(h)), self.radius,
            self.fill, self.outline, self.outline_width, self.scale
        )

    def _redraw(self, _event=None):
        if not self.winfo_exists():
            return
        size = (max(2, self.winfo_width()), max(2, self.winfo_height()))
        if getattr(self, "_pending_size", None) == size and getattr(self, "_redraw_id", None):
            return
        self._pending_size = size
        if getattr(self, "_redraw_id", None):
            try: self.after_cancel(self._redraw_id)
            except Exception: pass
        self._redraw_id = self.after(35, self._redraw_now)

    def _content_configure(self, _event=None):
        # ttk/Tk 子控件的实际需求尺寸可能在第一次布局后才确定。
        # 延迟到当前布局周期结束后检查，避免在 Configure 回调中形成递归。
        rid = getattr(self, "_content_size_id", None)
        if rid:
            try: self.after_cancel(rid)
            except Exception: pass
        self._content_size_id = self.after_idle(self._ensure_content_size)

    def _ensure_content_size(self):
        self._content_size_id = None
        if not self.winfo_exists() or not self.inner.winfo_exists():
            return
        try:
            self.inner.update_idletasks()
            w = max(2, self.winfo_width())
            h = max(2, self.winfo_height())
            p = min(self.pad, max(2, min(w, h) // 2 - 1))
            req_h = max(1, self.inner.winfo_reqheight())
            needed_h = req_h + p * 2
            # 只向上扩，不强制缩小，避免管理器/窗口缩放时来回抖动。
            if needed_h > h + 1:
                try:
                    self.configure(height=needed_h)
                except tk.TclError:
                    pass
        except tk.TclError:
            pass

    def _redraw_now(self):
        if not self.winfo_exists():
            self._redraw_id = None
            return
        self._redraw_id = None
        self._pending_size = None
        w, h = max(2,self.winfo_width()), max(2,self.winfo_height())
        if getattr(self, "_last_draw_size", None) == (w, h) and getattr(self, "bg_image", None) is not None:
            # 即使背景尺寸没有变化，也要给内部内容一次尺寸检查机会。
            self._ensure_content_size()
            return
        self._last_draw_size = (w, h)
        self.delete("bg")
        self.bg_image = self._rounded_image(w,h)
        self.create_image(0,0,image=self.bg_image,anchor="nw",tags="bg")
        self.tag_lower("bg")
        p = min(self.pad, max(2,min(w,h)//2-1))
        self.coords(self.window_id, p, p)
        self.itemconfigure(self.window_id, width=max(2,w-2*p), height=max(2,h-2*p))
        self._ensure_content_size()


class AARoundedButton(tk.Canvas):
    """抗锯齿圆角按钮；无 Unicode 箭头、无直角覆盖。"""
    def __init__(self, parent, text, command=None, width=132, height=40,
                 bg="#7180FF", hover_bg="#8290FF", fg="#FFFFFF", radius=10,
                 outline=None, outline_width=1, font=("Microsoft YaHei UI", 9, "bold")):
        super().__init__(parent, width=width, height=height, bd=0, highlightthickness=0,
                         bg=parent.cget("bg"), cursor="hand2")
        self._btn_w, self._btn_h = width, height
        self._radius = min(radius, height//2, width//2)
        self._bg, self._hover = bg, hover_bg
        self._fg, self._font = fg, font
        self._outline, self._outline_w = outline, outline_width
        self._text, self._command = text, command
        self._image = None
        self._hovered = False
        self._enabled = True
        self._draw(bg)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", lambda _e: self._click())

    def _make_image(self, fill):
        outline=self._outline if self._outline else fill
        return _aa_cached_image(self._btn_w,self._btn_h,self._radius,fill,outline,self._outline_w,_AA_SCALE)

    def _draw(self, fill):
        self.delete("all")
        self._image = self._make_image(fill)
        self.create_image(0,0,image=self._image,anchor="nw")
        self.create_text(self._btn_w/2,self._btn_h/2,text=self._text,fill=self._fg,font=self._font)

    def _set_hover(self, value):
        if value != self._hovered:
            self._hovered = value
            self._draw(self._hover if value else self._bg)

    def set_text(self, text):
        self._text = str(text)
        self._draw(self._hover if self._hovered else self._bg)

    def set_command(self, command):
        self._command = command

    def set_enabled(self, enabled=True):
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if self._enabled else "arrow")
        self._draw((self._hover if self._hovered else self._bg) if self._enabled else self._bg)

    def _click(self):
        if getattr(self, "_enabled", True) and callable(self._command):
            self._command()


class AARoundedEntry(tk.Canvas):
    """统一的高质量抗锯齿圆角输入框：支持 textvariable、焦点高亮和响应式宽度。"""
    def __init__(self, parent, value="", radius=10, height=42, width=0, bg="#0B1629", border="#263A5B",
                 focus_border="#7584FF", textvariable=None, **entry_kwargs):
        canvas_kwargs = {"height": height, "bd": 0, "highlightthickness": 0, "bg": parent.cget("bg")}
        if width:
            canvas_kwargs["width"] = int(width)
        super().__init__(parent, **canvas_kwargs)
        self._bg_fill = bg
        self._border = border
        self._focus_border = focus_border
        self._current_border = border
        self._radius = radius
        self._height = height
        self._image = None
        self._redraw_id = None
        self.var = textvariable if textvariable is not None else tk.StringVar(value=value)
        # 允许调用方覆盖输入框字体；先从 entry_kwargs 取出，
        # 避免与这里的默认 font= 参数重复传递给 tkinter.Entry。
        entry_font = entry_kwargs.pop("font", ("Consolas", 10))
        self.entry = tk.Entry(self, relief="flat", bd=0, highlightthickness=0,
                              bg=bg, fg="#F4F7FF", insertbackground="#F4F7FF",
                              selectbackground="#283964", selectforeground="#F4F7FF",
                              font=entry_font, textvariable=self.var, **entry_kwargs)
        if textvariable is None:
            self.var.set(value)
        self._window = self.create_window(14, height//2, anchor="w", window=self.entry)
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        self.bind("<Configure>", self._resize)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.after_idle(self._resize)

    def _on_destroy(self, _event=None):
        rid = getattr(self, "_redraw_id", None)
        if rid:
            try: self.after_cancel(rid)
            except Exception: pass
            self._redraw_id = None
        self._image = None

    def _make_bg(self, w, h):
        return _aa_cached_image(max(2,int(w)), max(2,int(h)), self._radius,
                                self._bg_fill, self._current_border, 1)

    def _schedule_redraw(self):
        if self._redraw_id:
            try: self.after_cancel(self._redraw_id)
            except Exception: pass
        self._redraw_id = self.after_idle(self._resize_now)

    def _resize(self, _e=None):
        if not self.winfo_exists():
            return
        self._schedule_redraw()

    def _resize_now(self):
        if not self.winfo_exists():
            self._redraw_id = None
            return
        self._redraw_id = None
        w, h = max(40,self.winfo_width()), max(self._height,self.winfo_height())
        tk.Canvas.delete(self, "bg")
        self._image = self._make_bg(w,h)
        self.create_image(0,0,image=self._image,anchor="nw",tags="bg")
        self.tag_lower("bg")
        self.coords(self._window, 14, h//2)
        self.itemconfigure(self._window, width=max(20,w-28), height=max(20,h-10))

    def _focus_in(self, _e=None):
        self._current_border = self._focus_border
        self._schedule_redraw()

    def _focus_out(self, _e=None):
        self._current_border = self._border
        self._schedule_redraw()

    def focus_set(self): return self.entry.focus_set()
    def get(self): return self.var.get()
    def delete(self,*a,**k): return self.entry.delete(*a,**k)
    def insert(self,*a,**k): return self.entry.insert(*a,**k)


class AARoundedText(tk.Canvas):
    """抗锯齿圆角日志区，外层是真正的圆角表面，内部 Text 保持完整编辑能力。"""
    def __init__(self, parent, panel_bg, input_bg, radius=14, pad=10, **text_kwargs):
        super().__init__(parent, bg=parent.cget("bg"), bd=0, highlightthickness=0)
        self._panel_bg = panel_bg
        self._radius = radius
        self._pad = pad
        self._scale = _AA_SCALE
        self._image = None
        self.inner = tk.Frame(self, bg=panel_bg, bd=0, highlightthickness=0)
        self._window = self.create_window(pad, pad, anchor="nw", window=self.inner)
        self.inner.grid_rowconfigure(0, weight=1)
        self.inner.grid_columnconfigure(0, weight=1)
        self.text = tk.Text(self.inner, relief="flat", bd=0, highlightthickness=0, bg=input_bg,
                            **text_kwargs)
        self.scroll = ttk.Scrollbar(self.inner, orient="vertical", command=self.text.yview,
                                    style="RBVP.Vertical.TScrollbar")
        self.text.configure(yscrollcommand=self.scroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        self.scroll.grid(row=0, column=1, sticky="ns")
        self.bind("<Configure>", self._resize)
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, _event=None):
        rid = getattr(self, "_resize_id", None)
        if rid:
            try: self.after_cancel(rid)
            except Exception: pass
            self._resize_id = None
        self._image = None

    def _make_bg(self, w, h):
        return _aa_cached_image(w,h,self._radius,self._panel_bg,None,0,self._scale)

    def _resize(self, _e=None):
        if not self.winfo_exists():
            return
        size = (max(2, self.winfo_width()), max(2, self.winfo_height()))
        if getattr(self, "_pending_size", None) == size and getattr(self, "_resize_id", None):
            return
        self._pending_size = size
        if getattr(self, "_resize_id", None):
            try: self.after_cancel(self._resize_id)
            except Exception: pass
        self._resize_id=self.after(35, self._resize_now)

    def _resize_now(self):
        if not self.winfo_exists():
            self._resize_id=None
            return
        self._resize_id=None
        self._pending_size=None
        w,h=max(2,self.winfo_width()),max(2,self.winfo_height())
        if getattr(self, "_last_draw_size", None) == (w, h) and getattr(self, "_image", None) is not None:
            return
        self._last_draw_size = (w, h)
        tk.Canvas.delete(self, "bg")
        self._image=self._make_bg(w,h)
        self.create_image(0,0,image=self._image,anchor="nw",tags="bg")
        self.tag_lower("bg")
        p=min(self._pad,max(2,min(w,h)//2-1))
        self.coords(self._window,p,p)
        self.itemconfigure(self._window,width=max(2,w-2*p),height=max(2,h-2*p))

    def insert(self,*a,**k): return self.text.insert(*a,**k)
    def delete(self,*a,**k): return self.text.delete(*a,**k)
    def see(self,*a,**k): return self.text.see(*a,**k)
    def get(self,*a,**k): return self.text.get(*a,**k)
    def configure_text(self, **kwargs): self.text.configure(**kwargs)

