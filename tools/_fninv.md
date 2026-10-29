# 功能清单（验收契约）· `tools/_fninv.md`

> **用途**：用户已裁定「**可以修改前端布局，但不能丢失原有功能**」。
> 本文件把现有界面上**所有能用的东西**穷举为可勾选清单，作为布局重做/组件重构的验收契约。
> 验收方式：`python tools/_fninv_check.py`（**只看退出码**：0=全 PASS，非 0=有 FAIL）。
> ⚠ **本脚本不在打包流水线里**：`build_web.py --check-only` 的步骤 3~4.10 **没有**调用
> `tools/_fninv_check.py`（它只跑 py_compile / `node --check` / `selftest.py` / `record_check.py` /
> `config_matrix_audit.py` / `scale_mutate_check.py` / `offset_mutate_check.py` / `trend_mode_check.py` /
> `column_order_check.py` / `zero_mutate_check.py` / `real_frame_regress.py` / `user_cfg_roundtrip.py` /
> `cfg_authority_audit.py`）。所以本契约**必须手动跑**，别以为过了 `--check-only` 就过了这份清单。
> 状态（2026-10-16）：`_fninv_check.py` 目前也没有被任何其它脚本调用（全仓 grep `_fninv_check.py` 只命中它自己）。
>
> **清单锚定的基线**：`HEAD = bdce1ef`，`telemetry_web/app.js` sha `88F53E7C4742`、
> `telemetry_web/index.html` sha `42CF30781378`（**仅只读采集，未改动任何产品文件**）。
> 说明：采集时 `app.js` 处于 `M`（frontend 正在改），`index.html` 未改。若布局重做后清单条目对不上，
> **以本清单为"原有功能"的判据**，而不是以新布局为准。

## 图例

* **现有验证**：`探针名` = 已有自动断言；`部分` = 只验到侧面；`无` = **没有任何自动验证**。
* **优先级**：P0 = 核心/用户明确要求过；P1 = 常用；P2 = 边缘。
* **★** = 布局改动最可能弄丢（P0/P1 且无自动验证）→ 已由 `_fninv_check.py` 覆盖。

---

## A. 顶栏 · 全局（常驻，任何分区都在）

| 编号 | 功能 | 入口 | 预期行为 | 现有验证 | 优先级 |
|---|---|---|---|---|---|
| F01 | 左侧导航切换分区 | `.rail-item` ×3（总览 / 可视化 / 串口）click | 总览=显示除 `zoneVisual` 外全部；可视化=只显示 `zoneVisual`；串口=只显示 `zoneSerial`；`stage.scrollTop` 归零；被点项加 `.active`+`aria-current`，其余清除 | `web_ui_selfcheck`（只验分区存在）；`_ux*` 截图 | **P0 ★** |
| F02 | 连接状态灯 | `#livePill` / `#liveDot` / `#liveText` | 未连接/在线/演示三态文案+颜色 | `_qa_edge_probe`（setLive 部分） | P1 |
| F03 | 收帧计数 | `#rxTag`（顶栏） | 显示接收字节/帧计数，位宽锁定不推动相邻控件 | `_width_reserve_check`（F4） | P1 |
| F04 | 录制开关胶囊 | `#recPill`（`role=button tabindex=0`）click / Enter / Space | 切换录制；`#recText` 文案与 `#recDot` 状态同步；`title` 提示 | **无** | **P1 ★** |
| F05 | 冻结/暂停开关 | `#freezeBtn`（`role=button tabindex=0`）click / Enter / Space | 切 `state.frozen`；`#freezeText` 在「暂停/继续」间切换；`#pauseTag` 显隐；toast 提示 | **无**（`_replay_bar_check` 只验后端信号路径） | **P0 ★** |
| F06 | 时钟 | `#clock` | 每秒刷新 `HH:MM:SS` | 无 | P2 |
| F07 | 提示条 | `#toastWrap`（`role=status aria-live`） | 弹出提示；点按即关；到时自动消失并移除 DOM | **无** | P1 ★ |

## B. `#zoneP2` 动力与行驶（8 组仪表；原先的 2 张分析图已按用户要求删除）

> **删除记录（2026-10-16，按用户要求）**：本区底部整块 `div.band.bandC` 已删除 —— 里面只有两张分析卡：
> `#dualCanvas`（转速×车速 双轴图）与 `#scatterCanvas`（功率×电流 散点图）。连带删除的是
> `index.html` 的 `.bandC{...}` / `.bandC > .xcard{...}` / `.mini-cv{...}` / `.bandC > *` 那一项，
> 以及 `app.js` 的 `paintMini()` / `dualLayer` / `scatterLayer` / `miniDirty` / `lastMini` / `MINI_FPS`。
> **本区的仪表与数值一个都没动**（F10 车速环 / F11 转速表+换挡灯 / F12 功率+峰值 / F13 五组条）。
> 契约同步：`_fninv_check.py` 的 `NEED_HTML_IDS` 如实移出这两个 id（因此 `A_ids_missing` 由 104 项变 102 项、
> `A_ids_collapsed` 少掉针对它们的塌陷探测）；除此之外**没有任何断言被放宽**。

