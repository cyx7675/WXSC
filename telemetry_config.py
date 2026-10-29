# -*- coding: utf-8 -*-
"""
无线数传上位机 · 配置唯一源
============================
★ 改参数只改本文件上半部分的「参数区」，改完运行 一键打包.bat 重新打包生效。

参数区按数据区域分类，每个可改参数都是一个「命名宏」，后面带注释说明：

    ┌──────────────┬────────────────────────────────────────────┐
    │ ① 串口/链路   │ 波特率 / 校验位 / 停止位                    │
    │ ② 帧格式      │ 包头 / 包尾 / CRC 算法                      │
    │ ③ ADC 区      │ 10 路传感通道：名称/单位/量程/颜色/零点/顺序 │
    │ ④ 动力区      │ MOTOR 包：扭矩/转速/电压/电流/车速… 字段宏   │
    │ ⑤ 姿态区      │ NAV 包：横滚/俯仰/航向/加速度… 字段宏        │
    │ ⑥ 显示        │ 单位后缀 / 小数位 / 仪表量程 / 曲线参数      │
    └──────────────┴────────────────────────────────────────────┘

下方「自动组装 / 派生 / 体检 / 外部配置」均由上面宏自动生成，通常不用改。

优先级：telemetry_user.json（exe 旁，免打包） > 本文件 > app.js 兜底。
改完验证：python telemetry_config.py（体检） / python selftest.py（协议自检）。
"""
from typing import Dict, List, Tuple, Optional
import json as _json
import os as _os
import sys as _sys


# ══════════════════════════════════════════════════════════════════
#  ★ 参数区 —— 修改配置只改这里 ★
# ══════════════════════════════════════════════════════════════════

# ── ① 串口 / 链路 ────────────────────────────────────────────────
SERIAL = {
    "default_baud": 115200,          # 默认波特率（须与无线模块一致）
    "baud_choices": [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600],
    "bytesize": 8,                   # 数据位
    "parity": "N",                   # 校验位：N 无 / E 偶 / O 奇
    "stopbits": 1,                   # 停止位
    "timeout": 0.05,                 # 读超时（秒）
}

# ── ② 帧格式（所有包共用信封）────────────────────────────────────
# 帧布局：[包头2][类型1][序号1][时间戳4][数据…][CRC2][包尾2]
FRAME_HEADER_U16 = 0xAA55            # 包头（线上字节 55 AA）
TAIL_MODE = "reversed"               # 包尾模式：reversed=包头逆序 / custom=自定义
TAIL_CUSTOM = (0xAA, 0x55)           # 自定义包尾（仅 TAIL_MODE="custom" 生效）
CRC_NAME = "crc16_modbus"            # CRC 算法：crc16_modbus/ccitt/xmodem/kermit/none/custom
CRC_CUSTOM = dict(poly=0x8005, init=0xFFFF, refin=True, refout=True)  # CRC_NAME="custom" 时用
CRC_RANGE = "header_to_payload"      # CRC 覆盖范围（当前仅作说明）


# ══════════════════════════════════════════════════════════════════
#  ③ ADC 区（P0 · 10 路传感通道，100Hz）
# ══════════════════════════════════════════════════════════════════
# 通道宏：short(内部键,固定CH1..CH10) / name(中文名) / sensor(类型)
#         unit(单位) / rng(量程上限) / color(曲线色) / zero(零点码,可选)
# 注：零点在中间的传感器（悬架/转向中位）可加 zero=2048 让显示跨 0。
ADC_CH1  = dict(short="CH1",  name="悬架线位移右前", sensor="线位移", unit="mm", rng=28.0,  color="#7C3AED")
ADC_CH2  = dict(short="CH2",  name="悬架线位移左后", sensor="线位移", unit="mm", rng=28.0,  color="#2563EB")
ADC_CH3  = dict(short="CH3",  name="悬架线位移右后", sensor="线位移", unit="mm", rng=28.0,  color="#DC2626")
ADC_CH4  = dict(short="CH4",  name="悬架线位移左前", sensor="线位移", unit="mm", rng=28.0,  color="#0E9F6E")
ADC_CH5  = dict(short="CH5",  name="转向线位移",     sensor="线位移", unit="mm", rng=150.0, color="#B45309")
ADC_CH6  = dict(short="CH6",  name="压力传感器4",    sensor="压力",   unit="kg", rng=146.0, color="#0891B2")
ADC_CH7  = dict(short="CH7",  name="压力传感器1",    sensor="压力",   unit="kg", rng=146.0, color="#65A30D")
ADC_CH8  = dict(short="CH8",  name="压力传感器2",    sensor="压力",   unit="kg", rng=132.0, color="#DB2777")
ADC_CH9  = dict(short="CH9",  name="压力传感器3",    sensor="压力",   unit="kg", rng=132.0, color="#EA580C")
ADC_CH10 = dict(short="CH10", name="压力传感器（未接）",     sensor="压力",   unit="kg", rng=200.0, color="#0F766E")

# ADC 通用宏
ADC_ORDER = None             # 显示/CSV 顺序：None=物理序 CH1..CH10；[5,6,7,8,9,10,1,2,3,4]=把后6路排前面
ADC_DECODE_MODE = "eng"      # 解码模式：eng=码值×rng/4095 还原工程量 / raw=12bit 码原样直显
ADC_DECIMALS = 1             # 读数小数位：0/1/2/3 固定；None=用 DEFAULT_DECIMALS
ADC_12BIT_ORDER = "msb"      # 12bit 打包顺序：msb / lsb / msb_swap / lsb_swap（通道错位时切）


# ══════════════════════════════════════════════════════════════════
#  ④ 动力区（P2 · MOTOR 包，50Hz）
# ══════════════════════════════════════════════════════════════════
# 字段宏格式：(信号名, 偏移字节, struct格式, 换算系数scale)
#   换算约定：显示值 = (原始码 − zero) × scale
#   加字段：新增一行字段宏 + 把 MOTOR_PACKET_TOTAL 加上新字段的字节数。
MOTOR_PACKET_TOTAL = 23      # MOTOR 包总字节数（2头+1类型+1序号+4时间戳+字段+2CRC+2尾）
MOTOR_ENDIAN = "<"           # 多字节字段字节序：< 小端 / > 大端

F_MOTOR_TEMP = ("MOTOR_TEMP",  8, "<B", 1.0)     # 电机温度（原始码）
F_TORQUE     = ("TORQUE",      9, "<B", 1.0)     # 扭矩（原始码）
F_MOTOR_RPM  = ("MOTOR_RPM",  10, "<H", 1.0)     # 电机转速（原始码）
F_THROTTLE   = ("THROTTLE",   12, "<B", 1.0)     # 油门开度（原始码）
F_VOLTAGE    = ("VOLTAGE",    13, "<H", 1.0)     # 母线电压（1 码 = 1 V）
F_CURRENT    = ("CURRENT",    15, "<H", 1.0)     # 母线电流（1 码 = 1 A）
F_SPEED      = ("SPEED",      17, "<h", 0.001)   # 车速（改 scale 不影响小数位）


# ══════════════════════════════════════════════════════════════════
#  ⑤ 姿态区（P1 · NAV 包，25Hz）
# ══════════════════════════════════════════════════════════════════
# 字段宏格式：(信号名, 偏移字节, struct格式, 换算系数scale)
NAV_PACKET_TOTAL = 22        # NAV 包总字节数（2头+1类型+1序号+4时间戳+字段+2CRC+2尾）

