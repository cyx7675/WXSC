---
name: telemetry-frontend
description: 安全修改本遥测上位机的前端（telemetry_web/index.html 与 app.js）。当任务涉及页面渲染、CSS 样式、canvas 绘制、滚动/拖动性能、条形仪表、趋势曲线、交互 bug 时使用。内含本项目特有的渲染约束（过渡时长上限、禁用属性、滚动降频机制）与实测验收命令。
whenToUse: 需要改 telemetry_web/ 下的任何文件，或排查前端卡顿/闪烁/显示异常时
---

# 改这个项目的前端：先记住它和普通网页不一样

这是一个跑在 **QtWebEngine**（`file://` 加载）里的单页应用，数据以 **20 Hz** 灌进来。
普通网页的"随便写写 CSS"在这里会直接变成可见故障。

## 一、四条硬约束（违反必出故障）

### 1. 高频元素的 CSS `transition` 必须 **< 50 ms**

数据 20 Hz 刷新 ⇒ 每 **50 ms** 就是一个新值。

```css
/* ✗ 错误：90ms 的过渡在 50ms 就被打断，永远跑不完 */
.bar-fill { transition: transform .09s linear; }

/* ✓ 正确：40ms，在下一次更新前跑完 */
.bar-fill { transition: transform .04s linear; }
```

**症状**：十几个条形/指针同时"永远在颤"，看起来像闪烁。
**涉及元素**：`.bar-fill`、`.ladder .half`、`.tape .ptr`、`.gbar .pos/.neg`。

### 2. 高频元素**禁止** `will-change`

十几个条形各提升一张常驻合成层 = 净负担（软件光栅下尤其明显）。
只用 `transform` 做动画即可，浏览器会按需提升。

### 3. `.zone` **禁止** `content-visibility: auto`

GPU 合成模式下，Chromium 从"跳过渲染"切回"渲染"时会**先闪一下骨架**再画内容；
所有 zone 同时触发 = 整个界面闪白。

### 4. 版式：现状与事实（已无强制要求）

编号要求 R2「不许改前端界面布局」已于 2026-10-16 按用户要求删除，这里只记**事实**，
不再设"必须/不允许"：

- 现状：栅格容器是 `#stage`（`display:grid`，`grid-template-columns:repeat(6,minmax(0,1fr))`
  ⇒ **6 列**），`.zone` 按 `grid-column` 归属分区（如 `#zoneP2{grid-column:1/5}`、
  `#zoneP0{grid-column:1/7}` 跨满全宽）；底栏（`#footer`）固定 `flex:0 0 30px`。
- 已有证据：`tools/_footer_replay_probe.py` 的「零位移 A/B」断言、`tools/_width_reserve_check.py`
  的静态位移测量，能量出"新增/改动控件对既有元素的影响"——**要判断影响，就去跑它们拿数字**。
- 已知代价（改版式前值得知道）：改栅格归属/底栏高度，会让上述探针断言失效，需要同步重做基线。
- 结构改动仍受交付纪律约束：**改结构前立功能清单契约并逐条核**（见 `host-computer-development` 技能），
  以及 `app.js` 按 id 取节点 ⇒ **不许删改既有元素 id**。
- **2026-10-16 已达成的版面（用户点名删改，别当成漏删）**：
  `#zoneP2` 底部 `bandC` 两张分析图（`#dualCanvas`/`#scatterCanvas`）已删；
  `#zoneP0` 每卡迷你趋势线 `.a-spark` 已删（`.a-val` 17→19px，`.acard` 改两列）；
  `#zoneSettings` 右侧只读信息列 `.set-info` 已删（设置区单列）；
  录制计时改 `REC HH:MM:SS`（回放 `#replayPos` 仍是 `MM:SS`）。
  实测 8 档宽度（1440→520）**无横向溢出**，`#stage` 滚动高 1897→1734px。

## 二、已有的性能机制（别破坏）

主循环里已经做了这些，改动时不要绕过或删掉：

| 机制 | 位置 | 作用 |
|---|---|---|
| `IntersectionObserver` | `initZoneVisibility()` | 只刷视口内的分区 |
| 滚动降频 | 主循环 `rolling` 分支 | DOM → 6 Hz、趋势 → 10 fps |
| 滚动期间跳过 canvas 重绘 | 主循环 `if (!rolling)` | 把 GPU 让给滚动合成 |
| 静态层缓存 | `trendLayer.sKey` | 网格/刻度不每帧重算 |
| 脏值比对 | `updateDom()` | 数字没变就不写 DOM |

## 三、改之前先搞清数据是怎么来的

```
串口线程 → protocol.parse_stream → Backend._handle → _pending
   → QTimer _flush（20 Hz 批量） → dataReady → QWebChannel
   → app.js onDataBatch → pushSample → state.buf → requestAnimationFrame 绘制
```

关键点：**数据是批量进、按帧画的**。不要在 `onDataBatch` 里直接操作 DOM
（那是每批一次，可能一批多帧）；要写进缓冲，交给主循环。

## 四、验收（改完必须自己跑）

```powershell
node --check telemetry_web/app.js
python build_web.py --check-only
python tools/web_ui_selfcheck.py
```

> 环境提醒：本机是 **Windows PowerShell 5.1**。
> 读 UTF-8 文件必须加 `-Encoding utf8`，否则乱码且行数错报。
> stderr 里的 Chromium GPU ERROR 是正常噪声，**只看退出码和通过标志行**。

## 五、排查性能问题时的正确姿势

1. 先用 `tools/scroll_probe.py` / `tools/perf_bench.py` 量出**数字**，不要凭感觉；
2. 一次只改一个变量（做法，编号要求已于 2026-10-16 删除），否则无法归因；
3. 特别注意"改了但没生效"的经典陷阱——见 `telemetry-protocol` 技能里的配置优先级。

## 六、常见误判

| 现象 | 真因 |
|---|---|
| 界面闪白 | `content-visibility:auto`；或 GPU 后端问题（见 `telemetry-qt-packaging`） |
| 条形一直在颤 | 过渡时长 > 50 ms |
| 滚动卡顿 | 滚动期间还在重绘 canvas；或 `will-change` 滥用 |
| 曲线贴底/爆表 | 量程问题，**不用改代码**，改 `telemetry_user.json` 或界面「设置 → 量程与单位」 |