| 编号 | 功能 | 入口 | 预期行为 | 现有验证 | 优先级 |
|---|---|---|---|---|---|
| F10 | 车速环形表 | `#speedGauge/#speedNum/#speedUnit/#speedBar` | 数值/单位/环填充 + 超速热区 | `_e2e_round2`（读数） | P0 |
| F11 | 转速表 + 转速段 | `#rpmGauge/#rpmNum/#revRow/#rpmBar/#rpmLimitTick` | 读数 + 换挡灯分段点亮 | `_d1d2d5_check`（D1 指针/D2 字号） | P0 |
| F12 | 电池功率派生 + 峰值清零 | `#pwrCard/#pwrNum/#pwrBar/#pwrPeak` click | 功率=volt×amp/1000；**点击峰值归零**；阈值告警色 | `_pwr_check`（数值/告警）；点击归零 **无** | P1 ★ |
| F13 | 油门/扭矩/温度/电压/电流条 | `#thr*/#tq*/#temp*/#volt*/#amp*` 各自 `Bar/Num/Unit/Scale/Heat` | 数值+单位+条填充+量程刻度+热区 | `_ring_fit_check`/`_d1d2d5_check` 部分 | P0 |
| ~~F14~~ | ~~双轴图 / 散点图~~（原 `#dualCanvas/#scatterCanvas`，`#dualCap` 标题） | — | **已按用户要求删除（2026-10-16）** | 已删除，不再验收 | ~~P1~~ |

## C. `#zoneP1` 姿态与惯性

| 编号 | 功能 | 入口 | 预期行为 | 现有验证 | 优先级 |
|---|---|---|---|---|---|
| F20 | 横滚/俯仰/偏航读数与指针 | `#rollNum/#rollNeedle/#pitchNum/#pitchUp/#pitchDn/#yawNum/#yawPtr` | 读数 + 指针角度随值变化 | `_d1d2d5_check`（指针可见性） | P0 |
| F21 | 加速度读数 + 正负条 | `#accNum/#accNeg/#accPos/#accChip/#accScaleLo/#accScaleHi` | 读数 + 双向条 + 系数 chip + 量程刻度标签 | `_accvec_check` 部分 | P0 |
| F22 | 加速度矢量图 | `#accVec/#accVecCanvas` | 同心圈+箭头+合成幅值圈；超满量程夹紧并提示；读数 `#accVecLat/Lon/Mag`、`#accVecNote` 同源 | `_accvec_check` `_accvec_move_check` `_accvec_scale_check` | P0 |
| F23 | 矢量图悬停即刻重绘 | `#accVecCanvas` mousemove / mouseenter | 暂停时悬停也能刷新（离散入口） | `_accvec_move_check` | P1 |

## D. `#zoneP0` 10 路 ADC

> **删除记录（2026-10-16，按用户要求）**：每张 ADC 卡片内的**迷你趋势线 `.a-spark`** 已删除
> （`app.js` 的 `buildAdcCards()` 里那段内联 SVG、`updateAdcDom()` 里重建 path 的
> `SPARK_FLAT` / `card._spark` / `doSpark` / `lastSparkT`，以及 `index.html` 的 `.a-spark` 两处 CSS）。
> Lead 会重排卡片排版。**F30 保留并继续受验收的卡片元素**：`#adcGrid` 与每卡
> `.a-ch` / `.a-name` / `[data-val]` / `.a-unit`；验收口径**不变**（子卡片数 == `CFG.ADC_COUNT`，
> 读数 / 单位 / 条形仍在）——`_fninv_check.py` 没有任何与 F30 相关的断言被放宽。

| 编号 | 功能 | 入口 | 预期行为 | 现有验证 | 优先级 |
|---|---|---|---|---|---|
| F30 | ADC 卡片网格 | `#adcGrid`（子卡片数 == `CFG.ADC_COUNT`） | 每卡：通道名/单位/读数/条形（原迷你趋势线 `.a-spark` 已按用户要求删除）；12bit 解码顺序可配 | `web_ui_selfcheck`（adcCards==CFG.ADC_COUNT） | P0 |
| F31 | ADC 卡片随配置改名/改量程 | 运行期 `uiConfig` 推送 | 卡片名/单位/量程跟随变 | `_vischan_sync_check` `verify_cfg_sync` | P1 |

## E. `#zoneTrend` 趋势区（★ 布局重做的主战场）

| 编号 | 功能 | 入口 | 预期行为 | 现有验证 | 优先级 |
|---|---|---|---|---|---|
| F40 | 三档量程循环 | `#normBtn` click | 归一化→自适应→分组分带→归一化；按钮文案 `#normBtn` 跟随；`aria-pressed` | `trend_mode_check` | P0 |
| F41 | 曲线通道开关 | `#chipBar` 内 `.chip`（`role=button tabindex=0`）click / Enter | 点击切换该通道显隐；`.chip.on` 类同步；行点击（右侧面板）等效 | `web_ui_selfcheck`（chips==23）仅数量 | **P0 ★** |
| F42 | 趋势曲线绘制 | `#trendCanvas` | 20Hz 更新；三档模式各自画法；空数据提示文案 | `_e2e_round2` `trend_mode_check` | P0 |
| F43 | 悬停十字光标 + 数值气泡 | `#cursorCanvas` / `#trendCanvas` mousemove/mouseleave | 悬停显示该时刻各通道数值；移出清除；**暂停时也能看** | `_trend_interact_probe` `_task8_check` | P1 |
| F44 | 拖拽框选区间统计 | 趋势画布上 drag | 框选后显示区间统计 `#selStats`；移出/点击清除 | `_trend_interact_probe` | P1 |
| F45 | 右侧数值面板 | `#sigPanel`（mouseover / mouseleave / click / keydown） | 列出当前值；点击切换「只看该通道」高亮；键盘可达 | `_task8_check` 部分 | P1 |
| F46 | 暂停角标 | `#pauseTag` | 冻结时显示（非 0 尺寸），恢复后隐藏 | `_fninv_check` A_pauseTag_on_freeze | P2 |