F_ROLL  = ("ROLL",   8, "<h", 0.01)     # 横滚角（固件 ×100，0.01° 精度）
F_PITCH = ("PITCH", 10, "<h", 0.01)     # 俯仰角（固件 ×100）
F_YAW   = ("YAW",   12, "<h", 0.01)     # 航向角（固件 ×100）
# ★★ 这三个名字是**协议字段名/内部键**，不是界面文字：CSV 表头、前端曲线 id、
#    旧录播兼容垫片（protocol.py 按 ROLL/PITCH/YAW 取历史帧）都依赖它们。
#    **不要把它改成中文**——想改界面显示名，去第 ⑥ 区的 DISPLAY_NAMES 宏表（改一处全线生效）。
F_ACCEL_lat = ("ACCEL_LAT", 14, "<h", 0.001/9.8)   # 横向加速度（原始码 × 定标）
F_ACCEL_lon = ("ACCEL_LON", 16, "<h", 0.001/9.8)   # 纵向加速度（原始码 × 定标）


# ══════════════════════════════════════════════════════════════════
#  ⑥ 显示（单位后缀 / 小数位 / 仪表量程 / 曲线参数）
# ══════════════════════════════════════════════════════════════════
# 各信号单位后缀（键 = 第④⑤区的信号名）
UNITS: Dict[str, str] = {c["short"]: c["unit"] for c in [ADC_CH1, ADC_CH2, ADC_CH3, ADC_CH4, ADC_CH5,
                                                          ADC_CH6, ADC_CH7, ADC_CH8, ADC_CH9, ADC_CH10]}
UNITS.update({
    # 姿态区
    "ROLL": "°", "PITCH": "°", "YAW": "°", "ACCEL": "",
    # 加速度已拆成横/纵两路（NAV 包 20→22 字节）：固件原始码 × 0.001 后即为 g
    "ACCEL_LAT": "g", "ACCEL_LON": "g",
    # 动力区
    "MOTOR_TEMP": "°C", "MOTOR_RPM": "RPM", "TORQUE": "N·m",
    "THROTTLE": "%", "VOLTAGE": "V", "CURRENT": "A", "SPEED": "m/s",
})

# 各信号读数小数位（与 scale 无关）
FIELD_DECIMALS: Dict[str, int] = {
    # 姿态区
    "ROLL": 1, "PITCH": 1, "YAW": 1, "ACCEL": 2,
    "ACCEL_LAT": 2, "ACCEL_LON": 2,
    # 动力区
    "MOTOR_TEMP": 0, "MOTOR_RPM": 0, "TORQUE": 1, "THROTTLE": 0,
    "VOLTAGE": 1, "CURRENT": 1, "SPEED": 2,
}
DEFAULT_DECIMALS = 2                 # 上表没列到的信号兜底小数位
FIELD_ZERO: Dict[str, float] = {}    # 各信号零点(原始码)：显示=(原始码−zero)×scale

# 配置信号名 → app.js 小写键（新增显示字段要补映射 + index.html 加元素）
FRONTEND_KEYS = {
    # 姿态区
    "ROLL": "roll", "PITCH": "pitch", "YAW": "yaw", "ACCEL": "accel",
    # 动力区
    "MOTOR_TEMP": "temp", "MOTOR_RPM": "rpm", "TORQUE": "torque",
    "THROTTLE": "throttle", "VOLTAGE": "volt", "CURRENT": "amp", "SPEED": "speed",
}

# ══════════════════════════════════════════════════════════════════
#  ★★ 界面显示名宏区 ★★  —— 想改界面上看到的文字，只改这里 ★★
# ══════════════════════════════════════════════════════════════════
# 键 = 前端信号 id（与 FRONTEND_KEYS 的右值同一套，见 app.js 的 defSig）；
# 值 = 界面各处显示的**中文名**（趋势图例 / 右侧数值面板 / 总览卡片标题一起变）。
#
# ★ 为什么单独一张表：**协议字段名 ≠ 显示名**。
#   第④⑤区的字段名（ROLL/PITCH/YAW/SPEED…）是**内部键**，被三处硬依赖：
#     · CSV 表头（改了就变中文列名，外部脚本/历史记录对不上）
#     · 旧录播兼容垫片 protocol.py（按 ROLL/PITCH/YAW 取历史帧，改了就静默变空）
#     · 前端 payload 键映射（FRONTEND_KEYS）
#   → 所以那些名字**不要改**；要改界面文字就改这张表。
#
# 用法：改右边引号里的字，保存 -> 重新打包（或源码态重启程序）即可，别的地方不用动。
#      想加一路新信号：先在④⑤区加字段 + FRONTEND_KEYS 加映射，再来这里加一行显示名。
#      ADC 的通道显示名走第③区 ADC_CHx 的 name 字段（同一种"写一处、全线生效"的机制）。
DISPLAY_NAMES: Dict[str, str] = {
    # ── 姿态区（P1）──
    "roll":     "X轴角速度",          # 协议字段 ROLL
    "pitch":    "Y轴角速度",          # 协议字段 PITCH
    "yaw":      "Z轴角速度",          # 协议字段 YAW
    "accelLat": "加速度·横向",     # 协议字段 ACCEL_LAT
    "accelLon": "加速度·纵向",     # 协议字段 ACCEL_LON
    "accelMag": "合成加速度",      # 前端派生量（不是协议字段）
    # ── 动力区（P2）──
    "speed":    "车速",            # 协议字段 SPEED
    "rpm":      "电机转速",        # 协议字段 MOTOR_RPM
    "throttle": "油门开度",        # 协议字段 THROTTLE
    "torque":   "扭矩",            # 协议字段 TORQUE
    "temp":     "电机温度",        # 协议字段 MOTOR_TEMP
    "volt":     "母线电压",        # 协议字段 VOLTAGE
    "amp":      "母线电流",        # 协议字段 CURRENT
    "power":    "电池功率",        # 前端派生量 volt×amp/1000
}

# 加速度条满量程码值：界面满量程(显示单位) = 本值 × ACCEL 字段 scale。
# ★ 必须与 scale 匹配：旧值 1000000 是 scale=0.0001 时代的遗留，用户把 scale 改成 0.001 后
#   它会算成 1000 g（该字段 int16 物理上限仅 ±32.767 g），量程大 30 倍 →
#   矢量图箭头只剩 0.075 px、趋势曲线贴底、G 条 0.14% 宽，等于看不见。
#   16384 是 app.js 的原始默认（±16g 档）：16384 × 0.001 = 16.384 g。
#   保持"改 scale 时刻度自动跟随"这一设计（故 GAUGE_RANGES["ACCEL"] 仍留 None=自动）。
ACC_RAW_FULLSCALE = 16384
# 界面仪表量程：数=0~v；[lo,hi]=区间；None=自动
GAUGE_RANGES: Dict[str, object] = {
    "ACCEL": None,                                    # 自动 = ACC_RAW_FULLSCALE × 首个 ACCEL* scale
    "ROLL": [-180, 180], "PITCH": [-90, 90], "YAW": [0, 360],
    "SPEED": 40, "MOTOR_RPM": 10000, "TORQUE": 150, "THROTTLE": 100,
    "MOTOR_TEMP": 120, "VOLTAGE": 600, "CURRENT": 200,
}
# 曲线窗口 / 取样 / 帧率 / 默认曲线（json "ui" 段免打包改）
UI_CFG: Dict[str, object] = {
    "bufPoints": 600, "sampleMs": 50, "trendFps": 30, "dprCap": 2,
    "domHz": 20, "domHzScroll": 6, "trendFpsScroll": 10, "scrollQuietMs": 200,
    "sparkMs": 250, "hexMax": 60, "revSegs": 12,
    # ★ accelRaw 已随协议变更移除（NAV 包加速度拆成横/纵两路），默认改为可见加速度三路：
    #   横向 ACCEL_LAT / 纵向 ACCEL_LON / 合成 accelMag
    #   2026-10-16 追加 "power"（电池输出功率 kW）：用户要求在动力区看功率并做 >80kW 告警，
    #   默认也画进趋势曲线，省得每次手动勾选。
    "defaultVisible": ["rpm", "volt", "amp", "power", "accelLat", "accelLon", "accelMag"],
    # ★ 电池输出功率红色警告阈值（kW）。
    #   功率 = 母线电压(V) × 放电电流(A) / 1000（两个字段同在 MOTOR 帧，天然时间对齐）。
    #   FS 规则把动力电池的输出功率上限定在 80 kW，超过即违规/危险 → 动力区卡片转红警告。
    #   可在 telemetry_user.json 的 "ui" 段覆盖（例如台架标定与赛道用不同阈值）。
    "powerWarnKw": 80.0,
}


