# 子 Agent 上手包（CONTEXT）

> **用途**：子 agent **不共享主对话上下文**。委派任务时，把本文件（或相关章节）
> 连同任务一起贴进它的提示词，避免它重新摸索、或重踩已知的坑。
> **最后更新**：2026-10-14 · 版本锚点 `9a82046`
>
> ⚠️ **锚点只代表最后一次提交，不代表当前磁盘状态。** 动手前请自己跑
> `git log --oneline -1` 与 `git status --short` 确认（可能有人有未提交改动）。
> 记录时（2026-10-14）工作区有：`M telemetry_config.py`（F_ACCEL 定标 0.0001→0.001，未提交）、
> `?? agents/`。

---

## 1. 这是什么项目

**无线数传上位机**——CSUST 方程式赛车队（纯电动）的遥测上位机。

- **后端**：Python 3.13 + PyQt6（`launcher_web.py`）——真串口读取、帧解析、录像落盘、通过 QWebChannel 把数据推给页面；
- **前端**：手写 HTML/CSS/JS 单文件（`telemetry_web/`），由 **QtWebEngine** 以 `file://` 加载；
- **产物**：PyInstaller onefile 的 Windows exe（约 193 MB）。

数据链路（改代码前先理解这条线）：

```
串口读取线程 → protocol.parse_stream → Backend._handle → _pending 队列
   → QTimer _flush（20 Hz 批量） → dataReady 信号 → QWebChannel
   → app.js onDataBatch → pushSample → 环形缓冲 → requestAnimationFrame 主循环绘制
```

> 写盘在**读取线程内**完成，与前端渲染完全解耦：前端再卡也不丢数据。

---

## 2. 目录地图（只列要动的）

| 路径 | 作用 | 谁负责 |
|---|---|---|
| `launcher_web.py` | PyQt6 外壳：串口、解析、录制、WebChannel、**GPU/渲染参数** | Qt/打包专家 |
| `telemetry_config.py` | ★ **唯一配置真源**（分 6 区：串口/帧格式/ADC/动力/姿态/显示） | 协议/数据专家 |
| `protocol.py` | 帧解析（按配置动态读，不硬编码字段名） | 协议/数据专家 |
| `telemetry_web/index.html` | 页面结构 + 全部 CSS | 前端专家 |
| `telemetry_web/app.js` | 全部前端逻辑（约 2500 行） | 前端专家 |
| `telemetry_web/assets/` | 字体、tailwind.js | 前端专家 |
| `build_web.py` | 打包流水线（`--check-only` 会跑步骤 0/1/2/3/4/4.5/4.6/4.7/4.75/4.8/4.85/4.86/4.88/4.9/4.10 共 15 步） | Qt/打包专家 |
| `一键打包.bat` | 双击打包入口 | Qt/打包专家 |
| `诊断_闪烁排查.bat` + `tools/gl_probe.py` | 图形后端现场排查（不用打包） | Qt/打包专家 |
| `tools/*.py` | 各类自检 / 探针脚本 | 只读参考 |
| `BUGS.md` | 问题台账 + 用户要求 + 教训 | 我维护 |
| `WORKFLOW.md` | 五阶段工作流 + 专家名册 | 我维护 |

---

## 3. ★ 用户的硬性要求（违反=任务失败）

| # | 要求 |
|---|---|
| R1 | **所有面向用户的文本用中文**（提示、注释、文档） |
| R2 | 项目路径必须英文（`F:\telemetry_v2`）；中文路径会让 PyInstaller onefile 无法创建临时目录 |
| R3 | **每次版本更新后同步到 git**（改完即提交，多行信息用 `git commit -F 文件`） |
| R4 | **处理任务前先读 `BUGS.md`**，恢复历史上下文 |
| R5 | 界面流畅（滚动/拖动跟手）与不闪烁**都要**，不接受"二选一" |
| R6 | **不要提前操作**，只完成明确交代的任务；一次任务只解决一个需求 |
| R7 | 有优化意见**只提建议**，不擅自实施 |
| R8 | **严禁把源码推送到 GitHub** |
| R9 | **用户修改的 config 文件就是默认配置**，提交时一并带上 |

> 另有一条**做法**（不是编号要求）：排查类改动**一次只改一个变量**，否则无法归因。
> ✅ 编号以 [BUGS.md](../BUGS.md) 第 1 节为唯一真源；2026-10-16 按用户要求删除了原来的
> R2「不许改前端界面布局」与 R6「一次只改一个变量」，其余条目重新连续编号。

---

## 4. 改一处值，有三个地方能改——谁说了算

> 这是"我改了源码也打包了，界面却纹丝不动"的根源。

```
① telemetry_user.json  ← 优先级最高，免打包，重启生效（exe 旁自动生成）
② telemetry_config.py  ← 出厂默认值，要重新打包
③ app.js 顶部 UI_CFG   ← 最低，仅演示模式用，连上后端就被 ② 覆盖
```

**排查口诀**：改了 ② 却不生效 → 先看 ① 是不是压着它（界面「设置 → 恢复为内置配置」可清）。

### ★ 两种"名字"（2026-10-16 用户踩过，必读）

| 名字 | 位置 | 改动后果 |
|---|---|---|
| **协议字段名**（`ROLL/PITCH/YAW/SPEED…`） | `telemetry_config.py` 第④⑤区字段宏 | CSV 表头 / 前端 payload 键 / 旧录播兼容一起断（静默）→ **只能是英文标识符** |
| **界面显示名**（横滚/车速/母线电压…） | 同文件第 ⑥ 区 `DISPLAY_NAMES` 宏表 | 只改界面文字 → 趋势图例 / 数值面板 / 总览卡片标题全线同步，**改这里就对了** |