## F. `#zoneVisual` 可视化工作台

| 编号 | 功能 | 入口 | 预期行为 | 现有验证 | 优先级 |
|---|---|---|---|---|---|
| F50 | 新增图表 | `#addChartBtn` click | `#visGrid` 多一张图表卡片（含 `.vc-del`/`.vc-sigs`/`.vc-canvas` + `.vc-empty`「拖入数据通道」空态） | `_fninv_check` G_add_chart / G_card_parts | **P1 ★** |
| F51 | 删除图表 | 卡片内 `.vc-del` click | 该卡片移除，其余保留 | `_fninv_check` G_del_chart | **P1 ★** |
| F52 | **把通道拖进图表** | `#visChannels` 的 `.vc-item[draggable=true]` dragstart → 卡片 dragover/drop（`_fninv_check` 也验 dragstart 写入的 id） | 拖到卡片上后该图多一条曲线；dragover 时卡片加高亮类 | `_fninv_check` G_channels_draggable / G_dragstart_sets_data / G_drop_adds_sig | **P1 ★** |
| F53 | 把信号移出图表 | 卡片内 `.vc-sig` click | 该信号从这张图移除（`.vc-sig` 数量 −1） | `_fninv_check` G_sig_click_removes | **P1 ★** |
| F54 | 通道列表与配置同步 | `#visChannels` 内条目 | 随 `uiConfig`（改名/增删/换序）同步 | `_vischan_sync_check` `_u08_sensitivity_check` `web_ui_selfcheck`(U08) | P0 |
| F55 | 图表悬停读数 | `.vc-canvas-wrap` mousemove/mouseleave | 悬停显示该图各曲线数值（暂停/无数据流动时交互自己补画） | `_task8_check`（问题五） | P1 |

> **★ 读清单时注意**（我第一版写错过，读代码后更正）：图表卡片**没有**"卡片之间拖拽换位"这个功能；
> `dragstart` 挂在**通道列表项**上，卡片只做 `dragover/dragleave/drop` 接收端。
> 新建的卡片 `chart.sigs` 初始为空 → **`.vc-sigs` 里一开始没有 `.vc-sig` 是正常的**，
> 必须先拖入一个通道才会出现。凡是"新卡片应当有 .vc-sig"的断言都是错的。

## G. `#zoneSerial` 串口助手

| 编号 | 功能 | 入口 | 预期行为 | 现有验证 | 优先级 |
|---|---|---|---|---|---|
| F60 | 端口列表刷新 | `#refreshPortsBtn` click / `#portSelect` | 刷新并填充下拉 | **无** | P1 ★ |
| F61 | 波特率选择 | `#baudSelect` | 提供常用波特率，选中值参与连接 | **无**（`launch_check` 只验启动） | P1 ★ |
| F62 | 连接/断开 | `#connectBtn` click；`#connDot/#connText` 反馈 | 未连接→连接；已连接→断开；状态点/文案同步 | **无** | P0 ★ |
| F63 | HEX 显示区 | `#hexBox`（`#hexIdle` 空态） | 增量刷新、自动滚到底、可拖高（B07） | `_hex_bench` `_hexbox_fix_candidates` 部分 | P1 |
| F64 | 文本/HEX 发送 | `#cmdInput` + Enter、`#sendBtn` click、`#cmdMode` 选择 | ASCII 直发；HEX 按 `55 AA 01` 解析；空输入不报错 | **无** | **P1 ★** |
| F65 | 串口区零跳动 | 喂数据时 | 相邻元素零位移 | `_serial_jitter_probe` | P1 |

## H. `#zoneRecord` 录制 / 导出 / 回放

| 编号 | 功能 | 入口 | 预期行为 | 现有验证 | 优先级 |
|---|---|---|---|---|---|
| F70 | 开始/停止记录 | `#recBtn` click；`#recMeta` 状态 | 文案与状态同步；停止后写 `recordSaved` 提示 | `record_check`（后端链路） | P0 |
| F71 | 保存目录 | `#saveDirInput`（只读）/`#browseDirBtn`/`#openDirBtn` | 浏览选目录并回显；打开资源管理器定位 | **无** | P1 ★ |
| F72 | 导出 CSV | `#exportBtn` click | 有后端→复制录制文件；无后端→下载屏幕缓冲快照 | `_task17_check`（列序/表头），按钮接线 **无** | P0 ★ |
| F73 | 数据回放 | `#replayBtn` click | 弹选文件框→分片加载（loading 文案）→按帧节拍推送 | `_e2e_loading` `_e2e_realdata` | P0 |
| F74 | 录制日志列表 | `#logList` click | 列出历史录制；点击可定位/打开 | **无** | P2 ★ |
| F75 | 连接后自动录制 | `#autoRecord` checkbox | 勾选后连接即开始录 | **无** | P2 ★ |

## I. 底栏 + 回放进度条（`#footer`）

