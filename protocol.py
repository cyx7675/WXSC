# -*- coding: utf-8 -*-
# ============================================================================
# ⚠️ 本文件只放【算法】：CRC 计算、12bit 拆包、帧扫描解析、组帧。
#    协议里会变的「数据」（包头、通道表、字段表、量程、解码模式）
#    已全部集中到 telemetry_config.py —— ★改协议请改那个文件，不用动这里★
#
#    本文件对外暴露的名字保持兼容，旧代码 import 后用法完全不变：
#      HEADER / TAIL / SYNC / PROTOCOL / ADC_CHANNELS / ADC_FULLSCALE /
#      ADC_DECODE_MODE / ADC_12BIT_ORDER / UNITS / *_SIGNALS /
#      crc16 / unpack_12bit / pack_12bit / parse_stream / build_*_frame
# ============================================================================
"""
protocol.py -- 无线数传上位机 协议解析层  v2.2
============================================================

严格依据用户提供的真实固件结构体（2026-09-29 最新版，三种包）：

  // ---- ADC 包（27B，100Hz，保持不变）----
  typedef struct {
    uint16_t header;          // 0xAA55  -> 线上字节 55 AA
    uint8_t  type;            // 包类型: 0 = ADC
    uint8_t  seq;             // 包序号
    uint32_t timestamp;       // ms 时间戳（F4 接收完成时刻，统一 F4 基准）
    uint8_t  adc_packed[15];  // 10 路 ADC 值，12bit MSB-first 打包 = 120bit
    uint16_t crc16;           // CRC-16/MODBUS（header 到 adc_packed）
    uint16_t tail;            // 0x55AA  -> 线上字节 AA 55
  } tx_adc_packet_t;          // = 27 字节

  // ---- NAV 包（20B，25Hz；旧 28B/50Hz 的精简版：删 lat/lon/speed，加 accel）----
  typedef struct {
    uint16_t header;          // 0xAA55
    uint8_t  type;            // 包类型: 1 = NAV
    uint8_t  seq;
    uint32_t timestamp;       // ms（统一 F4 基准）
    int16_t  roll, pitch, yaw;// 姿态角 ×100，0.01° 精度，±327.67°
    int16_t  accel;           // 加速度 raw，有符号（U34 定标），25Hz
    uint16_t crc16;           // CRC-16/MODBUS（header 到 accel）
    uint16_t tail;            // 0x55AA
  } tx_nav_packet_t;          // = 20 字节

  // ---- MOTOR 包（23B，50Hz，v2 新增）----
  // 为什么不并进 NAV：NAV 20B + 电机 11B = 31B > E34 半双工单帧 29B 上限，
  // 会被拆成 29+2 两个空包，空中开销翻倍。拆成独立包后两个都 ≤29B。
  typedef struct {
    uint16_t header;          // 0xAA55
    uint8_t  type;            // 包类型: 2 = MOTOR
    uint8_t  seq;
    uint32_t timestamp;       // ms（与同组 NAV 包相同）
    uint8_t  motor_temp;      // 电机温度   raw
    uint16_t motor_rpm;       // 电机转速   raw
    uint8_t  torque;          // 扭矩       raw
    uint8_t  throttle;        // 油门开度   raw
    uint16_t voltage;         // 电压       raw
    uint16_t current;         // 电流       raw
    int16_t  speed;           // 车速 ×100，0.01 m/s —— 与 motor_rpm 同包同
                              // 时间戳，上位机可直接算滑移率判断打滑
    uint16_t crc16;           // CRC-16/MODBUS（header 到 speed）
    uint16_t tail;            // 0x55AA
  } tx_motor_packet_t;        // = 23 字节

解码说明（按用户明确要求）：
  「数据都是解析完的，不同的只需要转化成十进制」——固件已经把传感器量算成了
  工程量（或等价的 12bit 码），上位机只需把二进制转为 10 进制显示，
  **不做任何电压/量程换算**。×100 编码字段（姿态角、车速）仅做 ÷100 还原。
  因此默认 ADC_DECODE_MODE = "raw"：解包出的 12bit 整数(0~4095)直接作为显示值。

  ★ 如需显示真实工程量(mm/°/kg)，把 ADC_DECODE_MODE 改为 "eng"，会用各通道
    量程(ADC_FULLSCALE)做 12bit码 -> 工程量 的线性还原。两种模式都只需改这一行。
"""

