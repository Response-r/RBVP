# 🚀 RBVP

RBVP 是一个面向 **Left 4 Dead 2 Mod / VPK 资源处理** 的 Windows 桌面工具集，使用 Python + Tkinter 构建，采用模块化结构组织界面、业务逻辑和底层解析功能。

> 当前项目主要面向 Windows 环境，并依赖部分第三方工具完成 Crowbar、NekoMDL、3DS Max 等相关工作流。

## 🧩 功能

### 📦 VPK 工具

- 📤 **VPK 解包**：支持一次选择多个 `.vpk` 文件，使用 RBVP 内置 VPK 解析器进行解包，不依赖外部 `vpk.exe`。
- 📦 **VPK 打包**：选择包含多个 Mod 文件夹的父目录，自动识别符合条件的 Mod 并批量打包。
- 🗃️ **Mod 文件夹识别**：目录中至少包含以下任意一项即可视为 Mod：
  - `addoninfo.txt`
  - `materials`
  - `models`
  - `particles`
  - `scripts`
  - `sound`
- 官方 `vpk.exe` 未产生有效输出时，会尝试使用 RBVP 内置 VPK1 打包器进行回退。
- 打包结果会尽量保留源 Mod 文件夹的大小写命名。

### 🔍 Mod 分析与诊断

- 📊 **模型精度统计**：扫描 Addons 中的 VPK，统计模型 LOD0 的顶点与三角面信息，并支持按数量降序查看。
- ⚡ **Mod 冲突检测**：扫描 Addons 中的 VPK，检测重复资源路径；`addoninfo` / `addonimage` 不参与冲突统计。
- 🧪 **纹理组检测**：检查 VPK 中的 MDL 是否包含 `$texturegroup`。
- 🧹 **纹理组清除**：对命中的 MDL 执行 Crowbar 反编译、清理 QC 中的 `$texturegroup`，再通过 NekoMDL 编译并覆盖原资源。

### 🎯 批量减面（3DS Max）

- 在解包后的 Mod 文件夹中筛选 `models` 下的 `$staticprop` MDL。
- 排除：
  - `models/v_models`
  - `models/w_models`
  - `models/weapons`
- 支持通过 GUI 指定**最低三角面数扫描条件**。
- 使用 **3DS Max ProOptimizer** 批量减面。
- 会解析 QC 中的 `$body`、`$model`、`$bodygroup` 所引用的实际 SMD 名称，不强依赖固定的 `_reference.smd` 命名。
- 对存在 `$lod` 的模型，批处理流程会处理基础 SMD，并清理生成的 LOD SMD 与对应 `$lod` 区块。
- 每个批次复用专用 3DS Max 工作会话，减少频繁启动 3DS Max 的固定开销。
- 成功结果会写入处理记录，用于避免同一结果被重复处理；失败记录不会永久跳过模型。

### 🛠️ 其他工具

- 📝 **VMT 修改**：批量扫描多个 Mod 的 `materials` 目录并向 VMT 追加指定参数。
- 🖼️ **VTFEdit**：从 RBVP 内直接启动已配置的 `VTFEdit.exe`。
- 💾 **设置持久化**：VPK.exe、VTFEdit、Crowbar、NekoMDL、Addons、3DS Max 路径会保存到本地 `settings.json`，下次启动可自动复用。

## 💻 环境要求

建议使用：

- Windows 10 / Windows 11
- Python **3.11**
- Tkinter（通常随 Windows Python 安装提供）
- Pillow（用于 RBVP 的抗锯齿圆角 UI）

第三方工具按功能需要配置：

| 🔧 工具 | 🧭 用途 | 📌 是否必须 |
| --- | --- | --- |
| `vpk.exe` | VPK 批量打包 | VPK 打包功能需要 |
| `Crowbar.exe` | MDL / QC 反编译与部分编译流程 | 纹理组清除、批量减面需要 |
| `nekomdl.exe` | QC 编译 | 纹理组清除、批量减面需要 |
| `3dsmax.exe` | ProOptimizer 批量减面 | 批量减面需要 |
| `VTFEdit.exe` | VTF 编辑器启动 | 仅 VTFEdit 功能需要 |

### ⚠️ 3DS Max 注意事项

批量减面模块针对 **3DS Max + ProOptimizer** 工作流设计。项目代码包含对常见 3DS Max 安装位置的自动搜索，同时也支持在 GUI 的“设置”页面手动指定 `3dsmax.exe`。

