import os
import sys

# 确保单文件打包解压目录或当前运行目录被加入模块搜索路径
if getattr(sys, 'frozen', False):
    # PyInstaller 解压后的临时根目录
    project_root = sys._MEIPASS
else:
    # 开发环境下的项目根目录
    project_root = os.path.dirname(os.path.abspath(__file__))

if project_root not in sys.path:
    sys.path.insert(0, project_root)

# ----- 下面才是你原本的 import app 等代码 -----
import app

import multiprocessing
import tkinter as tk
from app.application import RBVPApplication

if __name__ == "__main__":
    multiprocessing.freeze_support()
    root=tk.Tk()
    app=RBVPApplication(root)
    root.mainloop()
