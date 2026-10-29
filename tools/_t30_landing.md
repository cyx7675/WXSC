# task-30 排版修复落地说明：「固定行槽的仪表带」+ 刻度按值对齐 + 姿态区统一控件

> 用户反馈：「①排版布局不够美观 ②组件有 bug，显示不好」。独立审查（ux2）判定这两条都是**结构性**的。
> 写范围：**只改 `telemetry_web/index.html`**（`app.js` 由 `frontend` 独占，本轮我一行未碰 —— `git status` 里 app.js 的改动是它的）。
> 硬约束：**不删除、不重命名任何既有 id**（脚本逐个核对：**153 → 155**，只新增 `speedScale`/`tempHeat`/`heatCap`）。

---

## 1. 结构改动（改了什么）

### 1.1 动力区：12 列优先级网格 → **3 条整宽横带 × 每卡 5 个固定行槽**
```
bandA 主控：16[标题] | minmax(168px,1fr)[图形] | 22[条] | 14[带内条] | 22[刻度]   车速/转速/功率
bandB 状态：16[标题] | 28[数值]              | 22[条] | 22[刻度]   | 22[热条]   油门/扭矩/温度/电压/电流
bandC 分析：16[标题] | minmax(96px,1fr)[画布]                                    双轴/散点
```
· 每张卡都是 `.hcard/.scard/.xcard` + `display:grid` + **显式 `grid-template-columns:minmax(0,1fr)`**。
  ★ 这行是**必须**的：不写的话隐式列按 `max-content` 定宽（实测卡 482 宽、列只有 235），
  卡里的条/热条会被缩成"内容宽度"，五张卡的条长度各不相同（76/135/148/115/84），横着没法比。
· "条"永远是各自卡片的**第 3 个槽** ⇒ 同带内的条必然落在同一条水平线上（这才是"美观"的根因）。
· 槽顺序统一为「条 → 带内条 → 刻度」（改前车速是"条→热条→刻度"、油门/电压/电流是"条→刻度→热条"）。
· 功率卡的图形槽从"60px 死白"改成 **大数字 + 峰值清零按钮**；第 4 个槽留空（对齐用）。

### 1.2 姿态区：四张卡 → 统一 4 行槽
`16[标题] | 28[数值] | 61[控件] | 14[刻度]`，控件行等高 ⇒ 四张卡**顶边齐**。
· 横滚：给 SVG 盘补静态刻度骨架（原来只有弧+零位虚线，0° 时指针与虚线重合，看不出是块仪表）。
· 俯仰：原来"竖向双向条塞进 211×52 宽框"，pitch≈0 时是一整块空矩形 → 收成**居中竖向量规**（含横档）。
  `scaleY` 的驱动方式没变（app.js 未动）。
· 航向：刻度数值原本画在 tape **内部**（还被 `overflow:hidden` 裁掉首尾）→ 移到带外的刻度行。
· 合成加速度：量程文字原本压在数据区里 → 移到带外刻度行（`#accScaleLo/Hi` 的 id 不变）。
· **四张卡几何保持 235×141**（基线 140.7 ±1）→ `_accvec_move_check` **不需要重取基线**（见 §4）。

### 1.3 动力区刻度行：从"等间距骗人"改成"两层按值定位"
```
上层 = <i class="thr thr-*" style="left:<v/满量程*100>%"> 阈值标签（钉在刻线上）
下层 = <span style="left:..."> 数值刻度（app.js 的 syncBarScale 按 span 数量均分文案）
```
· 阈值标签：车速 34 · 转速 3.8k/8k · 温度 70/90 · 电压 380/520 · 电流 180 · 功率 上限 80。
· **两层天然不相交**；层内标签数 ≤3、位置按值 → 实测 42 个标签两两包围盒相交 = **0**。
· `<i>` 而不是 `<span>`：`syncBarScale()` 用 `querySelectorAll('span')`，阈值绝不能被它均分覆盖（已在给 frontend 的契约里写明）。

### 1.4 其它
· **D9 电压安全带**：380–520 V 染成淡绿区间（`left:63.33%;right:13.33%`）+ 两端数值标签；
  原来只有 380 一条无标注线、520 完全没有标记。
· **D13 字号收敛**：动力区叶子只用 4 档 —— 11（微标签/刻度）/12（名称）/25（次级数值）/46（主数值）；
  原来 11 与 12 差 1px 却承担不同语义。
