---
name: telemetry-qt-packaging
description: 修改本遥测上位机的 PyQt6 外壳与打包链路（launcher_web.py、build_web.py、一键打包.bat），或排查窗口闪烁、GPU 渲染、exe 启动失败、打包警告时使用。内含 Optimus 双显卡闪烁的已确认根因与解法、PyInstaller 必需参数、以及打包后必须做的实测验证。
whenToUse: 涉及 launcher_web.py / build_web.py / 打包脚本，或排查闪烁、渲染、打包问题时
---

# Qt 外壳与打包：两个已经烧过很多轮的坑

## 一、★ 闪烁问题的根因与解法（别再走回头路）

**现象**：数据一进来（或拖动/滚动时）整个窗口偶发闪一下。

**已经确认的根因**（2026-10-14）：
本机是 **Optimus 双显卡笔记本**（NVIDIA RTX 4060 负责渲染，Intel 核显负责输出显示），
帧必须**跨 GPU 拷贝**一次；该拷贝与 Qt 的合成不同步时就是闪烁。

**正确解法**（已固化）：
```python
# launcher_web.py
DEFAULT_GL_PRESET = "qt-opengl"   # → 设 QSG_RHI_BACKEND=opengl
```
把 **Qt 场景图**后端从 Windows 默认的 D3D11 换成 OpenGL，绕开出问题的组合，
**而且不需要关闭 GPU**（流畅度不受影响）。

**⚠️ 不要回到"GPU 开 / 关"那个维度调参**——那是错误的方向，曾经为此走了 12 轮弯路：
- `--disable-gpu` 全禁 ⇒ 不闪了但滚动卡死（CPU 软件光栅）
- GPU 全开 ⇒ 流畅但闪
这是个假二分。真正的维度是**图形后端**，不是 GPU 开关。

**继续排查的工具**（源码启动，秒级生效，不用打包）：
```powershell
诊断_闪烁排查.bat          # 12 个后端预设逐个试 + Windows 每程序 GPU 偏好设置
```
`TELEMETRY_GL=<预设名> python launcher_web.py` 也可直接覆盖。
**已知必然崩溃、不要试**：`--use-gl=desktop`、`--use-gl=swiftshader`（退出码 `0xC0000409`）。

## 二、怎么确证"渲染设置真的生效了"

打包成窗口程序后**没有控制台**，`print` 看不见。所以程序会把实际生效的后端写进日志：

```
<保存目录>\telemetry\startup.log
2026-10-13 22:15:23  图形后端=qt-opengl  QSG_RHI_BACKEND=opengl  渲染模式=smooth  冻结=是
```
**这是唯一能确认"exe 到底跑的哪个后端"的手段**，排查时先读它。

## 三、打包链路

```
一键打包.bat → build_web.py → PyInstaller onefile
```

`build_web.py --check-only` 会跑 **15 个步骤**（步骤 0/1/2/3/4/4.5/4.6/4.7/4.75/4.8/4.85/4.86/4.88/4.9/4.10）。

### PyInstaller 必需参数（少一个就出问题）

```
--onefile --windowed
--add-data telemetry_web;telemetry_web
--add-data logo.ico;.
--hidden-import PyQt6.QtWebEngineWidgets
--hidden-import PyQt6.QtWebEngineCore
--hidden-import PyQt6.QtWebChannel
```

### 三条铁律

1. **必须清理构建缓存**——否则出现"exe 是新的、界面是旧的"，这是本工程最浪费时间的坑；
2. **新增硬依赖要改三处**：`requirements.txt` + `build_web.py` 步骤 0 环境检查 + `--hidden-import`；
3. **打包后必须启动 exe 实测**，不能只看"打包成功"。

### 修打包警告的正确姿势

先判断那个模块**是不是真的会被用到**：
- `scipy` 只在 pyqtgraph 的 `order>1` 图像插值分支里 import（本项目只画折线，走不到）⇒ 可以 `--exclude-module scipy`，还省 30 MB；
- `pycparser.lextab/yacctab` 是 ply 运行时自动生成的表 ⇒ 可以排除。

## 四、验收

```powershell
python -m py_compile launcher_web.py
python build_web.py --check-only            # → "✅ 自检全部通过"
python tools/launch_check.py                # → "RESULT: 启动正常，4 秒内无异常"
# 涉及打包时：
python build_web.py --no-explorer           # 完整打包（约 65~140 秒）
# 然后启动 无线数传上位机_v2.exe，确认窗口 5 秒内出现
```

> 环境提醒：本机 **PowerShell 5.1**，读 UTF-8 要 `-Encoding utf8`。
> stderr 里 `SharedImageManager::ProduceMemory`、`Failed to create GLES3 context`、
> `QFontDatabase: Cannot find font directory` 全是**正常噪声**，只看退出码和通过标志。

## 五、跳过自检的办法（自检误报时）

```python
# build_web.py 顶部
SKIP_SELFCHECK = False   # 改 True 后双击 bat 即跳过深度自检
```
或直接双击 `打包_跳过自检.bat`。
**语法检查永远会跑**（快、可靠、不依赖显卡）；`--check-only` 优先级最高，可随时强制全量自检。