# ══════════════════════════════════════════════════════════════════
#  ★ 自动组装（由上面宏生成，通常不用改）★
# ══════════════════════════════════════════════════════════════════

# ── 帧信封派生 ──────────────────────────────────────────────────
HEADER = FRAME_HEADER_U16.to_bytes(2, "little")      # b"\x55\xAA"
SYNC = HEADER
TAIL = bytes(TAIL_CUSTOM) if TAIL_MODE == "custom" else bytes(reversed(HEADER))

# ── 串口派生 ────────────────────────────────────────────────────
DEFAULT_BAUD = SERIAL["default_baud"]
BAUD_CHOICES = SERIAL["baud_choices"]
SERIAL_TIMEOUT = SERIAL["timeout"]

# ── ADC 派生 ────────────────────────────────────────────────────
ADC_CHANNELS = [ADC_CH1, ADC_CH2, ADC_CH3, ADC_CH4, ADC_CH5,
                ADC_CH6, ADC_CH7, ADC_CH8, ADC_CH9, ADC_CH10]
ADC_COUNT = len(ADC_CHANNELS)                  # 通道数（自动，别手改）
ADC_FULLSCALE = [c["rng"] for c in ADC_CHANNELS]

# ── 协议派生（用④⑤区的字段宏组装）──────────────────────────────
PROTOCOL: Dict[int, dict] = {
    # P0 · ADC 包（12bit 紧凑打包，字节数由通道数派生）
    0x00: {
        "name": "ADC",
        "total": 8 + (ADC_COUNT * 12 + 7) // 8 + 4,
        "seq": (3, "<B"),
        "ts": (4, "<I"),
        "packed_off": 8, "packed_len": (ADC_COUNT * 12 + 7) // 8, "adc_n": ADC_COUNT,
    },
    # P1 · NAV 姿态包
    0x01: {
        "name": "NAV",
        "total": NAV_PACKET_TOTAL,
        "seq": (3, "<B"),
        "ts": (4, "<I"),
        "fields": [F_ROLL, F_PITCH, F_YAW, F_ACCEL_lat, F_ACCEL_lon],
    },
    # P2 · MOTOR 动力包
    0x02: {
        "name": "MOTOR",
        "total": MOTOR_PACKET_TOTAL,
        "seq": (3, "<B"),
        "ts": (4, "<I"),
        "endian_key": "MOTOR_ENDIAN",      # 多字节字段字节序（protocol.py 动态读）
        "fields": [F_MOTOR_TEMP, F_TORQUE, F_MOTOR_RPM, F_THROTTLE,
                   F_VOLTAGE, F_CURRENT, F_SPEED],
    },
    # 新增包模板：照此加一项即可（解析/CSV 自动生效，无需改代码）
    # 0x03: {"name":"BMS","total":18,"seq":(3,"<B"),"ts":(4,"<I"),
    #        "fields":[("PACK_V",8,"<H",0.1),("PACK_A",10,"<h",0.1),
    #                  ("SOC",12,"<B",1.0),("FAULT",13,"<B",1.0)]},
}

def adc_out_order() -> List[int]:
    """显示/CSV 用的通道顺序(0-based 物理索引)；卡片序、CSV 列名、CSV 取值三者唯一真源。"""
    n = ADC_COUNT
    if not ADC_ORDER:
        return list(range(n))
    out: List[int] = []
    seen = set()
    for v in ADC_ORDER:
        try:
            i = int(v) - 1                 # 用户填 1-based 通道号
        except (TypeError, ValueError):
            continue
        if 0 <= i < n and i not in seen:
            seen.add(i)
            out.append(i)
    out += [i for i in range(n) if i not in seen]      # 没排到的补末尾
    return out

def adc_zero_code(i: int) -> float:
    """第 i 通道零点码值(12bit,0~4095)，默认 0。"""
    try:
        v = ADC_CHANNELS[i].get("zero", 0)
    except (KeyError, IndexError, AttributeError, TypeError):
        return 0.0
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0

def adc_step(i: int) -> float:
    """第 i 通道「1码=多少工程量」(raw 模式=1)。动态读 rng，勿 from-import 快照。"""
    if ADC_DECODE_MODE != "eng":
        return 1.0
    try:
        rng = float(ADC_CHANNELS[i].get("rng", 1.0))
    except (KeyError, IndexError, TypeError, ValueError):
        return 1.0
    return rng / 4095.0

def adc_display_range(i: int) -> Tuple[float, float]:
    """第 i 通道显示范围(min,max)；eng 随零点自动跨 0；raw 用 min/max。"""
    c = ADC_CHANNELS[i]
    z = adc_zero_code(i)
    if ADC_DECODE_MODE == "eng":
        step = float(c["rng"]) / 4095.0
        return -z * step, (4095.0 - z) * step
    return float(c.get("min", 0)) - z, float(c.get("max", 4095)) - z

# ── 边界夹紧（task-22）───────────────────────────────────────────
# ★ 必须定义在**首次使用之前**：adc_decimals() 引用 _clamp_decimals()，而它在模块级
#   第 285 行（_ADC_FMT 列表推导）就被调用了 —— 定义放在后面会导致 import 直接 NameError。
# telemetry_user.json 是手写 JSON，任何数值都可能离谱。这里统一夹紧，避免"配置没夹紧"
# 把界面或回放拖垮。★ 只针对非法/极端值；内置配置与正常覆盖值一字不动。
#   · decimals：0~20。>100 会让前端 toFixed() 抛 RangeError（那一帧的数值面板/悬停/
#     矢量图读数全部中断）；20~100 虽不抛错，但会产出 20~100 位数字串、读数框必然溢出。
#   · scale / gaugeRanges / accRawFullscale：必须有限且 > 0（负数会让刻度变成"±1e-12"、
#     矢量图永远超量程）。非法 → 忽略该覆盖、回退内置值，并记一条 CFG_NOTES。
#   · ui.bufPoints：10~20000（与前端 app.js 的夹紧一致）。1e8 会让前端缓冲不再 splice
#     （无限增长）且后端回填一次要造 1e8 个槽 → GUI 卡死 / OOM。
DECIMALS_MIN, DECIMALS_MAX = 0, 20
UI_BUFPOINTS_MIN, UI_BUFPOINTS_MAX = 10, 20000
CFG_NOTES: List[str] = []          # 被夹紧/被忽略的外部覆盖（在配置体检里显示）