## 🛠️ 安装与运行

### 📥 1. 获取源码

将项目克隆到本地：

```bash
git clone <your-repository-url>
cd vpktool
```

### 🐍 2. 创建虚拟环境

```bash
python -m venv .venv
```

激活虚拟环境后安装 Pillow：

```bash
python -m pip install --upgrade pip
python -m pip install Pillow
```

> RBVP 的 UI 模块在缺少 Pillow 时也会尝试自动安装，但推荐提前手动安装，以便更明确地控制运行环境。

### ▶️ 3. 启动

```bash
python main.py
```

也可以直接使用项目中的 Python 解释器运行 `main.py`。

## ⚙️ 第一次使用

启动 RBVP 后进入 **设置**，确认以下路径：

1. VPK.exe
2. VTFEdit
3. Crowbar
4. NekoMDL
5. Left 4 Dead 2 `Addons` 目录
6. 3DS Max

部分路径支持自动搜索；无法自动找到时可以手动选择或填写。

## 🗂️ 项目结构

```text
vpktool/
├─ app/
│  ├─ actions/          # 各功能页面的操作/任务线程
│  ├─ application.py    # 应用入口与全局状态
│  ├─ layout.py         # 主界面布局与导航
│  ├─ lifecycle.py      # 设置加载、保存、环境发现
│  └─ page_builders.py  # 各页面 UI 构建
├─ core/
│  ├─ batch_reduce.py   # 3DS Max / ProOptimizer 批量减面核心
│  ├─ conflict.py      # Mod 判定与冲突相关逻辑
│  ├─ crowbar.py        # Crowbar / NekoMDL 工具链
│  ├─ models.py         # MDL / VVD / VTX 模型数据解析
│  ├─ paths.py          # 工具路径自动发现
│  ├─ texturegroup.py   # $texturegroup 检测
│  ├─ texturegroup_clear.py
│  ├─ vmt.py            # VMT 扫描与修改
│  └─ vpk.py            # VPK 解析与内部打包
├─ ui/
│  └─ widgets.py        # 抗锯齿圆角控件
├─ main.py              # 程序入口
├─ README.md            # GitHub 项目说明
└─ .gitignore           # Git 忽略规则
```

## 📁 数据与生成文件

RBVP 会在运行过程中生成一些本地配置、缓存和批处理记录，例如：

- `settings.json`
- `.rbvp_model_cache.json`
- `.rbvp_batch_reduce_done.json`
- `.rbvp_batch_reduce_failed.json`

这些运行时文件不属于源码，已经加入 `.gitignore`。

## ⚠️ 注意事项

- **纹理组清除和批量减面会修改/覆盖模型资源。** 使用前请先做好备份。
- 批量减面属于模型重编译流程，实际结果会受到 MDL、QC、SMD、骨骼、LOD、3DS Max 版本及第三方工具状态影响。
- Crowbar、NekoMDL、3DS Max、VTFEdit 均为外部软件，相关许可与分发权利由各自项目/软件作者负责。
- 本仓库不应提交个人机器上的完整虚拟环境、IDE 配置、缓存或本地路径设置。

## 👨‍💻 开发说明

项目采用按职责拆分的模块结构：

- `app/` 负责界面、交互和任务调度；
- `core/` 负责格式解析、工具链调用和业务逻辑；
- `ui/` 负责可复用的 Tkinter 控件。

新增功能时，建议优先将底层逻辑放入 `core/`，将页面事件与线程调度放入 `app/actions/`，避免继续把业务逻辑集中到单一文件中。

## 📄 License

当前项目未在源码中声明明确的开源许可证。若准备公开发布，建议在仓库中根据实际版权归属补充合适的 `LICENSE` 文件。

## 🏗️ Windows 打包

项目提供 `build_RBVP.bat` 与 `RBVP.spec`。推荐在 Windows 项目根目录直接运行 `build_RBVP.bat`，脚本会清理旧的 `build` / `dist` 并使用项目 `venv` 进行 PyInstaller 打包。

打包入口固定为 `main.py`，并显式收集 `app`、`core`、`ui` 的 Python 模块及 `assets` 资源，避免模块化目录在 EXE 中被漏收。

生成文件：`dist\\RBVP\\RBVP.exe`