| 编号 | 功能 | 入口 | 预期行为 | 现有验证 | 优先级 |
|---|---|---|---|---|---|
| F80 | 回放进度条拖动 | `#replayRange`（range）`input`→live seek、`change`/`mouseup`/`touchend`/`blur`→全量 seek | 拖动中低开销 `seekReplayLive`；松手补发一次 `seekReplay` 且用最终位置；未拖动时全局 mouseup 不误发 | `_replay_bar_check` `_replay_e2e_check` | P0 |
| F81 | 回放暂停/继续 | `#replayPauseBtn` click | 切换 `replayPausedChanged`；按钮文案/禁用态同步；无该信号的后端用本地乐观切换 | `_replay_bar_check` `_footer_replay_probe` | P0 |
| F82 | 位置/总时长 + 加载文案 | `#replayPos` | 非回放显示 `— / —`；回放显示 `cur / total`；**加载中显示"正在解析… N 帧"** | `_task17_check` `_e2e_loading` | P1 |
| F83 | 帧率/接收计数 | `#fpsTag` `#rxFoot` | 计数位宽锁定，不推动相邻控件 | `_serial_jitter_probe` `_width_reserve_check` | P1 |

## J. `#zoneSettings` 设置

> **删除记录（2026-10-16，按用户要求）**：本区右侧**只读信息列** `div.set-col.set-info`
> （4 行 `.kv`：配置来源 / 生效方式 / 界面量程·单位·位数 / 数据契约）及其 CSS
> （`.set-info{border-left:1px solid var(--line);padding-left:18px}` 与小屏媒体查询里的那一条）已删除。
> 它是**纯展示、无 id、无交互**的元素，本来就不在 `NEED_HTML_IDS` 里，所以 `_fninv_check.py` **无需改动**。
> **本区保留并继续受验收**：`#demoMode`（F90）、`#autoRecord`（F75）、`#bufInput`（F91）——三者都在
> `NEED_HTML_IDS` 里，仍然逐个受存在性 + 塌陷 + 真交互断言覆盖。

| 编号 | 功能 | 入口 | 预期行为 | 现有验证 | 优先级 |
|---|---|---|---|---|---|
| F90 | 演示模式开关 | `#demoMode` checkbox change | 开→模拟数据+状态灯"演示"；已连接时拒绝并回弹勾选；关→停模拟清场 | `_demo_accel_check` `_qa_edge_probe` | P1 |
| F91 | 曲线缓冲点数 | `#bufInput` change | 写入配置并即时改 `BUF`；越界值被夹紧 | `_clamp_check`（夹紧） | P1 |

## K. 通用交互（跨分区，最容易被"重排"弄丢）

| 编号 | 功能 | 入口 | 预期行为 | 现有验证 | 优先级 |
|---|---|---|---|---|---|
| F95 | 分区拖高手柄 | **运行时注入**的 `.zone-resize`（每个 `.zone` 底部一个）mousedown+拖动 | 拖动改变该区高度；最小 140px；拖动中补画陈旧位图（防闪屏） | `_layout_flash_probe`（闪屏/几何）——**手柄本身与拖高行为无** | **P0 ★** |
| F96 | 页面滚动性能标记 | `#stage` scroll | 滚动中给 body 加 `.scrolling` 并降频重绘 | `scroll_probe` `scroll_bench` | P1 |
| F97 | 分区可见性观察 | `IntersectionObserver`（root=#stage） | 驱动分区级节流（不可见不画） | 间接（`_accvec_move_check`） | P1 |
| F98 | 键盘可达性 | 所有 `role=button` 元素 Enter/Space | REC/冻结/通道 chip/导航均可键盘操作 | **无** | **P1 ★** |

## L. 前端↔后端契约（`launcher_web.py`，18 Signal + 22 Slot）

后端→前端 Signal：`dataReady` `rawReady` `statusChanged` `errorMsg` `portsReady` `saveDirReady`
`recordStateChanged` `recordSaved` `recordFailed` `recordFileReady` `saveResult` `cfgSaved`
`recordProgress` `uiConfig` `replayStateChanged` `replayProgress` `replayLoadingChanged`
`replayPausedChanged` `replayBackfill`

前端→后端 Slot：`refreshPorts` `chooseSaveDir` `setSaveDir` `getSaveDir` `getUiConfig`
`saveUserCfg` `resetUserCfg` `startRecording` `stopRecording` `isRecording` `chooseReplayFile`
`startReplay` `stopReplay` `setReplayPaused` `seekReplay` `seekReplayLive` `saveTextFile`
`exportRecording` `showInExplorer` `connect` `disconnect` `send`

| 编号 | 功能 | 入口 | 预期行为 | 现有验证 | 优先级 |
|---|---|---|---|---|---|
| F99 | 后端信号/槽契约 | `launcher_web.py` | 上述 19 个 Signal + 22 个 Slot 全部存在（前端按名取值，少一个就静默失效） | `_fninv_check.py` K 段 | **P0 ★** |
| F100 | 数据链路 | `dataReady`→`onDataBatch`→`pushSample`→环形缓冲→主循环 | 每批 20Hz 落到缓冲并绘制 | `_e2e_round2` `web_ui_selfcheck` | P0 |

> **注**：后端另有 5 个 **UI 未接线** 的槽（`setSaveDir`、`saveUserCfg`、`resetUserCfg`、`isRecording`、
> `saveTextFile`）与 2 个前端未监听的信号（`recordFailed`、`recordFileReady`）—— 属"已实现但当前界面没用到"，
> **不算现有功能**（布局改动即使不动它们也不影响用户能用什么），但**不要顺手删掉**（有探针/后端内部依赖）。
> `recordFailed` 的失败提示实际由 `errorMsg` 送达。

---

## 一、目前**没有任何自动验证**的功能（★ 最危险缺口）

> **状态（2026-10-16）**：下表中标 ✅ 的条目**已由 `tools/_fninv_check.py` 补上自动校验**（54 PASS / 0 FAIL，
> 详见第三节）；未标 ✅ 的仍是纯人工/无验证。**这个"缺口"列应当随每次改动更新。**