from __future__ import annotations
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# ----------------------------------------------------------------------------
# 0) 【配置导入】下面这些名字全部定义在 telemetry_config.py 里，
#    在这里重新导入，保证旧代码 `import protocol; protocol.PROTOCOL` 依然可用。
#
#    ADC_DECODE_MODE : "raw"=12bit 码值直显 / "eng"=按量程换算成工程量
#    ADC_FULLSCALE   : 各通道量程上限（仅 eng 模式用）
#    ADC_12BIT_ORDER : 10 路 12bit 的打包顺序，"msb" 或 "lsb"
#    ADC_CHANNELS    : 通道总表（短名/中文名/单位/量程/颜色）
#    PROTOCOL        : 三类包的字段表与帧长
#    UNITS / *_SIGNALS : 单位表与信号分组（UI 用）
# ----------------------------------------------------------------------------
import telemetry_config as _CFG          # noqa: E402  ★ 运行期动态读配置（见 _adc_order）

from telemetry_config import (           # noqa: E402  (必须在 future 之后)
    FRAME_HEADER_U16, HEADER, TAIL, SYNC, CRC_NAME, CRC_CUSTOM,
    ADC_CHANNELS, ADC_COUNT, ADC_FULLSCALE, ADC_DECODE_MODE, ADC_12BIT_ORDER,
    PROTOCOL, UNITS, ADC_SIGNALS, NAV_SIGNALS, MOTOR_SIGNALS,
)


# ----------------------------------------------------------------------------
# 1) CRC16（通用实现，默认参数 = CRC-16/MODBUS）
# ----------------------------------------------------------------------------
def crc16(data: bytes, poly: int = 0x8005, init: int = 0xFFFF,
          refin: bool = True, refout: bool = True) -> int:
    """通用 CRC16。默认参数实现 CRC-16/MODBUS（与固件一致）。"""
    crc = init & 0xFFFF

    def _reflect(x: int, n: int) -> int:
        res = 0
        for i in range(n):
            if x & (1 << i):
                res |= 1 << (n - 1 - i)
        return res

    for b in data:
        if refin:
            b = _reflect(b, 8)
        crc ^= (b << 8)
        crc &= 0xFFFF
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ poly) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    if refout:
        crc = _reflect(crc, 16)
    return crc & 0xFFFF


# ★ CRC 算法由 telemetry_config.py 第 2 区的 CRC_NAME 决定，不在这里写死。
CRC_PRESETS = {
    "crc16_modbus": dict(poly=0x8005, init=0xFFFF, refin=True,  refout=True),
    "crc16_ccitt":  dict(poly=0x1021, init=0xFFFF, refin=False, refout=False),
    "crc16_xmodem": dict(poly=0x1021, init=0x0000, refin=False, refout=False),
    "crc16_kermit": dict(poly=0x1021, init=0x0000, refin=True,  refout=True),
}


def crc_params():
    """按配置取 CRC 参数；配成 "none" 时返回 None（不做校验）。"""
    name = (CRC_NAME or "none").lower()
    if name == "none":
        return None
    if name == "custom":
        return dict(CRC_CUSTOM)
    p = CRC_PRESETS.get(name)
    if p is None:
        raise ValueError("未知的 CRC 算法：%r（可选 %s / custom / none）"
                         % (CRC_NAME, " / ".join(sorted(CRC_PRESETS))))
    return p


def frame_crc(data: bytes):
    """算一帧的 CRC；配置关掉校验时返回 None（调用方自行跳过比对）。"""
    p = crc_params()
    return None if p is None else crc16(data, **p)