`validate()` 会拦住"协议字段名写成中文"与"同包字段重名"；`web_ui_selfcheck` 有 6 条 `[显示名宏]` 断言盯着"改了显示名四处齐变"。

---

## 5. 已知的坑（别重踩）

| 坑 | 事实 |
|---|---|
| **闪烁（B10）** | 根因是**本机 Optimus 双显卡**（NVIDIA 渲染 + 核显输出）跨 GPU 拷贝与 Qt 合成不同步。**已用 `QSG_RHI_BACKEND=opengl` 解决**（`launcher_web.py` 的 `DEFAULT_GL_PRESET`）。**不要**再回到"GPU 开/关"那个死胡同——那是错的维度 |
| **高频元素过渡（B08）** | 数据 20 Hz 刷新（间隔 50 ms），所以高频元素的 CSS `transition` **必须 < 50 ms**；写 90 ms 会导致过渡永远跑不完 → 十几个条形同时"永远在颤" |
| **`content-visibility:auto`** | 在 GPU 合成下会让 Chromium"先闪骨架再画内容"；已从 `.zone` 移除，**不要加回** |
| **高频元素别加 `will-change`** | 常驻合成层反成负担（十几个条形各一张层） |
| **打包必须清缓存** | 否则出现"exe 是新的、界面是旧的" |
| **新增硬依赖要改三处** | `requirements.txt` + `build_web.py` 步骤 0 环境检查 + PyInstaller `--hidden-import` |
| **多行 commit 消息** | PowerShell 会拆坏 `-m`，用 `git commit -F 文件` |

---

## 6. 可用工具与验收命令（★ 均已实测可用）

> **三个环境陷阱（实测踩到，务必先看）**
>
> 1. **跑 QtWebEngine 相关脚本前设** `$env:QT_QPA_PLATFORM="offscreen"`（否则会真开窗口）。
>    （`launch_check.py` 等脚本内已自带 `setdefault`，但 `build_web.py` 走的外部脚本不一定）
> 2. **本机 shell 是 Windows PowerShell 5.1，不是 pwsh 7**：
>    读 UTF-8 文件（如 `telemetry/startup.log`、所有 .md）**必须**加 `-Encoding utf8`
>    或用 `[System.IO.File]::ReadAllLines($p,[System.Text.Encoding]::UTF8)`；
>    否则会输出乱码、并**把行数报错**（例：128 行被报成 106 行）。
> 3. **stderr 里大段 Chromium GPU ERROR 是正常噪声**，不代表失败：
>    `SharedImageManager::ProduceMemory: ... non-existent mailbox`、
>    `Failed to create GLES3 context, fallback to GLES2`、
>    `QFontDatabase: Cannot find font directory` 等。
>    **判定只看退出码 + 通过标志行**，别被红字吓到。
>
> Python 路径：`C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe`

| 验证什么 | 命令 | 通过标志 |
|---|---|---|
| **全量自检（首选）** | `python build_web.py --check-only` | `✅ 自检全部通过`（约 6 秒） |
| JS 语法 | `node --check telemetry_web/app.js` | 无输出、退出码 0 |
| Python 语法 | `python -m py_compile launcher_web.py` | 无输出、退出码 0 |
| 协议/配置自洽 | `python selftest.py` | `结果: 全部通过 ✅` |
| 前端 DOM/JS | `python tools/web_ui_selfcheck.py` | `全部通过` |
| 界面能起来 | `python tools/launch_check.py` | `RESULT: 启动正常，4 秒内无异常` |
| 趋势图三档量程 | `python tools/trend_mode_check.py` | `[OK] 三档量程均…` |
| CSV 列与数据对齐 | `python tools/column_order_check.py` | `[OK] 所有顺序组合下…严格一一对应` |
| 历史真帧回归 | `python tools/real_frame_regress.py` | `✅ 历史真帧全部仍能被正确解读` |
| 配置权威性 | `python tools/cfg_authority_audit.py` | `[OK] 全部通过` |
| **打包产物能跑** | 完整打包后启动 exe，看窗口是否出现 | 窗口 5 秒内弹出 |
| **当前图形后端** | 读 `telemetry/startup.log` 最后一行 | 含 `图形后端=qt-opengl` |

---

## 7. 交付要求（做不到就算没完成）

1. **改动 + 原始证据**：把你跑的命令和**原始输出**贴回来，不要只写"已完成"；
2. **不许改超出授权范围的文件**（任务里会写明你能改哪些）；
3. 改完必须自己先跑一遍上表对应的验收命令；
4. 有不确定的地方**说出来**，不要猜着做；
5. 用中文汇报。

---

## 8. 参考文档（注意时效）

| 文档 | 说明 |
|---|---|
| `上位机源码地图与修改打包指南.md` | 406 行详细开发指南（配置优先级 / scale / 打包链路讲得很透）。**⚠️ 路径写的是迁移前的 `F:\上位机\v2`，实际是 `F:\telemetry_v2`**；内容停在 v2.7，早于近期修复 |
| `BUGS.md` | 问题台账 B01~B11 + 教训 L1~L7（最新） |
| `WORKFLOW.md` | 五阶段工作流 + 专家名册（最新） |
| `需求与规划.md` | 前期需求记录 |