| 排序 | 编号 | 功能 | 为什么最危险 | 现状 |
|---|---|---|---|---|
| 1 | **F95** | 分区拖高手柄 `.zone-resize` | 手柄是**运行时由 `initZoneResize()` 注入**的。若布局重做把 `.zone` 换类名/换容器，手柄会**静默消失**（不报错、不留痕），而这是 B07 用户明确要求过的功能 | ✅ 已补（含真拖动改高度） |
| 2 | **F01** | 导航切换 + active/aria | 依赖 `allZones` 数组与 `.hidden` 类名硬编码；重排后极易漏一个分区或漏 `zoneVisual` 例外 | ✅ 已补 |
| 3 | **F05** | 冻结按钮（点击 + 键盘） | P0 且只有后端路径被测过；按钮本身的事件接线无验证 | ✅ 已补（click/Enter/Space） |
| 4 | **F41** | 曲线通道开关 chips | 只有"数量==23"被验；点击切换 `.on`/显隐、键盘操作无验证 | ✅ 已补 |
| 5 | **F04** | 录制胶囊 recPill | 点击/键盘切换录制无验证 | ✅ 已补 |
| 6 | **F62** | 连接/断开按钮 | P0 且行为无验证（只验过启动） | ✅ 已补（有反馈即接线） |
| 7 | **F50/F51/F52/F53** | 可视化区 增/删图表 + 拖通道进图 + 移出信号 | 全靠 `visGrid` 动态构建，重排容易丢 | ✅ 已补（含真实 DragEvent） |
| 8 | **F64** | 命令发送（按钮 + Enter + ASCII/HEX） | 无验证 | ✅ 已补（空输入安全性 + 非空有反馈） |
| 9 | **F72** | 导出 CSV 按钮接线 | 只验过表头内容，没验过按钮是否还接着后端 | ✅ 已补 |
| 10 | **F98** | 键盘可达性（所有 `role=button`） | 完全无验证，重排后 Enter/Space 最易失效 | ✅ 已补（pill 三键 + chips 带 tabindex） |
| 11 | **F12** | 功率峰值点击清零 | 数值被验、点击归零没验 | ✅ 已补 |
| 12 | **F60/F61** | 端口刷新 / 波特率下拉 | 无验证 | ✅ 已补（存在性+可交互+选项数） |
| 13 | **F71/F74/F75** | 保存目录 / 录制日志 / 自动录制 | 无验证 | 部分 ✅（浏览按钮接线已补；日志/自动录制仍待补） |
| 14 | **F07** | 提示条点击关闭 | 无验证 | ✅ 已补（弹出 + 点击后真被移除） |
| 15 | **F99** | 后端 Signal/Slot 契约 | 无静态契约校验（删一个槽前端静默失效） | ✅ 已补（ast 解析 19+22） |
| 16 | **F02/F03/F06/F13/F20/F21/F30/F40/F42/F43/F44/F60…** | 状态灯/计数/时钟/仪表/曲线/悬停/框选… | 各有专用探针，但**不是契约级**（探针可被跳过） | 已有专用探针，见第五节 |
| 17 | **F11/F13/F20/F21 的"读数与指针"细节** | 环形表指针、字号稳定、热条 | 有 `_d1d2d5_check`/`_ring_fit_check` | 已有专用探针 |

## 二、补的最小校验（`tools/_fninv_check.py`）

**覆盖上面 P0/P1 且此前无自动验证的条目，逐项 PASS/FAIL，退出码 = FAIL 数**（0=全绿）。
手段以"真渲染 + 真事件"为主，静态检查只作兜底：

| 检查段 | 覆盖编号 | 手段 |
|---|---|---|
| `A_ids_missing/dup/collapsed` | 全部入口 | 真渲染后断言 **102** 个 id 齐全、无重复、**不得塌成 0×0**（布局丢失探测）。<br>2026-10-16 前为 104 个：`dualCanvas`/`scatterCanvas` 随 bandC 删减如实移出；**其余 102 个一个没减**，塌陷探测仍逐个覆盖剩下的每个元素 |
| `A_fills_grow` | F10-F13 F20-F21 | 先灌一批真机形态数据，再断言 11 个**百分比填充分条**确实长出来了 —— "重排把条的容器改窄/改 0"这类问题只有有数据时才现形 |
| `A_pauseTag_on_freeze` | F46 | 冻结→角标必须出现；恢复→必须隐藏 |
| `B_nav_click` | F01 | 真点击 3 个 rail-item，断言分区显隐集合 + `.active`/`aria-current` |
| `C_recPill_*` / `C_freeze_*` | F04 F05 F98 | 真 click + 真 keydown(Enter/Space)，断言 `recDot/recBtn/aria-pressed`、`state.frozen/freezeText` 同步且**可逆** |
| `D_chips_*` / `D_normBtn_cycle` / `D_sigPanel_interact` | F41 F40 F45 | 点击后按 `data-id` 重新取节点断言 `.on`；三档文案循环；面板 mouseover 设高亮 + click 切通道 |
| `E_resize_handles` / `E_resize_drag` | F95 | 断言每个 `.zone` 都有手柄；**真 mousedown+mousemove** 且让 rAF 确定性落地，断言该区高度确实变大 |
| `F_range_attrs` / `F_init_disabled` / `F_loading_text` / `F_replay_enable` / `F_pos_reset` | F80 F81 F82 | 属性、初始禁用、加载文案与进度回包、回放态启用、复位 |
| `G_add_chart` / `G_card_parts` / `G_channels_draggable` / `G_dragstart_sets_data` / `G_drop_adds_sig` / `G_sig_click_removes` / `G_del_chart` / `G_vis_channels` | F50 F51 F52 F53 F54 | 真 `DataTransfer` + `DragEvent` 走完 dragstart→drop→移除信号全链路 |
| `H_serial_controls` / `H_connect_wired` / `H_send_empty_safe` / `H_send_wired` / `H_export_wired` / `H_replay_wired` / `H_browse_wired` / `H_pwr_peak_click` / `H_demo_toggle` / `H_buf_change` | F62 F64 F72 F73 F71 F12 F90 F91 | 真点击后断言"有可观察反馈"（浏览器模式下的提示/数值变化），空输入必须无副作用 |
| `I_toast_show` / `I_toast_click` / `I_toast_dismiss` | F07 | 唯一标记 → 弹出可见 → 点击 → 下一拍确实被移除 |
| `F-js-func/rpc/sig` | 全量 | 静态断言 `app.js` 仍含 21 个关键函数、16 个后端 RPC、17 个信号监听（重写时被删就 FAIL） |
| `F99-signals/slots` | F99 | `ast` 解析 `launcher_web.py`，断言 19 Signal + 22 Slot 齐全 |
| `F100` / `F98` | F100 F98 | 主循环 `requestAnimationFrame(render)`、`bindPillKey`+keydown 仍在 |
| `K_no_js_errors` / `K_console` | — | 上述全部真交互后 `window.__errs` 为空、控制台无 ERROR |