# ----------------------------------------------------------------------------
# 2) 帧头 / 帧尾（统一信封：tail 永远是 header 的字节逆序）
# ----------------------------------------------------------------------------
# ★ 所有数据帧共用同一信封（用户 2026-07-31 确认）：
#     开头 = 包头 ；结尾的标志位 = 包头的字节逆序。
#   例：包头 0xAA55 -> 线上 55 AA ；包尾 0x55AA -> 线上 AA 55。
#   因此只需定义一次 header，tail 由 bytes(reversed(...)) 推导，二者关系
#   在代码里被强制成立，未来改包头时包尾自动跟着对。
# 信封常量（HEADER/TAIL/SYNC/FRAME_HEADER_U16）已移到 telemetry_config.py 第 1 区，
# 并在文件头统一 import。TAIL 由 HEADER 反序推导，永远不会出现只改一半的情况。


# ----------------------------------------------------------------------------
# 3) 12bit 打包 / 解包（10 路 -> 15 字节）
# ----------------------------------------------------------------------------
# 字节序由 ADC_12BIT_ORDER 控制（见 telemetry_config.py 第 3 区）：
#   lsb      : 第 i 路占打包区从最低位起的第 i 个 12bit 组
#   msb      : 第 i 路占从最高位起的第 i 个 12bit 组
#   lsb_swap / msb_swap : 先按上面解，再**每 3 字节组内两路互换**
#        （CH1↔CH2、CH3↔CH4 …）。固件把 (V2,V1) 打进一组时用它。
#
# ★ 顺序在这里**动态**从配置模块读，不能用文件头 from-import 的那个名字：
#   那是 import 瞬间绑定的字符串，运行期改配置（界面切换 / 点「恢复内置」）
#   它不会跟着变，会出现「界面显示已切换、解码还是旧的」这种鬼故事。
# 解包与打包共用同一顺序，改一个常量、收发两端一致。
def _adc_order() -> Tuple[str, bool]:
    """-> (基础序 'lsb'/'msb', 是否组内互换)"""
    o = str(_CFG.ADC_12BIT_ORDER or "lsb").strip().lower()
    if o not in ("lsb", "msb", "lsb_swap", "msb_swap"):
        o = "lsb"
    return (o[:-5] if o.endswith("_swap") else o), o.endswith("_swap")


def _swap_pairs(vals: List[int]) -> List[int]:
    """每 3 字节组内的两路互换：(0,1) (2,3) … 奇数个时最后一个不动。"""
    out = list(vals)
    for i in range(0, len(out) - 1, 2):
        out[i], out[i + 1] = out[i + 1], out[i]
    return out


def unpack_12bit(packed: bytes, n: int) -> List[int]:
    """从 n*12/8 字节里解 n 个 12bit 无符号整数。"""
    assert len(packed) * 8 >= n * 12, "打包区字节数不足以容纳 n 个 12bit"
    base, swap = _adc_order()
    if base == "lsb":
        buf = int.from_bytes(packed, "little")
        vals = [(buf >> (i * 12)) & 0xFFF for i in range(n)]
    else:
        buf = int.from_bytes(packed, "big")
        bits = n * 12
        vals = [(buf >> (bits - 12 * (i + 1))) & 0xFFF for i in range(n)]
    return _swap_pairs(vals) if swap else vals


def pack_12bit(values: List[int], n: int) -> bytes:
    """把 n 个 12bit 整数(0~4095)打包成 n*12/8 字节（与 unpack 互为逆运算）。"""
    nbytes = (n * 12 + 7) // 8
    base, swap = _adc_order()
    vals = _swap_pairs(list(values[:n])) if swap else list(values[:n])
    buf = 0
    if base == "lsb":
        for i, v in enumerate(vals):
            buf |= (int(round(v)) & 0xFFF) << (i * 12)
        return buf.to_bytes(nbytes, "little")
    bits = n * 12
    for i, v in enumerate(vals):
        buf |= (int(round(v)) & 0xFFF) << (bits - 12 * (i + 1))
    return buf.to_bytes(nbytes, "big")


# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# 5) 协议描述  →  已迁移到 telemetry_config.py 第 3 区
# ----------------------------------------------------------------------------
# 三类包的字段表 / 帧长 / 偏移量 / 系数全部集中在配置文件里，
# 本文件通过文件头的 import 拿到 PROTOCOL 对象，用法不变。
# 想加字段、改偏移、改系数，请打开 telemetry_config.py 的「第 3 区」。
# ----------------------------------------------------------------------------


