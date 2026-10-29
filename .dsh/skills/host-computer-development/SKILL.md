---
name: host-computer-development
description: 无线数传上位机（地面站）开发的完整工作内容与交付工作流。当任务是修改/修复/优化这台上位机（PyQt6+QtWebEngine 前端、协议解析、录制回放、打包 exe）或做交付验收时使用。内含项目事实（技术栈/数据流/关键文件/配置优先级）、9 条硬性规矩、以及本会话用血换来的交付纪律：先测量再改、独立审查优先于自证、功能契约、真机验证、探针退出码与灵敏度证明。
whenToUse: 开发/修复/优化上位机；改前端或排版；协议与配置变更；打包与交付验收
---

# 上位机（地面站）开发：工作内容与工作流

## 一、这是什么

**无线数传遥测上位机** —— 方程式赛车队电动赛车的**地面站**。车载电台把传感器数据发给接收机，
上位机**实时显示 + 录制 + 回放**。用户是车队工程师，在车库/赛道边、阳光下、戴手套、赶时间。

- **技术栈**：Python 3.13 + PyQt6 + QtWebEngine；前端是 `telemetry_web/index.html`（含全部 CSS）
  + `app.js`，通过 `file://` 加载，用 QWebChannel 与 Python 通信；PyInstaller onefile → 约 193MB 单文件 exe。
- **数据流**：串口线程 → `protocol.parse_stream` → `Backend._handle` → `_pending`
  → QTimer 20Hz `_flush` → `dataReady` → `app.js onDataBatch` → `pushSample`（50ms 抽稀）→ 环形缓冲 → rAF 主循环。
- **配置优先级**：`telemetry_user.json` > `telemetry_config.py` > `app.js` 的 `UI_CFG`。
  ★ **用户改过的 config 就是新默认**（R9），提交时一并带上。
- **关键文件**：`launcher_web.py`（后端/窗口/QWebChannel）、`protocol.py`（解包与旧版垫片）、
  `telemetry_config.py`（唯一配置真源）、`telemetry_web/app.js` + `index.html`、`build_web.py`（打包/自检）、
  `selftest.py`、`BUGS.md`（记忆库：规矩 + 事故 + 教训）。
- **实测数据规模**：ADC 100Hz + MOTOR 50Hz + NAV 25Hz（实测总帧率约 206Hz，**不是标称 175Hz**），
  20Hz 批量推到前端；21~24 路信号曲线。

## 二、硬性规矩（违反即返工）

| # | 规矩 | 要点 |
|---|---|---|
| R1 | **面向用户的文本一律中文** | 注释、提示、文档都是 |
| R2 | **项目路径必须英文** | 中文路径下 PyInstaller onefile 建不了临时目录 |
| R3 | **每次版本更新后同步 git** | 改完即提交，别攒着；多行提交信息用 `git commit -F <file>` |
| R4 | **处理任务前先读 `BUGS.md`** | 恢复上下文，避免重复踩坑 |
| R5 | **流畅与不闪烁都要** | 不接受"二选一" |
| R6 | ★ **不要提前操作，只完成明确交代的任务** | 涉及账号/系统/外部服务的操作**先问再做** |
| R7 | **优化意见只提建议，不擅自实施** | 说清建议与理由，等点头 |
| R8 | **严禁把源码推到 GitHub** | 本地 `origin` 已配但**不许推** |
| R9 | **用户改过的 config 即默认配置** | `telemetry_config.py` / `telemetry_user.json` 都算 |

> 另有一条**做法**（不占编号）：排查类改动**一次只改一个变量**，否则无法归因。
> ✅ 编号以 [BUGS.md](../../BUGS.md) 第 1 节为唯一真源；2026-10-16 按用户要求删除了原来的
> R2「不许改前端界面布局」与 R6「一次只改一个变量」两条要求，其余条目重新连续编号。

## 三、★ 交付纪律（这一节的每一条都是踩过坑换来的）

### 1. 先测量，再改
凭感觉改样式/几何，十次有九次改错地方。**先量化**：读 `getBoundingClientRect` / `measureText` /
canvas path 与实际像素、或写只读探针跑一遍，拿到数字再动手。
> 实例：我三次凭"看起来如何"改 UI 都被打回；第四次先做独立审查量出"条的 y 有 6 组不同值"
> "温度标签「90」与它自己的红线差 46px"，才一次改对。