def _clamp_decimals(v) -> Optional[int]:
    """小数位夹到 [0, 20]；不可转换 → None（调用方保留原值）。"""
    try:
        return max(DECIMALS_MIN, min(DECIMALS_MAX, int(v)))
    except (TypeError, ValueError):
        return None


def adc_decimals(i: int) -> int:
    """第 i 通道小数位：ADC_DECIMALS 有值听它，None 用 DEFAULT_DECIMALS。★ 夹到 [0,20]。"""
    n = _clamp_decimals(ADC_DECIMALS if ADC_DECIMALS is not None else DEFAULT_DECIMALS)
    if n is None:                       # 显式配了非法值（如 NaN）→ 退回默认小数位
        n = _clamp_decimals(DEFAULT_DECIMALS)
    return DECIMALS_MIN if n is None else n

def adc_csv_fmt(i: int) -> str:
    """ADC 第 i 通道 CSV 输出格式。"""
    return "%%.%df" % adc_decimals(i)

_ADC_FMT: List[str] = [adc_csv_fmt(i) for i in range(ADC_COUNT)]

# ── 信号分组派生 ────────────────────────────────────────────────
ADC_SIGNALS = [c["short"] for c in ADC_CHANNELS]
NAV_SIGNALS = [f[0] for f in PROTOCOL[0x01]["fields"]]
MOTOR_SIGNALS = [f[0] for f in PROTOCOL[0x02]["fields"]]

# ── 边界夹紧（task-22）的常量与 _clamp_decimals 已上移到 adc_decimals 之前 ──


def _positive(v) -> Optional[float]:
    """合法正数（有限且 > 0）→ float；NaN / ±Inf / 0 / 负数 / 非数 → None。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")) or f <= 0:
        return None
    return f

def _finite(v) -> Optional[float]:
    """任意有限数（**允许负数与 0**）→ float；NaN / ±Inf / 非数 → None。

    ★ 量程区间 [低,高] 必须允许负的下界：内置的 ROLL=[-180,180]、PITCH=[-90,90]
      就是负区间；这里只拒绝"非有限"和"高≤低"。
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f

def field_scale(fields, name: str, default: float = 1.0) -> float:
    """按信号名取 scale（供 protocol.py/自检用）。"""
    for f in fields:
        if f[0] == name:
            return float(f[3])
    return default

def field_decimals(name: str, field=None) -> int:
    """某信号小数位：① 字段第5项 ② FIELD_DECIMALS ③ DEFAULT。★ 一律夹到 [0,20]（task-22）。"""
    if field is not None and len(field) > 4 and field[4] is not None:
        n = _clamp_decimals(field[4])
        if n is not None:
            return n
    v = FIELD_DECIMALS.get(name)
    n = _clamp_decimals(DEFAULT_DECIMALS if v is None else v)
    if n is None:                       # 显式配了非法值（如 NaN）→ 退回默认小数位，而不是 0
        n = _clamp_decimals(DEFAULT_DECIMALS)
    return DECIMALS_MIN if n is None else n

def field_zero(name: str, field=None) -> float:
    """某信号零点(原始码)，默认 0：① 字段第6项 ② FIELD_ZERO ③ 0。"""
    if field is not None and len(field) > 5 and field[5] is not None:
        try:
            return float(field[5])
        except (TypeError, ValueError):
            pass
    try:
        return float(FIELD_ZERO.get(name, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


# ══════════════════════════════════════════════════════════════════
#  ★ CSV 导出（列/行格式自动派生，通常不用改）★
# ══════════════════════════════════════════════════════════════════

def csv_channels_meta() -> List[Tuple[str, str]]:
    """[(物理标号, 通道名)]，列名 CH07_压力传感器1；编号取物理通道号、顺序取 adc_out_order()。"""
    return [("CH%02d" % (i + 1), ADC_CHANNELS[i]["name"]) for i in adc_out_order()]

def csv_groups() -> List[Tuple[int, List[str]]]:
    """CSV 按包类型分组的 [(类型号, [信号名...])]，升序；新增包自动进 CSV。"""
    return [(tid, [f[0] for f in spec["fields"]])
            for tid, spec in sorted(PROTOCOL.items()) if "fields" in spec]

def csv_columns() -> List[str]:
    """完整 CSV 列名列表：time + ADC各通道 + 各包字段。"""
    cols = ["time"]
    cols += ["%s_%s" % (s, n) for s, n in csv_channels_meta()]
    for _tid, names in csv_groups():
        cols += [n.lower() for n in names]
    return cols

CSV_HEADER = ",".join(csv_columns())

def _fmt_for(field) -> str:
    """单字段 CSV 输出格式（位数口径见 field_decimals）。"""
    return "%%.%df" % field_decimals(field[0], field)

_GROUP_FMT = {tid: [_fmt_for(f) for f in spec["fields"]]
              for tid, spec in PROTOCOL.items() if "fields" in spec}

def refresh():
    """重算所有「由 PROTOCOL 派生」的表（仅运行时动态改 PROTOCOL 才需调）。"""
    global CSV_HEADER
    _GROUP_FMT.clear()
    _GROUP_FMT.update({tid: [_fmt_for(f) for f in spec["fields"]]
                       for tid, spec in PROTOCOL.items() if "fields" in spec})
    _ADC_FMT[:] = [adc_csv_fmt(i) for i in range(ADC_COUNT)]
    CSV_HEADER = ",".join(csv_columns())

def build_csv_row(stamp: str, adc: List[float], *groups: List[float]) -> str:
    """把三类包到齐的快照拼成 CSV 一行（含末尾换行）。*groups 顺序须与 csv_groups() 一致。"""
    cells = [stamp]
    for i, v in enumerate(adc):
        cells.append(_ADC_FMT[i] % v if i < len(_ADC_FMT) else str(v))
    for gi, vals in enumerate(groups):
        fmts = _GROUP_FMT[csv_groups()[gi][0]]
        for i in range(len(fmts)):
            cells.append(fmts[i] % (vals[i] if i < len(vals) else 0.0))
    return ",".join(cells) + "\n"


# ══════════════════════════════════════════════════════════════════
#  ★ 下发前端（ui_config 由上面参数生成，通常不用改）★
# ══════════════════════════════════════════════════════════════════

def ui_adc_config() -> List[dict]:
    """界面画 ADC 卡片的元数据（顺序走 adc_out_order()，与 CSV 同源）。"""
    out = []
    for i in adc_out_order():
        c = ADC_CHANNELS[i]
        lo, hi = adc_display_range(i)
        out.append(dict(short=c["short"], name=c["name"], sensor=c["sensor"],
                        unit=c["unit"], rng=c["rng"], color=c["color"],
                        min=lo, max=hi,
                        zero=adc_zero_code(i),
                        dec=adc_decimals(i)))
    return out

def _accel_scale() -> float:
    """第 4 区加速度字段当前 scale。

    ★ 加速度已拆成 ACCEL_LAT / ACCEL_LON（原单路 ACCEL）；这里用 startswith 以同时兼容
      旧布局与新布局，取第一个加速度字段的 scale（两路 scale 相同）。
    """
    for f in PROTOCOL[0x01]["fields"]:
        if str(f[0]).startswith("ACCEL"):
            return float(f[3])
    return 1.0

def accel_fullscale() -> float:
    """加速度条显示单位满量程 = ACC_RAW_FULLSCALE × ACCEL scale（改 scale 自动跟随）。"""
    return ACC_RAW_FULLSCALE * _accel_scale()

def gauge_ranges() -> Dict[str, object]:
    """界面仪表量程；ACCEL 为 None 必须原样下发 None（前端退化为 accRawFs×scale）。"""
    out: Dict[str, object] = {}
    for k, v in GAUGE_RANGES.items():
        if v is None:
            out[k] = None
        elif isinstance(v, (list, tuple)) and len(v) == 2:
            out[k] = [float(v[0]), float(v[1])]
        else:
            out[k] = float(v)
    return out

def ui_config() -> dict:
    """整份前端配置；app.js 收到后重建 ADC 卡片与信号注册表。"""
    return {
        "version": 2,
        "decodeMode": ADC_DECODE_MODE,
        "adc12bitOrder": ADC_12BIT_ORDER,
        "motorEndian": MOTOR_ENDIAN,
        "serial": dict(SERIAL),
        "adc": ui_adc_config(),
        "nav": [dict(name=f[0], unit=UNITS.get(f[0], ""), scale=f[3],
                     dec=field_decimals(f[0], f)) for f in PROTOCOL[0x01]["fields"]],
        "motor": [dict(name=f[0], unit=UNITS.get(f[0], ""), scale=f[3],
                       dec=field_decimals(f[0], f)) for f in PROTOCOL[0x02]["fields"]],
        "csvHeader": CSV_HEADER,
        "accRawFs": ACC_RAW_FULLSCALE,
        "gaugeRanges": gauge_ranges(),
        # ★ 界面显示名宏（第 ⑥ 区 DISPLAY_NAMES）：前端收到后统一刷新
        #   趋势图例 / 数值面板 / 总览卡片标题 → 改一次配置全线生效。
        "displayNames": dict(DISPLAY_NAMES),
        "ui": dict(UI_CFG),
        "userCfg": {
            "path": USER_CFG_PATH,
            "loaded": bool(USER_CFG_PATH),
            "error": USER_CFG_ERROR,
            "overrides": user_cfg_overrides(),
        },
    }


# ══════════════════════════════════════════════════════════════════
#  ★ 外部配置 telemetry_user.json（免打包、重启生效）★
# ══════════════════════════════════════════════════════════════════
# 优先级：json > 本文件内置值。启动在 exe 旁/本文件旁/当前目录找。
# 下划线开头的键是注释不生效；错误键被忽略，不崩。
USER_CFG_NAME = "telemetry_user.json"
USER_CFG_LOADED: Dict[str, object] = {}
USER_CFG_PATH: Optional[str] = None
USER_CFG_ERROR = ""

def user_cfg_paths() -> List[str]:
    """按优先级返回待查找路径（去重）：exe 旁 > 本文件旁 > 当前目录。"""
    cands = []
    if getattr(_sys, "frozen", False):
        cands.append(_os.path.join(_os.path.dirname(_sys.executable), USER_CFG_NAME))
    cands.append(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), USER_CFG_NAME))
    cands.append(_os.path.join(_os.getcwd(), USER_CFG_NAME))
    seen, out = set(), []
    for p in cands:
        k = _os.path.normcase(_os.path.abspath(p))
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out