· **颜色双语义**：非语义条统一成**一个数据色**（`--data:#5B728F`）；绿/琥珀/红只留给"状态"
  （状态徽标、阈值刻线与标签、温度与功率的填充 —— 后两者的填充色由 app.js 按告警态切，属语义）。
· **D14**：记录区"保存目录"加 placeholder；设置区改两列（左=可操作项 / 右=只读信息：配置来源·生效方式·量程来源·数据契约）；
  底栏 `#replayRange:disabled + .hint::after` 显示"· 未在回放"（**不动 `#replayPos` 的文本** —— `_replay_bar_check` 断言初始文案必须是 `— / —`）；
  峰值按钮有完整按钮态（hover/active/告警描边）。
· **D12 无数据骨架态**（lead 真机截图证实是"加粗黑—"）：读数元素 `.is-empty` → `--faint` + `font-weight:500`；
  卡片 `.is-empty` → 条填充变骨架灰、热条/换灯 30% 透明、中部浮出 `data-empty` 文案（HTML 已预置
  `data-empty="等待 MOTOR 数据"` / `"等待 NAV 数据"`，**JS 只需切 class**，已把契约发给 frontend）。
· **D5(e)**：热条说明按实际口径改成「近 28 s 热条」并给 id `#heatCap` 供 JS 精算。

---

## 2. 量化对照（证明"同类条落在同一条水平线"）

### 2.1 条形 y 坐标（Chrome，同一脚本同参数）
```
动力区   改前 6 组不同 y：316(功率,h=26) / 317(车速,h=22) / 339(转速,h=22)
                        464(扭矩) / 465(油门·电压·电流) / 485(温度,h=26)
         改后 2 组：      323(车速·转速·功率，全 h=22) / 467(油门·扭矩·温度·电压·电流，全 h=22)
姿态区   改前 3 组：835(横滚,h=64) / 847(俯仰·合成,h=52) / 865(航向,h=34)
         改后 1 组：834(四张卡，全 h=61)
```

### 2.2 ★ 真机窗口（`tools/_t30_grab_real.py`，QScreen.grabWindow，2400×1500，DPI 1.5）
```
#stage.scrollHeight = 1868
bandA [97,121,1463,275]   bandB [97,404,1463,143]   bandC [97,555,1463,133]
动力区   y=321  车速(h=22)/转速(h=22)/功率(h=22)
        y=465  油门/扭矩/温度/电压/电流 (全 h=22)
姿态区   y=829  横滚/俯仰/航向/合成加速度 (全 h=61)
★ 刻度标签两两包围盒相交 = 0（标签总数 42）
```
截图：`tools/_t30/real_after.png`（真机有数据）、`tools/_t30/对照_真机_vs_Chrome.png`（真机 vs Chrome 并排）。

### 2.3 D3 阈值刻线 ↔ 标签对齐（谁在骗人）
```
改前：8 条阈值刻线里只有 1 条有标签落在 ±10px 内
      转速限扭 偏差46px · 转速报警 偏差80px · 温度过热 偏差47px · 温度偏高边界 偏差29px
      电压 380 偏差41px · 电压 520 偏差12px
改后：8/8 全部 ≤1px
```
（量测脚本 `tools/_t30_bars.js` 同时给出 `labelClash` 两两包围盒相交计数，改前改后都是 0 ——
 改前的病是"位置错"不是"挤在一起"；挤在一起是我第一版的中间状态，已由"上下两层"彻底解决。）

---

## 3. 验收（原始输出 `tools/_t30_accept.log`）
```
build_web --check-only      exit=0  ✅ 自检全部通过（共 5 项断言）
selftest.py                 exit=0  结果: 全部通过 ✅
web_ui_selfcheck.py         exit=0  全部通过
_layout_flash_probe.py      exit=0  溢出/裁切元素数 = 0
_serial_jitter_probe.py     exit=0  全部通过（喂数据期间串口区零跳动）
_width_reserve_check.py     exit=0  全部通过
_replay_bar_check.py        exit=0  全部通过
_ux_needle_probe.py         exit=0  全部通过（环真有像素/中心读数在环内/告警变红/横滚中心式 rotate）
_accvec_check.py            exit=0  全部通过
_accvec_move_check.py       exit=0  全部通过 ← 无需重取基线（见 §4）
_accvec_scale_check.py      exit=0  全部通过
_pwr_check.py               exit=0  全部通过（功率 30 条）
config_matrix_audit.py      exit=0  全部通过（含"改量程刻度文案跟着变"11 条）
_clamp_check.py             exit=0  全部通过
_task8_check.py             exit=0  全部通过（含"切区/回填后立刻补画"）
_fninv_check.py             exit=0  54 PASS / 0 FAIL（qa 的功能清单契约）
```
另：**F95 分区拖高实测**（`tools/_t30_resize.js`，真实 mousedown/mousemove/mouseup）：
`zones=8 handles=8`，拖动 45px 后 `#zoneP2` 643 → 663（内联 `height:663px` 生效并在 mouseup 后保持）✓