### 2. 独立审查优先于自证
**同一个 agent 自己验自己 = 没验。** 交付前必须有**另一个人/agent**、**只读**地量测并给证据。
更要紧的是：**探针全绿 ≠ 用户看着舒服** —— 探针只验"有没有/对不对"，不验"看得清不清、对齐没对齐、语义对不对"。
> 本会话 18 个探针全绿的同时，界面上躺着 15 类显示缺陷。

### 3. 关键判据要做**灵敏度证明**
新增断言时，**故意把产品改坏，断言必须变红**，再改回。
> 实例：旧判据「环总像素>2000」在指针被涂成底色时**仍是绿的**（判据太弱）；新判据才 47→0 变红。

### 4. 探针的**退出码必须反映失败**
"打印 FAIL 却 `exit 0`"= 假通过，任何 CI 都会放过。
> 本会话一次审计查出**13 个探针**都有这个毛病，已全部补上。
> 顺带两条：**不许"改测试迁就实现"**（改基线前先说明，断言逻辑一行都不许动）；
> `pwsh | Select-Object -First N` 会提前关管道**杀掉子进程**（表现为 `exit=-1`），先完整收集再过滤。

### 5. 一个写者一个文件
并行改同一文件必然互相覆盖。按**文件**切分写范围（例如 `index.html` 归排版、`app.js` 归逻辑），
审查方**只读**。改 DOM 结构时**不许删改既有元素 id**（`app.js` 按 id 取节点）。
> 大型结构改动前先立**功能清单契约**（穷举所有交互元素/事件绑定/前后端 Signal-Slot + 校验脚本，
> 退出码 = FAIL 数），改完逐条核。

### 6. 真机验证
`Chrome headless` ≠ `QtWebEngine`，**用户看的是 QtWebEngine**。
最终验证用真窗口抓图：`QScreen.grabWindow(win.winId())`（`QWebEngineView.grab()` 拿不到网页内容）。
> 实例：真机图上才看到刻度标签重叠成一团 —— Chrome 里完全看不见。

### 7. 打包必须**逐位验证**
`build_web.py` 打完后，不能只看时间戳：**解包 exe 比对 `app.js`/`index.html` 的 SHA256 == 源码**，
再用 `marshal` 检查 `launcher_web` 的关键符号。
> 事故（见 `BUGS.md` 教训 L10）：exe 早于源码 → 用户手里的程序没有修复。打包前先杀残留进程（否则 exe 被占用删不掉）。

## 四、环境陷阱（本机实测）

- **Windows PowerShell 5.1**（不是 pwsh 7）：读 UTF-8 文件要 `-Encoding utf8`；
  管道给 Python 的输出需要 `sys.stdout.reconfigure(encoding="utf-8")`。
- ★ **`pwsh` 工具每次都要显式传 `workdir`**：会话默认 cwd 可能是个**已不存在的旧路径**，
  不传会报 `spawn powershell.exe ENOENT`（Node 对不存在的 cwd 就是报这个误导性错误）。
- 跑 Qt/离屏脚本前设 `$env:QT_QPA_PLATFORM="offscreen"`；
  stderr 里的 Chromium GPU ERROR / `QFontDatabase` / `SKIA` 警告是**正常噪声**，只看退出码与结论。
- **渲染红线**：20Hz 高频元素**禁止** `transition > 50ms`、`will-change`、`content-visibility`；
  不许动 GPU/渲染参数（那台机器是 Optimus 双显卡，靠 `qt-opengl` 才压住闪烁）。
- 性能数字要在**真窗口**下测：离屏软件渲染下 canvas 会贵 30%，qt-opengl 下不复现。

## 五、验收命令（交付前逐条跑）

```
node --check telemetry_web/app.js
python build_web.py --check-only          # 期望「✅ 自检全部通过」
python selftest.py                        # 期望「结果: 全部通过」
python tools/_fninv_check.py              # 功能清单契约，退出码 = FAIL 数
python tools/_e2e_realdata.py             # 真机录播跑完整链路（loading/进度/跳转/矢量图/NAV 六路）
python tools/_layout_flash_probe.py       # 溢出/裁切 = 0，拖动无陈旧位图/空白帧
python tools/web_ui_selfcheck.py          # 界面自检（含 U08 可视化通道同步）
python tools/config_matrix_audit.py       # 配置矩阵 24/24
```
★ 探针全家桶在 `tools/`（`_accvec_*` / `_pwr_check` / `_ux_needle_probe` / `_ring_*` / `_d1d2d5_check` /
`_replay_bar_check` / `_serial_jitter_probe` / `_width_reserve_check` / `_clamp_check` / `_task8_check` …），
**不要删**。新增探针请遵循第三节第 3、4 条（灵敏度证明 + 退出码）。