def _entries(d) -> List[Tuple[str, object]]:
    """取字典真正数据项，跳过下划线开头的注释键。"""
    if not isinstance(d, dict):
        return []
    return [(k, v) for k, v in d.items() if not str(k).startswith("_")]

def snapshot_builtin() -> None:
    """给「出厂默认值」拍快照（本文件所有区定义完后自动调）；restore_builtin() 的依赖。"""
    global _BUILTIN
    _BUILTIN = {
        "gaugeRanges": dict(GAUGE_RANGES), "units": dict(UNITS),
        "serial": dict(SERIAL), "ui": dict(UI_CFG),
        "channels": [dict(c) for c in ADC_CHANNELS],
        "accRawFullscale": ACC_RAW_FULLSCALE,
        "adc12bitOrder": ADC_12BIT_ORDER, "adcOrder": ADC_ORDER, "motorEndian": MOTOR_ENDIAN,
        "fieldDecimals": dict(FIELD_DECIMALS), "fieldZero": dict(FIELD_ZERO),
        "defaultDecimals": DEFAULT_DECIMALS, "adcDecimals": ADC_DECIMALS,
        "fields": {tid: [tuple(f) for f in spec["fields"]]
                   for tid, spec in PROTOCOL.items() if "fields" in spec},
    }

_BUILTIN: Dict[str, object] = {}

def restore_builtin() -> None:
    """先整体回滚到本文件默认值，再叠加外部值（幂等加载关键）。"""
    global ACC_RAW_FULLSCALE, ADC_12BIT_ORDER, MOTOR_ENDIAN
    global DEFAULT_DECIMALS, ADC_DECIMALS, ADC_ORDER
    if not _BUILTIN:
        return
    FIELD_ZERO.clear();    FIELD_ZERO.update(_BUILTIN["fieldZero"])
    MOTOR_ENDIAN = _BUILTIN.get("motorEndian", MOTOR_ENDIAN)
    ADC_ORDER = _BUILTIN.get("adcOrder", ADC_ORDER)
    FIELD_DECIMALS.clear(); FIELD_DECIMALS.update(_BUILTIN["fieldDecimals"])
    DEFAULT_DECIMALS = _BUILTIN["defaultDecimals"]
    ADC_DECIMALS = _BUILTIN["adcDecimals"]
    GAUGE_RANGES.clear();  GAUGE_RANGES.update(_BUILTIN["gaugeRanges"])
    UNITS.clear();         UNITS.update(_BUILTIN["units"])
    SERIAL.clear();        SERIAL.update(_BUILTIN["serial"])
    UI_CFG.clear();        UI_CFG.update(_BUILTIN["ui"])
    ADC_12BIT_ORDER = _BUILTIN.get("adc12bitOrder", ADC_12BIT_ORDER)
    for i, ch in enumerate(_BUILTIN["channels"]):
        if i < len(ADC_CHANNELS):
            ADC_CHANNELS[i] = dict(ch)
    ACC_RAW_FULLSCALE = _BUILTIN["accRawFullscale"]
    for tid, fields in _BUILTIN["fields"].items():
        spec = PROTOCOL.get(tid)
        if isinstance(spec, dict) and "fields" in spec:
            spec["fields"] = [tuple(f) for f in fields]