# ----------------------------------------------------------------------------
# 6) 帧扫描解析器（滑窗，容忍串口读取截断 / 假同步）
# ----------------------------------------------------------------------------
@dataclass
class ParsedFrame:
    type_id: int
    name: str
    values: Dict[str, float]      # 信号名 -> 物理量（10 进制）
    raw: bytes                    # 整帧原始字节
    seq: Optional[int] = None
    ts: Optional[int] = None


def _field_fmt(spec: dict, fmt: str) -> str:
    """字段的 struct 格式：把字节序换成配置当前的值。

    ★ 组帧与解码**必须**共用这一个函数，否则两端字节序不一致，
      自己组的帧自己解不开（selftest 的往返断言会抓到）。

    spec 里可选的 "endian_key" 指向配置里的某个变量名（如 "MOTOR_ENDIAN"），
    用 getattr **运行期动态读** —— 界面切换后下一帧就生效，
    ⚠️ 千万别写成 from-import 的常量，那是 import 瞬间绑定的值，改配置它不变。

    只对多字节字段生效（H/h/I/i/f…），1 字节的 B/b 换不换都一样。
    """
    key = spec.get("endian_key")
    if not key:
        return fmt
    endian = getattr(_CFG, str(key), None)
    if endian not in ("<", ">", "!", "="):
        return fmt
    return endian + fmt.lstrip("<>=!")


def _decode_frame(type_id: int, spec: dict, frame: bytes) -> ParsedFrame:
    """把一个合法帧解码为物理量字典（仅做进制转换，不做换算）。"""
    values: Dict[str, float] = {}
    seq = None
    ts = None

    seq = struct.unpack(spec["seq"][1], frame[spec["seq"][0]: spec["seq"][0] + 1])[0]
    ts = struct.unpack(spec["ts"][1], frame[spec["ts"][0]: spec["ts"][0] + 4])[0]

    # ★ 按「配置里写了什么」决定怎么解，而不是写死包类型：
    #   有 packed_off 的走 12bit 紧凑解包（ADC 类），有 fields 的走通用字段解码。
    #   所以你在配置第 4 区加一个 0x03 包，这里不用改一行。
    if "packed_off" in spec:                      # ADC 类：12bit 打包
        packed = frame[spec["packed_off"]: spec["packed_off"] + spec["packed_len"]]
        raw12 = unpack_12bit(packed, spec["adc_n"])
        for i in range(spec["adc_n"]):
            # ★ 零点：动态读配置（运行期改 ADC_CHANNELS[i]["zero"] 立即生效）。
            #   零点在中间的传感器（悬架中位 / 转向中位 / 双向力）减掉零点后
            #   才会「零点显示 0、向两边一正一负」。
            z = float(_CFG.adc_zero_code(i))
            # adc_step 动态读：eng = rng/4095（每码工程量），raw = 1.0（码值直显）。
            # ★ 不用 from-import 的 ADC_FULLSCALE —— 那是 import 时快照，
            #   外部 json 改了 rng 之后它不会更新（同族坑：MOTOR_ENDIAN 绑定）。
            val = (raw12[i] - z) * _CFG.adc_step(i)
            # 键名用 ch["short"]（= CH1..CH10），后端按这个键取值，不可随意改
            values[ADC_CHANNELS[i]["short"]] = val

    elif "fields" in spec:                        # 通用：逐个字段解
        for fld in spec["fields"]:
            name, off, fmt, scale = fld[0], fld[1], fld[2], fld[3]
            f = _field_fmt(spec, fmt)             # 字节序可能由配置动态决定
            raw = struct.unpack(f, frame[off: off + struct.calcsize(f)])[0]
            z = float(_CFG.field_zero(name, fld))  # ★ 零点（原始码），默认 0
            values[name] = (float(raw) - z) * scale

    return ParsedFrame(type_id=type_id, name=spec["name"], values=values,
                       raw=bytes(frame), seq=seq, ts=ts)


