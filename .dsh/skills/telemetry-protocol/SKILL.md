---
name: telemetry-protocol
description: 修改本遥测上位机的协议与配置（telemetry_config.py、protocol.py），或排查"改了配置没生效"、换算系数错、CSV 列与数据错位、通道名/单位不对时使用。内含六区配置结构、配置优先级陷阱、scale 与 CSV 的一致性要求与专项自检命令。
whenToUse: 涉及帧格式、字段偏移、换算系数、通道定义、单位、CSV 列，或配置不生效时
---

# 协议与配置：一个值有三处能改

## 一、★ 最先要搞清的事：谁说了算

```
① telemetry_user.json（exe 旁，界面「设置」也写它）  ← 优先级最高，免打包，重启生效
② telemetry_config.py                                ← 出厂默认，要重新打包
③ telemetry_web/app.js 顶部 UI_CFG                   ← 最低，仅演示模式，连上后端就被 ② 覆盖
```

**最经典的坑**：在 ① 里改过一次，之后改 ② 重新打包**界面也不会变**——因为 ① 一直压着它。

**"改了没生效"的排查顺序**：
1. 先确认没有 ① 覆盖（界面「设置 → 恢复为内置配置」可清；或看状态栏是绿还是橙）；
2. 再确认改了 ② 之后**真的重新打包了**；
3. 最后才怀疑代码。

## 二、六区配置结构（`telemetry_config.py`）

```
① 串口/链路   ② 帧格式   ③ ADC 区   ④ 动力区(MOTOR)   ⑤ 姿态区(NAV)   ⑥ 显示
```

**这是唯一真源**：通道名、字段表、CSV 列、曲线配色全从这里派生。
所以改一处要同时确认**三处一致**：解析（protocol）、CSV 列、界面显示。

### ★★ 两种"名字"别搞混（2026-10-16 用户踩过）★★

| 名字种类 | 住在哪 | 改了会断什么 |
|---|---|---|
| **协议字段名**（内部键）`ROLL/PITCH/YAW/SPEED…` | 第④⑤区字段宏第 1 个元素 | CSV 表头、前端 payload 键映射、**旧录播兼容垫片**（`protocol.py` 按 `ROLL/PITCH/YAW` 取历史帧）→ 会**静默**变空/错位 |
| **界面显示名** `横滚角/俯仰角/航向角…` | 第⑥区 `DISPLAY_NAMES` 宏表 | 只影响界面文字（趋势图例 / 数值面板 / 总览卡片标题），**随便改** |

- **想改界面上的字 → 只改 `DISPLAY_NAMES`**（键 = 前端信号 id：`roll/pitch/yaw/speed/temp/…`）。
  改完 `python tools/web_ui_selfcheck.py` 有 6 条 `[显示名宏]` 断言盯着"四处齐变"。
- **协议字段名必须是英文标识符**；`validate()` 会拦住写成中文（否则 CSV/曲线/旧录播全断）。
- `F_PITCH` 与 `F_YAW` 曾都被写成 `Y角加速度` → 同一包内字段重名 → 自检红。
  **自检报"字段名重复"时，先看是不是这种手滑**，不要把中文名塞进协议字段。
- ADC 通道显示名走第③区 `ADC_CHx` 的 `name`（同一套"写一处、全线生效"机制）。

## 三、换算只有一个公式

```
显示值 = 原始码 × scale
```
scale 只在配置第 4 区写一次。组帧与解码互为逆运算，改了系数两边自动同步。
**改 scale 会改协议解析**——和固件不对齐就别动。

## 四、"改配置" 还是 "改代码"？判断标准只有一条

**改的东西属不属于协议本身。**

| 想改什么 | 走哪条路 | 要重新打包吗 |
|---|---|---|
| 曲线量程不合适 / 单位后缀 / 默认勾哪几条曲线 | 界面「设置 → 量程与单位」 | ❌ |
| ADC 通道名 / 曲线颜色 / 波特率默认值 | `telemetry_user.json` | ❌（重启生效） |
| **界面上显示的信号名**（横滚/车速/母线电压…） | `telemetry_config.py` 第 ⑥ 区 `DISPLAY_NAMES` | ✅ |
| **scale / 字段偏移 / 新增包类型** | `telemetry_config.py` | ✅ |
| 布局、配色、加一块显示区 | `index.html` + `app.js` | ✅ |

用户问"能不能不打包"时，先查这张表。

## 五、改动协议时的强制性检查

CSV 成行要求 **ADC / NAV / MOTOR 三类都到齐**才写（避免同一行混入不同时刻的数据）。
所以改字段/加包之后，必须确认：

1. CSV 列名与取值**严格一一对应**（改了顺序也不能错位）；
2. 界面通道名、CSV 列名、解析取值**三者同源**；
3. 历史真帧仍能被正确解读（字节序 / 12bit 顺序 / 偏移 / scale 没漂移）。

## 六、验收

```powershell
python selftest.py                        # → "结果: 全部通过 ✅"
python tools/column_order_check.py        # → "CSV 列名与数据严格一一对应"
python tools/real_frame_regress.py        # → "历史真帧全部仍能被正确解读"
python tools/cfg_authority_audit.py       # → "[OK] 全部通过"
python build_web.py --check-only          # → "✅ 自检全部通过"
```

> 环境提醒：本机 **PowerShell 5.1**，读 UTF-8 要 `-Encoding utf8`，否则乱码且行数错报。
> stderr 里的 Chromium GPU ERROR 是正常噪声，只看退出码与通过标志行。

## 七、调试利器

```powershell
python tools/decode_frame.py      # 解一帧看字段
python tools/adc_unpack_probe.py  # ADC 12bit 解包顺序
python tools/verify_acc_scale.py  # 校验加速度 scale
```

ADC 12bit 解包顺序与 MOTOR 字节序都可以**运行期动态改**（配置保存后立即生效，不用重启），
现场拿着传感器就能试出正确组合。