def _apply_user_cfg(d: dict) -> None:
    """把外部配置合并进各区（只覆盖出现过的键）；调用前须先 restore_builtin()。"""
    global ACC_RAW_FULLSCALE, ADC_12BIT_ORDER, MOTOR_ENDIAN
    global DEFAULT_DECIMALS, ADC_DECIMALS, ADC_ORDER
    CFG_NOTES.clear()
    for k, v in _entries(d.get("decimals")):
        n = _clamp_decimals(v)
        if n is None:
            CFG_NOTES.append("decimals.%s = %r 不是整数，已忽略（保留内置值）" % (k, v))
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool) and n != v:
            CFG_NOTES.append("decimals.%s = %r 超出 [%d,%d]，已夹到 %d"
                             % (k, v, DECIMALS_MIN, DECIMALS_MAX, n))
        FIELD_DECIMALS[str(k)] = n
    if isinstance(d.get("defaultDecimals"), int):
        n = _clamp_decimals(d["defaultDecimals"])
        if n is not None:
            if n != d["defaultDecimals"]:
                CFG_NOTES.append("defaultDecimals = %r 超出 [%d,%d]，已夹到 %d"
                                 % (d["defaultDecimals"], DECIMALS_MIN, DECIMALS_MAX, n))
            DEFAULT_DECIMALS = n
    if "adcDecimals" in d and (d.get("adcDecimals") is None or isinstance(d.get("adcDecimals"), int)):
        raw = d["adcDecimals"]
        if raw is None:
            ADC_DECIMALS = None
        else:
            n = _clamp_decimals(raw)
            if n is not None:
                if n != raw:
                    CFG_NOTES.append("adcDecimals = %r 超出 [%d,%d]，已夹到 %d"
                                     % (raw, DECIMALS_MIN, DECIMALS_MAX, n))
                ADC_DECIMALS = n
    _end = d.get("motorEndian")
    if not isinstance(_end, str):
        pro = d.get("protocol")
        if isinstance(pro, dict):
            _end = pro.get("motorEndian")
    if isinstance(_end, str) and _end.strip() in ("<", ">"):
        MOTOR_ENDIAN = _end.strip()
    _ord = d.get("adc12bitOrder")
    if not isinstance(_ord, str):
        pro = d.get("protocol")
        if isinstance(pro, dict):
            _ord = pro.get("adc12bitOrder")
    if isinstance(_ord, str) and _ord.strip().lower() in ("lsb", "msb", "lsb_swap", "msb_swap"):
        ADC_12BIT_ORDER = _ord.strip().lower()
    _ao = d.get("adcOrder")
    if isinstance(_ao, list) and _ao:
        try:
            ADC_ORDER = [int(v) for v in _ao]
        except (TypeError, ValueError):
            pass
    for k, v in _entries(d.get("gaugeRanges")):
        key = str(k)
        if v is None:
            GAUGE_RANGES[key] = None
            continue
        if isinstance(v, (list, tuple)) and len(v) == 2:
            # ★ 区间允许负下界（ROLL=[-180,180] / PITCH=[-90,90] 就是负区间）；
            #   只要求两端有限、且上界 > 下界（拒绝 [-180,-180]、[600,1] 这类退化/反序值）
            lo, hi = _finite(v[0]), _finite(v[1])
            if lo is None or hi is None or hi <= lo:
                CFG_NOTES.append("gaugeRanges.%s = %r 不是合法的 [低,高] 区间（需有限且 高>低），已忽略（保留内置值）"
                                 % (key, v))
                continue
            GAUGE_RANGES[key] = [lo, hi]
            continue
        f = _positive(v)
        if f is None:
            CFG_NOTES.append("gaugeRanges.%s = %r 不是正数，已忽略（保留内置值）" % (key, v))
            continue
        GAUGE_RANGES[key] = f
    for k, v in _entries(d.get("units")):
        UNITS[str(k)] = str(v)
    wanted = {}
    for k, v in _entries(d.get("scales")):
        f = _positive(v)
        if f is None:
            # ★ task-22：负 scale 会让界面显示"满量程 ±1e-12"、矢量图永远超量程
            CFG_NOTES.append("scales.%s = %r 不是正数，已忽略（保留内置 scale）" % (k, v))
            continue
        wanted[str(k)] = f
    if wanted:
        for tid, spec in PROTOCOL.items():
            if "fields" not in spec:
                continue
            newf = [tuple(list(f[:3]) + [wanted[f[0]]] + list(f[4:])) if f[0] in wanted and len(f) >= 4
                    else f for f in spec["fields"]]
            spec["fields"] = newf
    for k, v in _entries(d.get("zeros")):
        try:
            FIELD_ZERO[str(k)] = 0.0 if v is None else float(v)
        except (TypeError, ValueError):
            pass
    _az = d.get("adcZero")
    if isinstance(_az, dict):
        for i, ch in enumerate(ADC_CHANNELS):
            v = _az.get(ch.get("short")) or _az.get(ch.get("name")) or _az.get("CH%d" % (i + 1))
            if v is None:
                continue
            try:
                ch["zero"] = 0.0 if v is None else float(v)
            except (TypeError, ValueError):
                pass
    for i, patch in enumerate(d.get("channels") or []):
        if i >= len(ADC_CHANNELS) or not isinstance(patch, dict):
            continue
        for key, val in _entries(patch):
            if key in ("name", "unit", "rng", "color", "short", "zero"):
                ADC_CHANNELS[i][key] = val
    for k, v in _entries(d.get("serial")):
        SERIAL[str(k)] = v
    for k, v in _entries(d.get("ui")):
        UI_CFG[str(k)] = v
    # ★ task-22：bufPoints 上下都要夹。1e8 会让前端 BUF 不再 splice（21 路×20Hz 无限增长）
    #   且后端回填一次要造 1e8 个槽 → GUI 卡死 / OOM；下限保持 0/负值不被接受。
    if "bufPoints" in UI_CFG:
        raw = UI_CFG["bufPoints"]
        try:
            bp = int(raw)
        except (TypeError, ValueError):
            bp = None
        if bp is None:
            del UI_CFG["bufPoints"]
            CFG_NOTES.append("ui.bufPoints = %r 不是整数，已忽略（保留内置值）" % (raw,))
        else:
            bp = max(UI_BUFPOINTS_MIN, min(UI_BUFPOINTS_MAX, bp))
            if isinstance(raw, (int, float)) and not isinstance(raw, bool) and bp != raw:
                CFG_NOTES.append("ui.bufPoints = %r 超出 [%d,%d]，已夹到 %d"
                                 % (raw, UI_BUFPOINTS_MIN, UI_BUFPOINTS_MAX, bp))
            UI_CFG["bufPoints"] = bp
    if isinstance(d.get("accRawFullscale"), (int, float)) and not isinstance(d.get("accRawFullscale"), bool):
        f = _positive(d["accRawFullscale"])
        if f is None:
            CFG_NOTES.append("accRawFullscale = %r 不是正数，已忽略（保留内置值）"
                             % (d["accRawFullscale"],))
        else:
            ACC_RAW_FULLSCALE = f

def load_user_cfg() -> Optional[str]:
    """加载外部配置，返回实际路径（None=无外部配置）；幂等：先 restore_builtin 再叠加。"""
    global USER_CFG_LOADED, USER_CFG_PATH, USER_CFG_ERROR
    USER_CFG_LOADED, USER_CFG_PATH, USER_CFG_ERROR = {}, None, ""
    restore_builtin()
    for p in user_cfg_paths():
        if not _os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as fh:
                d = _json.load(fh)
            if not isinstance(d, dict):
                raise ValueError("文件内容必须是 JSON 对象")
            _apply_user_cfg(d)
            refresh()
            USER_CFG_LOADED, USER_CFG_PATH = d, p
            return p
        except Exception as e:
            USER_CFG_ERROR = "%s 加载失败：%s（已按内置配置运行）" % (p, e)
            return None
    return None

def user_cfg_overrides() -> Dict[str, object]:
    """外部文件真正生效的键（按分区归类），给界面标注「值来自文件」。"""
    out: Dict[str, object] = {}
    for sec in ("gaugeRanges", "units", "scales", "serial", "ui"):
        block = USER_CFG_LOADED.get(sec)
        if isinstance(block, dict):
            keys = sorted(str(k) for k, _v in _entries(block))
            if keys:
                out[sec] = keys
    chs = USER_CFG_LOADED.get("channels")
    if isinstance(chs, list):
        idx = [i for i, p in enumerate(chs) if isinstance(p, dict) and _entries(p)]
        if idx:
            out["channels"] = idx
    return out