# ----------------------------------------------------------------------------
# 6.5) 旧 .bin 兼容：NAV 20B 历史布局（2026-10-14 之前的固件 / 录播）
# ----------------------------------------------------------------------------
# 用户 2026-10-14 把 NAV 从 20B（4 字段）改成 22B（5 字段：单路 ACCEL 拆成
# ACCEL_LAT@14 / ACCEL_LON@16）。市面 6 份旧录播全是 20B 帧，按新布局会被 CRC/包尾拒掉
# → 回放里 roll/pitch/yaw/加速度整路为空（不是错值，是彻底没有）。
#
# 因此加一道**双校验兜底**：
#   · 只在 type==0x01 的**当前布局**校验失败时才启用（新协议帧第一次就过，实时路径零额外开销）；
#   · 必须 **CRC + 包尾都通过**才接受 —— 误命中概率 ~2^-32，绝不会错位解出错值；
#   · 按旧布局解出的单路 ACCEL 映射到 ACCEL_LON（旧 UI 那一格语义上就是"纵向/行车方向"），
#     ACCEL_LAT 记 0：宁可少一路，也不猜一个并不存在的横向值。
# 以后若再改 NAV 布局，把上一版的 total 追加进这个元组即可（同名字段按当前配置的
# scale/zero 解历史偏移，所以只改包长类的东西不用动这段代码）。
LEGACY_NAV_TOTALS = (20,)            # 历史 NAV 包长（TAIL 在 total-2，CRC 在 total-4）
LEGACY_NAV_ACCEL_OFF = 14            # 旧布局里单路 ACCEL 的偏移
LEGACY_NAV_ACCEL_INTO = "ACCEL_LON"  # 旧单路加速度映射到当前哪一路


def _envelope_ok(frame: bytes) -> bool:
    """信封完整性：包尾必须是包头逆序，且 CRC 通过（CRC 配成 "none" 时只看包尾）。"""
    if frame[-2:] != TAIL:
        return False
    want = frame_crc(frame[:-4])
    if want is None:
        return True
    return want == struct.unpack("<H", frame[-4:-2])[0]


def _legacy_nav_frame(buf: bytearray):
    """当前布局校验失败时，按历史包长再试一次；**CRC + 包尾双通过**才返回该帧。"""
    for total in LEGACY_NAV_TOTALS:
        if len(buf) < total:
            continue
        lf = bytes(buf[:total])
        if _envelope_ok(lf):
            return lf
    return None


def _decode_legacy_nav(frame: bytes, spec: dict) -> ParsedFrame:
    """按历史布局解一帧旧 NAV，并**映射到当前配置的字段名**。

    · 同名字段（ROLL/PITCH/YAW）在历史偏移上按**当前配置**的 fmt/scale/zero 解码
      （这三个字段的历史偏移与现在一致：8/10/12）；
    · 历史单路 ACCEL（偏移 14）→ `LEGACY_NAV_ACCEL_INTO`（默认 ACCEL_LON）；
    · 当前配置里其余字段（如 ACCEL_LAT）补 0，绝不猜值。
    """
    cur = {f[0]: f for f in spec.get("fields", [])}
    values: Dict[str, float] = {}
    for name, off in (("ROLL", 8), ("PITCH", 10), ("YAW", 12)):
        fld = cur.get(name)
        if fld is None:
            continue
        f = _field_fmt(spec, fld[2])
        raw = struct.unpack(f, frame[off:off + struct.calcsize(f)])[0]
        z = float(_CFG.field_zero(name, fld))
        values[name] = (float(raw) - z) * float(fld[3])
    tgt = LEGACY_NAV_ACCEL_INTO if LEGACY_NAV_ACCEL_INTO in cur else (
        "ACCEL" if "ACCEL" in cur else None)
    fld = cur.get(tgt) if tgt else None
    if fld is not None:
        f = _field_fmt(spec, fld[2])
        off = LEGACY_NAV_ACCEL_OFF
        raw = struct.unpack(f, frame[off:off + struct.calcsize(f)])[0]
        z = float(_CFG.field_zero(tgt, fld))
        values[tgt] = (float(raw) - z) * float(fld[3])
    for name in cur:
        values.setdefault(name, 0.0)      # ACCEL_LAT 等当前布局独有字段：补 0
    seq = struct.unpack(spec["seq"][1], frame[spec["seq"][0]: spec["seq"][0] + 1])[0]
    ts = struct.unpack(spec["ts"][1], frame[spec["ts"][0]: spec["ts"][0] + 4])[0]
    return ParsedFrame(type_id=0x01, name=spec["name"], values=values,
                       raw=bytes(frame), seq=seq, ts=ts)