---

## 4. 关于 `_accvec_move_check` 基线（回应 lead 的"重取前告诉我"）
**不需要重取。** 我把 `.dcard` 的内容压到 136px ≤ 行高 140.7 —— 卡片高度仍由同行最高的 `#accVec` 决定，
实测四张卡仍是 **235×141**（基线 140.7，探针容差 ±1）→ 探针原样通过（exit=0）。
所以本轮**没有动任何基线文件**。

---

## 5. 结构改动 → 可能影响的功能（对照 `tools/_fninv.md` 的风险排序逐条自查）
| 风险项 | 我的处理 | 证据 |
|---|---|---|
| **F95 `.zone-resize` 拖高**（运行时注入） | `.zone` 类与结构保留、8 个分区未增删改名；手柄仍是 `.zone` 的直接子元素 | 实测拖动 643→663 ✓；`_layout_flash_probe` 拖动用例通过 |
| **F01 导航分区显隐**（`allZones` 硬编码数组） | 8 个分区 id 与顺序**一个没动**（P2 P1 P0 Trend Visual Serial Record Settings） | `_fninv_check` PASS；§2.1 分区 rect 对照 |
| **F41/F45/F40 趋势三件套**（chips/数值面板/三档按钮） | **完全没碰** trend 区（`#chipBar`/`#sigPanel`/`#normBtn` 原样） | `_fninv_check`、`_task8_check`、`_clamp_check` PASS |
| **F50–F53 可视化 增/删/拖** | **完全没碰** visual 区（`#visGrid`/`#visChannels` 原样） | `_fninv_check`、`_accvec_move_check` ③ PASS |
| **F80–F82 回放进度条/暂停** | 只在底栏加 CSS `::after` 提示；`#replayRange` 的 0/1000/1、五处接线、`#replayPos` 文本全部未动 | `_replay_bar_check` 全部通过（含初始 `— / —`） |
| **F10–F13/F20–F21 百分比填充** | ⚠️**本轮主动修强**：以前隐式列按 max-content 定宽，条只有 76–148px；现在显式 `minmax(0,1fr)`，条恒等于卡内容宽（456/260）→ 百分比填充更可靠 | §2.1 条宽 456/260 全部一致 |
| **F98 键盘可达性** | 未删任何 `role/tabindex`；`#pwrPeak` 仍是 `<button>`（只加样式与 hover/active 态） | `_fninv_check` 的键盘用例 PASS |
| 读数/单位按配置 | `#speedSub`/`#speedFs`/`#heatCap` 保留；单位仍在数字的同一父节点内（`config_matrix_audit` 的取法） | `config_matrix_audit` PASS |

新增元素只有 3 个 id：`#speedScale`（刻度行容器）、`#tempHeat`（温度热条，app.js 的 `.heat[data-heat]` 自动接管）、`#heatCap`（热条口径文案）。

---

## 6. 不确定项
1. **真机"骨架态"截图**：`frontend` 的 `_grab_real.py` 连续两次 `grabWindow` 返回了同一张位图
   （`real_empty.png` 与 `real_after.png` 字节完全相同）→ 骨架态我用 Chrome 出图（`chrome_empty_zoneP2.png`），
   真机**有数据态**已用真窗口验证 ✓。若要在真机上抓骨架态，需要在两次 grab 之间强制重绘
   （改窗口尺寸/滚动），我没有改 frontend 的脚本。
2. **窄屏**：1280/1080/820 三档断点按"带内降列"重写，未做窄屏实机验证。
3. **`.bandB > .is-empty::after{top:38%}`** 是给矮卡单独调的位置；换字体/DPI 可能需要微调。
4. 功率卡第 4 槽（14px 带内条行）留空（车速放热条、转速放换挡灯）。若要填，可加 `#pwrHeat`
   （app.js 的 `.heat[data-heat]` 会自动接管），但需要产品确认"功率要不要 30 s 热条"。
5. `_t30` 量测脚本是**几何量测**，不替代像素级视觉评审；真机图的"整页观感"仍建议人眼过一遍。