def reset_user_cfg() -> bool:
    """清空外部配置里所有生效键，回到纯内置配置（只删数据、保留说明文字）。"""
    path = USER_CFG_PATH or user_cfg_paths()[0]
    if not _os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = _json.load(fh)
        if not isinstance(d, dict):
            return False
        for sec in ("gaugeRanges", "units", "scales", "serial", "ui"):
            block = d.get(sec)
            if isinstance(block, dict):
                d[sec] = dict((k, v) for k, v in block.items() if str(k).startswith("_"))
        if isinstance(d.get("channels"), list):
            d["channels"] = [(p if not isinstance(p, dict)
                              else dict((k, v) for k, v in p.items()
                                        if str(k).startswith("_"))) for p in d["channels"]]
        with open(path, "w", encoding="utf-8") as fh:
            _json.dump(d, fh, ensure_ascii=False, indent=2)
        load_user_cfg()
        return True
    except Exception:
        return False

def write_user_cfg_template(path: Optional[str] = None) -> Optional[str]:
    """生成带说明的模板（已存在则不覆盖）。"""
    target = path or user_cfg_paths()[0]
    if _os.path.exists(target):
        return None
    tpl = {
        "_说明": [
            "★ 本文件优先级高于 telemetry_config.py：保存、重启即生效，不用重新打包。",
            "★ 所有键可选，用不到的整段删掉，程序自动用内置值。",
        ],
        "_重要": "带下划线前缀的键是说明文字，不生效；启用示例键：删掉其名下划线和'示例_'。",
        "gaugeRanges": {
            "_用法": "界面显示量程（显示单位满量程）。ACCEL 填 null=自动按 原始满量程×scale；"
                     "★ 必须为正数或 [低,高] 正区间，非正数会被忽略并回退内置值。",
            "_信号名": ["SPEED", "MOTOR_RPM", "TORQUE", "MOTOR_TEMP", "VOLTAGE", "CURRENT",
                       "THROTTLE", "ACCEL", "ACCEL_LAT", "ACCEL_LON", "ROLL", "PITCH", "YAW"],
            "_示例_SPEED": 40.0, "_示例_VOLTAGE": 600.0, "_示例_MOTOR_RPM": 10000.0,
        },
        "units": {"_用法": "界面单位后缀，键用第4区信号名", "_示例_SPEED": "m/s", "_示例_ACCEL": "g"},
        "scales": {"_用法": "显示值=原始码×scale；改了组帧与解码同步变，务必与固件对齐。★ 必须为正数，非正数会被忽略并回退内置值",
                   "_示例_SPEED": 1.0},
        "decimals": {"_用法": "界面/CSV 小数位，★ 与 scale 无关；取值范围 0~20（超过 20 会被夹到 20）",
                     "_示例_SPEED": 2, "_示例_MOTOR_RPM": 0},
        "_defaultDecimals": "没列到的信号用几位小数（内置 2；同样夹在 0~20）",
        "_adcDecimals": "ADC 各通道小数位（内置 1；null=用 defaultDecimals；同样夹在 0~20）",
        "channels": ["_用法：第 1 个 ADC 通道显示属性，按格式往下排，只写前几个也行",
                     {"_示例_name": "前左悬架", "_示例_unit": "mm", "_示例_rng": 50.0}],
        "serial": {"_用法": "串口默认值", "_示例_default_baud": 115200},
        "ui": {"_用法": "曲线缓冲/取样/帧率/默认曲线；★ bufPoints 取值 10~20000（超出会被夹紧）",
               "_示例_bufPoints": 600,
               "_示例_defaultVisible": ["rpm", "volt", "amp", "accelLat", "accelLon", "accelMag"]},
    }
    try:
        with open(target, "w", encoding="utf-8") as fh:
            _json.dump(tpl, fh, ensure_ascii=False, indent=2)
        return target
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
#  ★ 配置体检（python telemetry_config.py 可见）★
# ══════════════════════════════════════════════════════════════════
_STRUCT_SIZE = {"<B": 1, "<b": 1, "<H": 2, "<h": 2, "<I": 4, "<i": 4, "<f": 4, "<d": 8}

def layout_notes() -> List[str]:
    """非致命布局提醒（声明顺序≠偏移顺序、数据区空洞）；解析不受影响。"""
    notes: List[str] = []
    for type_id, spec in PROTOCOL.items():
        if "fields" not in spec:
            continue
        tag = "第 4 区：0x%02X(%s)" % (type_id, spec.get("name", "?"))
        fields = spec["fields"]
        total = int(spec.get("total", 0))
        crc_at = total - 4
        offs = [f[1] for f in fields]
        if any(offs[k] > offs[k + 1] for k in range(len(offs) - 1)):
            by_off = "、".join(f[0] for f in sorted(fields, key=lambda f: f[1]))
            notes.append("%s：fields 声明序与偏移序不一致，解析不受影响；建议按偏移升序：%s"
                         % (tag, by_off))
        used = set()
        for name, off, fmt, _scale in fields:
            for b in range(off, off + _STRUCT_SIZE.get(fmt, 0)):
                used.add(b)
        holes = [b for b in range(8, crc_at) if b not in used]
        if holes:
            notes.append("%s：数据区 %d~%d 有 %d 字节未用（偏移 %s%s），组帧填 0；非固件保留字节请核对偏移"
                         % (tag, 8, crc_at - 1, len(holes),
                            ", ".join(str(b) for b in holes[:8]), "…" if len(holes) > 8 else ""))
    # ★ task-22：外部配置里被忽略/被夹紧的键在这里如实报出（否则用户以为改生效了）
    notes.extend(CFG_NOTES)
    return notes