def parse_stream(buf: bytearray) -> List[ParsedFrame]:
    """
    从累积缓冲里尽可能多地解析出完整帧，并就地消费掉已解析/已跳过的字节。
    返回本次解析出的帧列表。

    扫描策略：
      - 用 buf.find(SYNC) 向前找下一个同步头；
      - 同步头之前的字节视为垃圾并丢弃；
      - 同步头处类型非法 / 包尾不是 header 逆序 / CRC 不符 -> 假同步，跳 1 字节继续找；
      - 同步头有效但字节不够一整帧 -> 保留，等更多数据。
    这样可容忍帧间噪声、串口读取截断、假同步；包尾逆序校验多一道完整性保险。
    """
    out: List[ParsedFrame] = []
    while True:
        n = len(buf)
        if n == 0:
            break
        p = buf.find(SYNC)
        if p == -1:
            # 无完整同步头：若末尾恰为 SYNC 首字节，可能跨边界，保留它；否则清空
            if buf and buf[-1] == SYNC[0]:
                del buf[:-1]
            else:
                buf.clear()
            break
        if p > 0:
            del buf[:p]   # 丢弃同步头之前的垃圾
            continue
        # 此时 buf 以 SYNC 开头
        if len(buf) < 3:
            break  # 等类型字节
        type_id = buf[2]
        spec = PROTOCOL.get(type_id)
        if spec is None:
            del buf[:1]   # 假同步，跳 1 字节
            continue
        total = spec["total"]
        if len(buf) < total:
            # ★ 旧 20B NAV 帧落在流末尾时，永远等不到第 22 个字节 —— 这里也兜一次，
            #   否则每份旧录播的最后 1 帧会丢。双校验通过才接受（见下）。
            if type_id == 0x01:
                lf = _legacy_nav_frame(buf)
                if lf is not None:
                    out.append(_decode_legacy_nav(lf, spec))
                    del buf[:len(lf)]
                    continue
            break  # 整帧未到齐，等更多数据
        frame = bytes(buf[:total])
        # --- 信封完整性检查：CRC + 包尾(=包头逆序) ---
        if _envelope_ok(frame):
            out.append(_decode_frame(type_id, spec, frame))
            del buf[:total]
            continue
        # --- 旧 .bin 兼容兜底：仅当 0x01 的**当前布局**校验失败时才按 20B 旧窗口再试一次 ---
        #     新协议帧在上面就通过了，所以实时路径不会多走这一步。
        if type_id == 0x01:
            lf = _legacy_nav_frame(buf)
            if lf is not None:
                out.append(_decode_legacy_nav(lf, spec))
                del buf[:len(lf)]
                continue
        del buf[:1]   # 假同步（包尾或 CRC 不符），跳 1 字节继续找
    return out


# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# 7) 单位表与信号分组  →  已迁移到 telemetry_config.py 第 4 区
# ----------------------------------------------------------------------------
# UNITS / ADC_SIGNALS / NAV_SIGNALS / MOTOR_SIGNALS 都在配置文件里派生，
# 本文件通过 import 拿到，用法不变。改单位请打开 telemetry_config.py。
# ----------------------------------------------------------------------------


# ----------------------------------------------------------------------------
# 8) 组帧（供 simulator.py 使用，保证与解析完全一致）
# ----------------------------------------------------------------------------
# ★ 重要约定（改配置后不用动这里）：
#     显示值 = 原始码 × scale
#   所以组帧时反过来：原始码 = round(显示值 / scale)。
#   scale 只在 telemetry_config.py 第 4 区写一次，编码和解码都从它推导，
#   互为逆运算 —— 你把车速的 scale 从 0.01 改成 1.0，两边会一起变，
#   不会出现"改了配置但模拟器还在按 ×100 编码"这种脱节。
#
#   因此下面这些 build_* 的参数统一是【物理量 / 显示值】，不是原始码：
#     build_nav_frame(roll=12.34) 表示 12.34°；accel 传的也是换算后的值。
#   想从原始码换算，用下面的 to_display() / to_raw()。
# ----------------------------------------------------------------------------