**刻意的设计取舍（避免误报）**：
* 无数据时百分比填充**天然为 0**，所以尺寸断言一律**先灌数据**；
* `#toastWrap`（空容器）、`#pauseTag`、`#selStats`（条件显示）排除在"0 尺寸=丢功能"之外，
  但 `pauseTag` 另配行为断言（冻结时必须出现）；
* 拖高测试不让 rAF 随机：探针内临时接管 `requestAnimationFrame` 并同步 flush，
  **只作用于探针页面，不动产品代码**。
* 后端 5 个"UI 未接线"槽（`setSaveDir/saveUserCfg/resetUserCfg/isRecording/saveTextFile`）与
  2 个前端未监听信号（`recordFailed/recordFileReady`）**不算契约缺失**，只断言它们"仍在后端存在"。

## 三、原始输出（2026-10-16，HEAD `bdce1ef`，product-sha `app.js a285ff025798` / `index.html 99e19241ef3b`）

```
$ $env:QT_QPA_PLATFORM="offscreen"; python tools/_fninv_check.py
基线: HEAD bdce1ef   product-sha(app.js/index.html) = a285ff025798 / 99e19241ef3b
结果: 54 PASS / 0 FAIL
EXIT=0
```
（完整 54 行输出见交付消息；关键几行的证据值：
`A_ids_missing` 104 个 id 全部存在；`A_fills_grow` 11 项有数据后均非 0；
`E_resize_drag  [zoneP2 611px -> 691px, inline=691px, 本轮 rAF 回调=1]`；
`D_normBtn_cycle  labels=["归一化量程","自适应量程","分组分带","归一化量程"]`；
`F99-signals` 19 个 / `F99-slots` 22 个；`K_no_js_errors` 空。）

> ⚠ **这次通过是在 `app.js` 正被 frontend 改动的过程中取得的**（采集期 sha 从 `88F53E7C4742`
> 变到 `a285ff025798`）。布局重做**落地后必须重跑**；届时若有 FAIL，就是契约抓到了丢失的功能。

## 三·补、产品删减后的契约同步 + 复跑（2026-10-16）

**背景**：用户裁定删除 ① `#zoneP2` 底部整块 `bandC`（`#dualCanvas` 双轴 / `#scatterCanvas` 散点）、
② 每张 ADC 卡片里的迷你趋势线 `.a-spark`、③ `#zoneSettings` 右侧只读信息列 `.set-info`、
④ 录制计时改 `REC HH:MM:SS`（回放 `#replayPos` 的 `MM:SS / MM:SS` 不变）。

**本次只做"移除针对已删元素的那几条"**：

| 契约文件 | 改动 | 为什么只改这些 |
|---|---|---|
| `tools/_fninv_check.py` | `NEED_HTML_IDS` 移出 `dualCanvas`、`scatterCanvas`（原第 84 行），并加注释说明 | 宿主整块 bandC 已删；`A_ids_missing` 104 → **102** 项、`A_ids_collapsed` 少掉针对这 2 个 id 的塌陷探测。**没有其它任何条目被放宽/删除** |
| `tools/_fninv.md` | F14 标注「已按用户要求删除（2026-10-16）」+ 保留编号（**不重新编号**）；B/D/J 三区加删除记录；`A_ids_*` 的 104 → 102；第 16 行缺口表的 `F14` 划掉 | 契约文档如实反映现状；被删功能不再算验收目标，也不编造新条目 |
| `tools/_t30_bars.js` | **只加注释**（无逻辑/输出结构改动）：`.bandC` 已删 → 取不到元素时 `R()` 返回 `null`，`out.bands` 记成 `{"band":".bandC","rect":null}` 是"该带确已消失"的预期证据；`.xcard` 选择器保留作防御性分组 | 它不引用任何被删 **id**，且 `R(null)` 本来就安全返回 null，`_t30_measure.py`/`_t30_grab_real.py` 都用 `.get()` 读取，不会崩 |
| `tools/_e2e_round2.py` | **无需改动**（已全文件核对） | 不含 `dualCanvas/scatterCanvas/dualCap/a-spark/data-spark/set-info/bandC/xcard/mini-cv/paintMini/miniDirty/MINI_FPS/SPARK_FLAT` 任一；只引用未变的 `#replayPos` |