def validate() -> List[str]:
    """返回配置问题列表，空=自洽。"""
    problems: List[str] = []
    if not ADC_CHANNELS:
        problems.append("第 3 区：ADC_CHANNELS 是空的")
    for i, c in enumerate(ADC_CHANNELS):
        if c.get("short") != "CH%d" % (i + 1):
            problems.append("第 3 区：第 %d 通道 short 应为 'CH%d'，实际 '%s'（写错会永远读到 0）"
                            % (i + 1, i + 1, c.get("short")))
        zv = c.get("zero", 0)
        if zv is not None:
            try:
                zf = float(zv)
            except (TypeError, ValueError):
                problems.append("第 3 区：通道 %s 的 zero=%r 不是数字" % (c.get("short"), zv))
            else:
                if zf < 0 or zf > 4095:
                    problems.append("第 3 区：通道 %s 的 zero=%g 超出 12bit 码值 0~4095（zero 是原始码，非工程量）"
                                    % (c.get("short"), zf))
    if ADC_ORDER is not None:
        try:
            _seq = [int(v) for v in ADC_ORDER]
        except (TypeError, ValueError):
            problems.append("第 3 区：ADC_ORDER 只能是整数通道号列表，如 [5,6,7,8,9,10,1,2,3,4]")
            _seq = []
        if _seq:
            _bad = [v for v in _seq if v < 1 or v > ADC_COUNT]
            if _bad:
                problems.append("第 3 区：ADC_ORDER 越界通道号 %s（合法 1~%d）" % (_bad[:5], ADC_COUNT))
            _dup = sorted({v for v in _seq if _seq.count(v) > 1})
            if _dup:
                problems.append("第 3 区：ADC_ORDER 通道号重复：%s" % _dup)
            if len(_seq) != ADC_COUNT:
                problems.append("第 3 区：ADC_ORDER 有 %d 项，但共 %d 通道" % (len(_seq), ADC_COUNT))
    for type_id, spec in PROTOCOL.items():
        tag = "第 4 区：0x%02X(%s)" % (type_id, spec.get("name", "?"))
        total = spec.get("total", 0)
        fields = spec.get("fields", [])
        if "packed_len" in spec:
            need = 8 + spec["packed_len"] + 4
            if total != need:
                problems.append("%s total=%d 但应=%d（8 头+%d 打包区+4 CRC尾）"
                                % (tag, total, need, spec["packed_len"]))
            continue
        if not fields:
            problems.append("%s 没有 fields" % tag)
            continue
        spans = []
        for name, off, fmt, scale in fields:
            size = _STRUCT_SIZE.get(fmt)
            if size is None:
                problems.append("%s 字段 %s 的 struct 格式 '%s' 不支持" % (tag, name, fmt))
                continue
            if off < 8:
                problems.append("%s 字段 %s 偏移 %d 落在帧头区（应 ≥ 8）" % (tag, name, off))
            end = off + size
            if end + 4 > total:
                problems.append("%s 字段 %s 占 %d~%d，加 CRC+尾后超出 total=%d" % (tag, name, off, end - 1, total))
            spans.append((off, end, name))
            if scale == 0:
                problems.append("%s 字段 %s 的 scale=0（所有值算成 0）" % (tag, name))
        spans.sort()
        for k in range(1, len(spans)):
            if spans[k][0] < spans[k - 1][1]:
                problems.append("%s 字段 %s 与 %s 字节区间重叠（%d~%d 撞 %d~%d）"
                                % (tag, spans[k - 1][2], spans[k][2],
                                   spans[k - 1][0], spans[k - 1][1] - 1,
                                   spans[k][0], spans[k][1] - 1))
        cursor = max((e for _o, e, _n in spans), default=8)
        if cursor > total - 4:
            problems.append("%s 数据区最远写到第 %d 字节，超 CRC+尾起点 %d" % (tag, cursor - 1, total - 4))
        names = [f[0] for f in fields]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            problems.append("%s 字段名重复：%s" % (tag, ", ".join(sorted(dup))))
    if SERIAL["default_baud"] not in SERIAL["baud_choices"]:
        problems.append("第 1 区：默认波特率 %s 不在候选列表" % SERIAL["default_baud"])
    if SERIAL["parity"] not in ("N", "E", "O"):
        problems.append("第 1 区：校验位只能是 N / E / O")
    if ADC_DECODE_MODE not in ("raw", "eng"):
        problems.append("第 3 区：ADC_DECODE_MODE 只能是 'raw' / 'eng'")
    if ADC_12BIT_ORDER not in ("msb", "lsb", "lsb_swap", "msb_swap"):
        problems.append("第 3 区：ADC_12BIT_ORDER 只能是 msb/lsb/msb_swap/lsb_swap")
    if TAIL_MODE not in ("reversed", "custom"):
        problems.append("第 2 区：TAIL_MODE 只能是 'reversed' / 'custom'")
    # ── 第 ⑥ 区：界面显示名宏（DISPLAY_NAMES）自洽检查 ──
    # 目标：让"改显示名"这件事本身不会把别处改坏。
    if not DISPLAY_NAMES:
        problems.append("第 6 区：DISPLAY_NAMES 是空的（界面会退回内置默认文字）")
    _known_ids = {FRONTEND_KEYS.get(f[0], f[0].lower())
                  for tid in (0x01, 0x02) for f in PROTOCOL[tid]["fields"]}
    _known_ids |= {"accelLat", "accelLon", "accelMag", "power"}   # 前端派生量
    _bad = sorted(k for k in DISPLAY_NAMES if k not in _known_ids)
    if _bad:
        problems.append("第 6 区：DISPLAY_NAMES 里有前端不认识的 id：%s"
                        "（可用：%s）" % (", ".join(_bad), ", ".join(sorted(_known_ids))))
    _empty = sorted(k for k, v in DISPLAY_NAMES.items() if not str(v).strip())
    if _empty:
        problems.append("第 6 区：DISPLAY_NAMES 的显示名为空：%s" % ", ".join(_empty))
    _dupn = {}
    for _k, _v in DISPLAY_NAMES.items():
        _dupn.setdefault(str(_v).strip(), []).append(_k)
    _clash = {v: ks for v, ks in _dupn.items() if len(ks) > 1}
    if _clash:
        problems.append("第 6 区：DISPLAY_NAMES 显示名重复（界面上会分不清）：%s"
                        % "; ".join("%s=%s" % (v, "/".join(ks)) for v, ks in _clash.items()))
    # 协议字段名（内部键）若被改成中文，前端/CSV/旧录播兼容都会静默失效 —— 提前拦住
    _non_ascii = sorted({f[0] for tid in (0x01, 0x02) for f in PROTOCOL[tid]["fields"]
                         if not f[0].isascii()})
    if _non_ascii:
        problems.append("第 4/5 区：协议字段名只能是英文标识符，发现：%s"
                        "（中文名请写到第 6 区 DISPLAY_NAMES，否则 CSV/曲线/旧录播会失效）"
                        % ", ".join(_non_ascii))
    return problems


# ══════════════════════════════════════════════════════════════════
#  自动加载：import 时先拍快照再叠加外部值
# ══════════════════════════════════════════════════════════════════
snapshot_builtin()
load_user_cfg()


# ══════════════════════════════════════════════════════════════════
#  直接运行：打印派生结果 + 体检
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # 编码自保：GBK 控制台下直接 print 中文/特殊符号会抛 UnicodeEncodeError
    try:
        for _s in (_sys.stdout, _sys.stderr):
            if "utf" not in (getattr(_s, "encoding", "") or "").lower():
                _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("=" * 66)
    print("  遥测配置体检报告")
    print("=" * 66)
    print("ADC 通道数 ：", ADC_COUNT)
    print("串口默认   ：", SERIAL["default_baud"], SERIAL["bytesize"], SERIAL["parity"], SERIAL["stopbits"])
    print("帧头 / 帧尾：", HEADER.hex(" ").upper(), "/", TAIL.hex(" ").upper(), "（模式 %s）" % TAIL_MODE)
    print("CRC        ：", CRC_NAME, "覆盖", CRC_RANGE)
    print("CSV 列数   ：", len(csv_columns()))
    print("CSV 表头   ：", CSV_HEADER)

    print("\n各字段换算（显示值 =（原始码 − zero）× scale）：")
    for type_id in sorted(PROTOCOL):
        spec = PROTOCOL[type_id]
        if "fields" not in spec:
            continue
        print("  [%s]" % spec["name"])
        for f in spec["fields"]:
            z = field_zero(f[0], f)
            print("    %-12s 偏移 %2d  %-3s  scale=%-10g zero=%-6g CSV 格式 %s"
                  % (f[0], f[1], f[2], f[3], z, _fmt_for(f)))

    print("\nADC 零点与显示范围（zero = 零点码值，0~4095）：")
    for i, c in enumerate(ADC_CHANNELS):
        z = adc_zero_code(i)
        lo, hi = adc_display_range(i)
        lo = 0.0 if abs(lo) < 1e-12 else lo
        mid = 4095.0 / 2.0
        tag = ""
        if abs(z) < 1e-9:
            tag = "（未偏移；若零点在中位可设 zero=%.0f）" % mid
        elif abs(lo + hi) < 1e-6:
            tag = "（零点在正中，双向 ±%.4g）" % hi
        else:
            tag = "（零点偏一侧）"
        print("  %-4s %-14s zero=%-6g 显示 %10.4g ~ %-10.4g %-4s %s"
              % (c["short"], c["name"], z, lo, hi, c["unit"] or "", tag))

    problems = validate()
    print("\n" + "=" * 66)
    if problems:
        print("  [FAIL] 配置有问题 %d 处：" % len(problems))
        for p in problems:
            print("   · " + p)
    else:
        print("  [OK] 配置体检通过：没有发现重叠、越界或矛盾")
    for n in layout_notes():
        print("  [!] " + n)
    print("=" * 66)