# 每种 struct 格式能表示的整数范围，编码时用来夹住越界值（防止 struct.error）
_FMT_RANGE = {
    "<B": (0, 255), "<b": (-128, 127),
    "<H": (0, 65535), "<h": (-32768, 32767),
    "<I": (0, 2 ** 32 - 1), "<i": (-2 ** 31, 2 ** 31 - 1),
}


def _field_scale(type_id: int, name: str) -> float:
    """取某个字段的 scale。"""
    for f in PROTOCOL[type_id]["fields"]:
        if f[0] == name:
            return float(f[3])
    return 1.0


def to_display(type_id: int, name: str, raw: float) -> float:
    """原始码 → 显示值（= raw × scale）。给模拟器/测试用。"""
    return float(raw) * _field_scale(type_id, name)


def to_raw(type_id: int, name: str, display: float) -> int:
    """显示值 → 原始码（= round(display / scale)）。"""
    s = _field_scale(type_id, name)
    return int(round(float(display) / s)) if s else int(round(display))


def _pack_display(display: float, fmt: str, scale: float, zero: float = 0.0) -> bytes:
    """按 scale 把显示值编码成原始码并打包（越界自动夹住）。

    ★ 与解码互为逆运算：解码是 (raw − zero) × scale，所以这里必须
      raw = display / scale + zero。漏了 +zero 的话，组帧写进去的码会偏一个零点，
      自检的往返会立刻红（这是故意的：宁可自检红，也不能让零点悄悄失效）。
    """
    raw = int(round(float(display) / scale)) if scale else int(round(display))
    raw = int(round(raw + float(zero or 0.0)))
    lo, hi = _FMT_RANGE.get(fmt, (-(2 ** 31), 2 ** 31 - 1))
    raw = max(lo, min(hi, raw))          # 夹到类型能表示的范围，避免 struct.error
    return struct.pack(fmt, raw)


def _build_typed_frame(type_id: int, seq: int, timestamp: int,
                       values: Dict[str, float]) -> bytes:
    """按第 4 区 fields 表组帧（字段增删 / 改偏移后本函数无需修改）。

    ★★ 必须按字段【自己声明的偏移 off】写入，绝不能按 fields 列表顺序往后拼。

    为什么：偏移才是字节布局的唯一真源，列表顺序只决定 CSV / 界面的列顺序。
    以前这里写成顺序 append，用户一把 MOTOR_RPM 挪到 10、TORQUE 挪到 9
    （列表顺序与偏移顺序相反），组帧写进去的位置就和解析读的位置整体错位 ——
    表现为「改一下偏移，自检就满江红」，但报错信息完全指不到根因。
    selftest 里有【组帧字节落点 == 配置偏移】的回归锁盯着，别再改回顺序拼。
    """
    spec = PROTOCOL[type_id]
    total = int(spec["total"])
    crc_at = total - 4                     # CRC(2) + 包尾(2)
    body = bytearray(total)                # 全 0 打底：空洞保持 0，不留随机内容
    body[0:2] = HEADER
    body[2] = type_id & 0xFF
    body[3] = seq & 0xFF
    body[4:8] = struct.pack("<I", timestamp & 0xFFFFFFFF)
    for fld in spec["fields"]:
        name, off, fmt, scale = fld[0], fld[1], fld[2], fld[3]
        f = _field_fmt(spec, fmt)          # 字节序与解码端共用同一个取值路径
        size = struct.calcsize(f)
        if off < 8 or off + size > crc_at:
            raise ValueError(
                "第 4 区 0x%02X 字段 %s 偏移 %d 宽 %d 越出数据区 [8, %d)；"
                "跑 python telemetry_config.py 看体检报告"
                % (type_id, name, off, size, crc_at))
        z = float(_CFG.field_zero(name, fld))
        body[off:off + size] = _pack_display(values.get(name, 0.0), f, scale, z)
    body[crc_at:crc_at + 2] = struct.pack("<H", frame_crc(bytes(body[:crc_at])) or 0)
    body[total - 2:total] = TAIL
    return bytes(body)