**强度自证 —— 没有被放宽的断言（逐项点名）**：`A_ids_missing`（102 个 id 全存在）/ `A_ids_dup` /
`A_ids_collapsed`（剩下每个元素仍逐个查 0×0）/ `A_fills_grow`（11 项填充条）/ `A_pauseTag_on_freeze`；
`F-js-func` 21 项 / `F-js-rpc` 16 项 / `F-js-sig` 17 项；`F99-signals` 19 / `F99-slots` 22；`F01` 导航 ≥3；
`F95` 手柄注入 / `F98` 键盘 / `F100` 主循环；`B/C/D/E/F/G/H/I` 全部真渲染真交互段。
`#demoMode` / `#autoRecord` / `#bufInput` / `#adcGrid` / `#replayPos` 均仍在 `NEED_HTML_IDS` 内。

**原始输出**（2026-10-16，HEAD `6d4e3d9`，product-sha `app.js f539ea53cf8b` / `index.html 40448a48e5e9`，
跑前跑后 sha 一致 `SHA-STABLE: yes`）：

```
$ node --check telemetry_web/app.js
node exit=0

$ python -m py_compile tools\_fninv_check.py
py_compile exit=0

$ $env:QT_QPA_PLATFORM="offscreen"; python tools\_fninv_check.py
================================================================================
功能清单契约校验 tools/_fninv_check.py   （清单：tools/_fninv.md）
基线: HEAD 6d4e3d9   product-sha(app.js/index.html) = f539ea53cf8b / 40448a48e5e9
       （product-sha = git blob 哈希；与上一次 PASS 的票号不同即说明产品文件被改过，需重跑）
================================================================================
  PASS F95                    分区拖高手柄仍由 initZoneResize() 运行时注入（.zone-resize）
  PASS F-js-func              app.js 关键函数/绑定仍在（21 项）
  PASS F-js-rpc               app.js 仍在调用后端 RPC（16 项）
  PASS F-js-sig               app.js 仍在监听后端信号（17 项）
  PASS F98                    role=button 的键盘可达（Enter/Space）
  PASS F100                   主循环 requestAnimationFrame(render) 仍在
  PASS F01                    导航 rail-item ≥3 个（总览/可视化/串口）
  PASS F99-signals            后端 pyqtSignal 契约齐全（19 个）
  PASS F99-slots              后端 @pyqtSlot 契约齐全（22 个）
  PASS A_feed                 灌入真机形态数据后读数面板有数
  PASS A_fills_grow           有数据时填充分条不再是 0 尺寸（11 项）
  PASS A_ids_collapsed        功能元素没有塌成 0x0（布局丢失探测；条件显示项已排除并单测）
  PASS A_ids_dup              页面内没有重复 id
  PASS A_ids_missing          清单 102 个功能入口 id 全部存在
  PASS A_pauseTag_on_freeze   冻结时 #pauseTag 显示、恢复后隐藏（暂停角标行为）
  PASS B_nav_click            三个导航项点击后分区显隐正确 + active/aria 同步
  PASS C_freeze_click_key     freezeBtn 点击/Enter 均切换冻结（state + freezeText 同步）
  PASS C_freeze_space         freezeBtn 支持 Space 键
  PASS C_recPill_click        recPill 点击切换录制（recDot/recBtn/aria-pressed 三者同步变化）
  PASS C_recPill_restore      再次点击能回到未录制态（可逆）
  PASS D_chips_exist          通道 chip 存在且带 role=button/tabindex/data-id
  PASS D_chips_toggle         chip 点击切换显隐（state.visible 与 .on 同步；重建后按 data-id 重新取节点）
  PASS D_normBtn_cycle        normBtn 循环三档量程（归一化/自适应/分组分带）
  PASS D_sigPanel_interact    sigPanel 行 mouseover 设置高亮 state.hl、click 切换该通道（等效 chip）
  PASS E_resize_drag          拖动 .zone-resize 真的改变分区高度（B07 拖高契约）  [zoneP2 509px -> 589px, inline=589px, 本轮 rAF 回调=1]
  PASS E_resize_handles       每个 .zone 都有 .zone-resize 拖高手柄
  PASS F_init_disabled        非回放态下滑块与暂停按钮都禁用
  PASS F_loading_text         加载中 #replayPos 显示后端文案、进度回包不覆盖、滑块保持禁用
  PASS F_pos_reset            停止回放后 #replayPos 复位为 "— / —"
  PASS F_range_attrs          #replayRange 是 range 且 min/max/step=0/1000/1
  PASS F_replay_enable        回放态下进度条启用（replayStateChanged 接线）
  PASS G_add_chart            addChartBtn 点击后 visGrid 卡片 +1
  PASS G_card_parts           新卡片结构完整（.vc-del / .vc-sigs / .vc-canvas / 空态提示）
  PASS G_channels_draggable   通道列表项 draggable（可拖进图表）
  PASS G_del_chart            .vc-del 点击后卡片被移除
  PASS G_dragstart_sets_data  拖拽通道时 dragstart 写入通道 id
  PASS G_drop_adds_sig        把通道拖到图表卡片上会新增 .vc-sig（drag&drop 接线）
  PASS G_sig_click_removes    点击 .vc-sig 把该信号移出图表
  PASS G_vis_channels         #visChannels 有通道条目
  PASS H_browse_wired         browseDirBtn 点击有反馈
  PASS H_buf_change           bufInput change 生效（1234 被采纳；1e9 被夹紧到上限内）
  PASS H_connect_wired        connectBtn 点击有反馈（浏览器模式给出提示而非静默）
  PASS H_demo_toggle          demoMode 勾选/取消都能切换 state.demo
  PASS H_export_wired         exportBtn 点击有反馈（仍接着导出链路）
  PASS H_pwr_peak_click       pwrPeak 点击执行峰值清零（#pwrPeakNum 被重置）
  PASS H_replay_wired         replayBtn 点击有反馈（仍接着回放链路）
  PASS H_send_empty_safe      空输入按 Enter 不发送也不报错
  PASS H_send_wired           sendBtn 点击有反馈（HEX 文本进发送链路）
  PASS H_serial_controls      端口/波特率/发送格式控件存在且选项合理
  PASS I_toast_click          toast 元素可点击（触发关闭流程）
  PASS I_toast_show           toast() 能在 #toastWrap 弹出提示
  PASS I_toast_dismiss        toast 点击后真的被移除（关闭生效）
  PASS K_no_js_errors         全部交互过程中没有 JS 运行时错误
  PASS K_console              控制台无 ERROR/Uncaught
--------------------------------------------------------------------------------
结果: 54 PASS / 0 FAIL
================================================================================
EXIT=0
```

