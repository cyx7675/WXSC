---
name: telemetry-independent-qa
description: 对本遥测上位机的改动做独立质检与验收复验，或核实文档/交接包里的声明是否属实。当需要验证别人（或另一个 agent）声称"已完成/已修复"的工作、或需要在交付前做需求者视角验收时使用。内含反自欺的取证方法、会导致误判的环境陷阱、以及必须覆盖的边界与回归项。
whenToUse: 复验他人改动、交付前验收、核实文档声明、判断"是否真的修好了"时
---

# 独立质检：你的任务是**证伪**，不是确认

## 一、最高原则：不信任自述，只信证据

别人说"已修复 / 已通过 / 已完成"——**这不构成证据**。

你要做的是：
1. **自己跑一遍**，看**原始输出**（不是他的转述）；
2. **走不同的路径**——他跑自检脚本，你就去读源码、构造反例、试边界；
3. 找不到反例才叫"通过"，而不是"看起来没问题"。

**同一个 agent 自己验自己 = 没验。** 独立复验必须是**另一个人/agent**。

## 二、取证方法（照做）

| 他说 | 你要 |
|---|---|
| "自检全部通过" | 自己跑那条命令，看**退出码 + 通过标志行** |
| "改好了" | `git diff` 看实际改了什么，有没有改到不该改的文件 |
| "已验证" | 问**具体命令与原始输出**；没有就给不出结论 |
| "文档里写了 X" | **实际去查** X 是不是真的（文档最容易过期） |
| "设置已生效" | 找**可观测的落盘证据**（如 `telemetry/startup.log`），不要只看代码 |

## 三、★ 本机两个会导致**误判**的环境陷阱

这两个会让你的"核实"得出错误结论，必须避开：

### 1. 本机是 Windows PowerShell **5.1**，不是 pwsh 7

```powershell
# ✗ 错：按 ANSI/GBK 解码 UTF-8 文件 → 乱码 + 行数错报（128 行报成 106 行）
Get-Content telemetry\startup.log

# ✓ 对：
Get-Content telemetry\startup.log -Encoding utf8
[System.IO.File]::ReadAllLines($p, [System.Text.Encoding]::UTF8)
```
**曾因此误判**：把 128 行的文件报成 106 行，把正常内容报成乱码。

### 2. stderr 里大段 Chromium GPU ERROR 是**正常噪声**

```
SharedImageManager::ProduceMemory: Trying to Produce a Memory representation from a non-existent mailbox.
Failed to create GLES3 context, fallback to GLES2.
QFontDatabase: Cannot find font directory ...
```
全部**不影响结论**。**判定只看退出码 + 通过标志行**，别被红字吓到误报失败。

## 四、必查的六项

| 项 | 怎么查 |
|---|---|
| **需求者视角** | 用户要的那个东西，用户**自己能验证**吗？怎么验证？有没有"实现者以为好了、但用户看不到"的？ |
| **边界** | 无数据 / 断连 / 极值 / 首次运行 / 端口占用 / 空缓冲 |
| **回归** | 有没有引入新的闪烁、卡顿、崩溃？性能有没有退化？ |
| **约束** | 有没有违反用户硬性要求（见 [BUGS.md](../../BUGS.md) 第 1 节，现行 R1~R9）：中文界面、英文路径、不越界改文件… |
| **副作用** | 跑完验收命令后，工作区是否被污染？（`git status` 对比前后） |
| **覆盖反转** | 配置/环境里有没有"压着改动"的东西让验证结论无效？（如 `telemetry_user.json` 优先级高于 `telemetry_config.py`） |

## 五、报告格式（缺项视为未完成）

```
■ 结论：通过 / 有条件通过 / 不通过
■ 逐项证据：你跑的命令（原文）+ 原始输出（照抄，不要总结成"正常"）
■ 反例：具体哪个输入/操作会让它出问题（必须能复现）
■ 未覆盖：你没验到的部分（如实列出，不要装作验全了）
■ 不确定：无法确认的项，写"无法确认"而不是猜
```

**关键**：原始输出要**照抄**。把 `✅ 自检全部通过` 写成"自检没问题"就已经失真了。

## 六、验收命令速查（均已实测）

```powershell
$env:QT_QPA_PLATFORM="offscreen"           # 跑 Qt 相关脚本前设，否则真开窗口
python build_web.py --check-only           # 全量自检（15 步，约 6 秒）← 首选
python selftest.py                         # 协议/配置自洽
python tools/web_ui_selfcheck.py           # 前端 DOM/JS
python tools/launch_check.py               # 界面能起来
python tools/trend_mode_check.py           # 趋势图三档量程
python tools/column_order_check.py         # CSV 列与数据对齐
python tools/real_frame_regress.py         # 历史真帧回归
python tools/cfg_authority_audit.py        # 配置权威性
node --check telemetry_web/app.js          # JS 语法
```
读 `telemetry/startup.log` 最后一行可确认 exe 实际生效的图形后端。

## 七、最容易漏掉的一类问题

**文档与事实不符**。文档会过期、会写错日期、会高估自检步数、会漏记未提交改动。
核实文档时**逐条对着实际文件/输出比**——这类问题不影响功能，但会误导下一个接手的人。