def build_adc_frame(seq: int, timestamp: int, adc_raw12: List[int]) -> bytes:
    """组 ADC 帧。adc_raw12 为 10 个 12bit 整数(0~4095)，直接打包。"""
    body = bytearray()
    body += HEADER                                # 包头（派生常量）
    body += bytes([0x00])                          # type = ADC
    body += bytes([seq & 0xFF])                    # seq uint8
    body += struct.pack("<I", timestamp & 0xFFFFFFFF)
    body += pack_12bit(adc_raw12, ADC_COUNT)       # 10 路 12bit -> 15 字节
    body += struct.pack("<H", frame_crc(bytes(body)) or 0)
    body += TAIL                                   # 包尾（=包头逆序，派生常量）
    return bytes(body)


def build_nav_frame(seq: int, timestamp: int, *values) -> bytes:
    """组 NAV 帧（长度、字段、顺序**全部取自配置第 4 区**）。

    ★ 参数全是【显示值】，**按配置里 NAV 字段的顺序**逐个传：
        例：NAV fields = (ROLL, PITCH, YAW, ACCEL_LAT, ACCEL_LON) → 传 5 个值。
      ★ 为什么改成 *values：用户把 NAV 从 4 字段改成 5 字段（ACCEL 拆成 ACCEL_LAT/LON、
        包长 20→22）后，旧签名写死 roll/pitch/yaw/accel 四个参数，多出来的字段会被
        `values.get(name, 0.0)` 静默填 0 —— 组出来的帧"合法但数据是错的"。
        现在字段增删/改名/换顺序都不用改本函数（改 telemetry_config.py 即可），
        参数个数对不上会**直接报错**而不是静默丢数据。
      手上只有原始码？用 to_display(0x01, <字段名>, raw) 换算一下再传。
    """
    names = [f[0] for f in PROTOCOL[0x01].get("fields", [])]
    if len(values) != len(names):
        raise ValueError(
            "NAV 组帧需要 %d 个显示值，按配置顺序 %s；收到 %d 个。"
            "（协议改字段后请同步调用方：字段表在 telemetry_config.py 第 4 区）"
            % (len(names), list(names), len(values)))
    return _build_typed_frame(0x01, seq, timestamp, dict(zip(names, values)))


def build_motor_frame(seq: int, timestamp: int,
                      motor_temp: int, motor_rpm: int, torque: int,
                      throttle: int, voltage: int, current: int,
                      speed: float) -> bytes:
    """组 MOTOR 帧（23B，50Hz）。

    ★ 参数全是【显示值】：前 6 个字段 scale=1.0 所以显示值=原始码；
      speed 的单位由配置的 scale 决定（scale=1.0 时直接传 m/s 的整数值）。
    """
    return _build_typed_frame(0x02, seq, timestamp, {
        "MOTOR_TEMP": motor_temp, "MOTOR_RPM": motor_rpm, "TORQUE": torque,
        "THROTTLE": throttle, "VOLTAGE": voltage, "CURRENT": current,
        "SPEED": speed,
    })


if __name__ == "__main__":
    # 自检：组帧 -> 解析 应一致
    adc_in = [2048, 1000, 500, 300, 200, 100, 80, 60, 40, 20]
    f1 = build_adc_frame(1, 1000, adc_in)
    print("ADC 帧字节数:", len(f1), "(配置声明 %d)" % PROTOCOL[0x00]["total"])
    # ★ 参数全是「显示值」，且**按配置字段顺序**传（字段增删改名都不用改这里）
    nav_names = [f[0] for f in PROTOCOL[0x01]["fields"]]
    f2 = build_nav_frame(2, 1000, *[to_display(0x01, n, 100) for n in nav_names])
    print("NAV 帧字节数:", len(f2), "(配置声明 %d)" % PROTOCOL[0x01]["total"])
    f3 = build_motor_frame(3, 1000, 65, 4200, 88, 95, 485, 120, 10.57)
    print("MOTOR 帧字节数:", len(f3), "(配置声明 %d)" % PROTOCOL[0x02]["total"])
    buf = bytearray(f1 + f2 + f3 + f1)
    frames = parse_stream(buf)
    for fr in frames:
        print(fr.name, fr.values, "seq=", fr.seq, "ts=", fr.ts)
    print("remaining buf:", buf.hex())