## 四、结论：**最可能被这次布局改动弄丢的功能**（按风险排序）

因为改动是"三条固定行槽横带"的**结构性重排**，风险集中在"**靠 JS 运行时注入 / 靠类名约定 /
靠动态构建**"的东西上 —— 它们在 DOM 大搬家时不会报错，只会静默消失：

1. **分区拖高手柄 `.zone-resize`（F95，P0）** —— 最危险。它不在 HTML 里，是 `initZoneResize()`
   遍历 `.zone` 现场 `createElement` 注入的。一旦新区布局不再用 `.zone` 类、或把手柄挪到别处，
   拖高功能直接消失且**没有任何报错**。另外拖高是否生效还依赖"内联 height 不被外层 grid/flex
   拉伸覆盖"—— 行槽横带若把分区变成 `grid` 项并 `align-self:stretch`，内联高度会被吃掉
   （我用 `E_resize_drag` 的 `inline=` 与实测高度差一起断言，正是为了抓这种"设了但没用"）。
2. **导航分区显隐集合（F01，P0）** —— `allZones` 是硬编码数组，`总览=除 zoneVisual 外全显`。
   行槽重排后若新增/改名分区而没同步这个数组，会出现"切到某页看不到内容"或"总览里混进可视化台"。
3. **趋势区的三个部件（F41 chips / F45 数值面板 / F40 三档按钮，P0-P1）** ——
   chips 由 `buildChips()` 动态生成、面板行由 `buildSigPanel()` 生成、`normBtn` 文案由 JS 写。
   行槽横带很容易把 `#chipBar`/`#sigPanel` 挪进新的 header/footer 而**忘了它们依赖的父容器宽度**
   （chip 换行、面板行高塌陷），或者把 `#normBtn` 和 `#addChartBtn` 搞混（两者都是 `.mode-btn`）。
4. **可视化区 增/删/拖（F50-F53，P1）** —— `visGrid` 卡片、`#visChannels` 项全是 JS 建的；
   `drop` 目标尺寸塌陷会让"拖通道进图"变成拖不进去（`G_drop_adds_sig` 会抓到）。
5. **回放进度条与暂停（F80-F82，P0）** —— 底栏若被并入新的行槽，`#replayRange` 的
   `min/max/step`、`input/change/mouseup/touchend/blur` 五处接线、以及 `#replayPos` 的
   "加载文案/回放位置"双语义都容易被简化掉。
6. **百分比填充分条（F10-F13/F20-F21）** —— 行槽如果给这些条换了更窄/更矮的容器，
   `width:X%`/`height:X%` 的视觉会退化成"条永远是空的"（无数据时看不出，一上真机就发现）。
   `A_fills_grow` 就是为它准备的。
7. **键盘可达性（F98，P1）** —— `role=button` + `tabindex=0` + `bindPillKey`/chips 的 keydown
   是**手写**的；重写 HTML 时最容易被省略（视觉完全正常，只有键盘用户受影响）。

## 五、已有专用探针（不算缺口，但**不要删**）

矢量图 `_accvec_check` `_accvec_move_check` `_accvec_scale_check`；趋势交互 `_trend_interact_probe`；
三档量程 `trend_mode_check`；回放条 `_replay_bar_check` `_footer_replay_probe` `_replay_e2e_check`；
seek 成本 `_replay_seek_cost`；暂停回填契约 `_task8_check`；串口零跳动 `_serial_jitter_probe`；
位宽锁定 `_width_reserve_check`；功率 `_pwr_check`；通道同步 `_vischan_sync_check` `_u08_sensitivity_check`；
配置夹紧 `_clamp_check`；布局闪屏 `_layout_flash_probe`；指针/字号/热条 `_d1d2d5_check`；
环形读数 `_ring_fit_check`；演示量纲 `_demo_accel_check`；DOM 基础 `web_ui_selfcheck`；
应用级 `_e2e_round2` `_e2e_loading` `_e2e_realdata`；真实帧解码 `real_frame_regress`；
CSV 列序 `column_order_check`；用户配置往返 `user_cfg_roundtrip`；真渲染截图/几何 `_ux_shot` `_ux2_probe`。
