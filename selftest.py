# -*- coding: utf-8 -*-
"""
selftest.py -- 协议自检（无需硬件、无需 GUI）
=====================================================
★ 改完 telemetry_config.py 之后请立刻跑一遍：python selftest.py

覆盖 4 类检查：
  1) 信封    ：包头/包尾字节序、帧长与配置文件声明一致
  2) 往返    ：12bit 打包 → 解包 → 组帧 → 解析，数值必须完全还原
  3) 容错    ：噪声字节 / CRC 损坏 / 未知类型 —— 必须被丢弃且不卡死下水道
  4) CSV     ：表头列数 = 一次快照的字段数，保证导出文件不串列

全绿才能打包；任何一项红了都说明配置表和固件没对齐。

⚠️ 编码说明（本文件开头的 3 行不要删）：
    Windows 中文系统的控制台默认是 GBK，直接 print ✅/❌ 会抛
    UnicodeEncodeError 让整个自检崩掉 —— 打包脚本会把它误判成
    "协议自检未通过"，而实际上一条断言都没跑。
    下面这段代码先把输出切成 UTF-8，再按终端真实能力选择标记：
    能显示 emoji 就显示，不能就自动退回 [OK]/[FAIL] ASCII，
    保证在任何终端下都不会因为"打不出一个勾"而崩。
"""
import sys
import struct
from typing import List

# --- 输出编码自保：必须在任何 print 之前执行 -------------------------------
# 只在终端编码不是 UTF-8 时才切（典型场景：被打包脚本用管道 capture_output
# 捕获，此时 Python 按系统 locale=GBK 编码输出，✅ 直接编码失败 → 整个脚本
# 崩掉、退出码非 0，打包脚本误判为"协议没通过"）。
# 已经是 UTF-8 的终端（Windows 控制台、Git Bash、VS Code）保持原样不动。
def _ensure_utf8():
    for stream in (sys.stdout, sys.stderr):
        try:
            enc = (getattr(stream, "encoding", "") or "").lower()
            if "utf" not in enc:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_ensure_utf8()


def _supports(s):
    """当前终端编码能否表示这个字符。"""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        s.encode(enc)
        return True
    except Exception:
        return False


# 万一上面的切换也没生效，标记自动降级成 ASCII，绝不因为"打不出勾"而崩
MARK_OK = "\u2705" if _supports("\u2705") else "[OK]"
MARK_FAIL = "\u274c" if _supports("\u274c") else "[FAIL]"

import telemetry_config as CFG
from protocol import (
    build_adc_frame, build_nav_frame, build_motor_frame, parse_stream,
    unpack_12bit, pack_12bit, ADC_CHANNELS, SYNC, TAIL, PROTOCOL,
    _build_typed_frame,
)

OK = True


def check(name, cond):
    global OK
    print(("%s" % MARK_OK if cond else "%s" % MARK_FAIL), name)
    if not cond:
        OK = False


print("── 1) 帧信封 ──────────────────────────────────────")
check("SYNC = 55 AA（header 0xAA55 小端）", SYNC == bytes([0x55, 0xAA]))
check("TAIL = AA 55（= header 字节逆序）", TAIL == bytes([0xAA, 0x55]))

# ------------------------------------------------------------------------------
# ★ 测试向量全部按配置推导，不写死任何期望值
# ------------------------------------------------------------------------------
# 约定：显示值 = 原始码 × scale。所以自检先给每个字段挑一个原始码，按配置里
# 的 scale 算出「显示值」喂给组帧，解析回来再跟它比。
# 这样你把 scale 改成 1.0 / 0.01 / 0.00001 甚至加新字段，自检都会自动跟着变，
# 不会因为改了系数就报红（那才是 bug，不是你的错）。
_FMT_RANGE = {"<B": (0, 255), "<b": (-128, 127), "<H": (0, 65535),
              "<h": (-32768, 32767), "<I": (0, 4294967295),
              "<i": (-(2 ** 31), 2 ** 31 - 1)}
# 每个类型挑一个有辨识度的测试原始码（负数类型取负，能顺带验证有符号还原）
_SAMPLE_RAW = {"<B": 65, "<b": -30, "<H": 4200, "<h": -2048,
               "<I": 123456, "<i": -50000}


def _sample_for(fmt: str) -> int:
    lo, hi = _FMT_RANGE.get(fmt, (0, 255))
    return max(lo, min(hi, _SAMPLE_RAW.get(fmt, 1)))


def _vectors(type_id: int):
    """[(信号名, 原始码, 显示值)] —— 全部从配置第 4 区推导。"""
    out = []
    for f in PROTOCOL[type_id].get("fields", []):
        name, fmt, scale = f[0], f[2], float(f[3])
        raw = _sample_for(fmt)
        out.append((name, raw, raw * scale))
    return out


def _as_dict(vec):
    return {n: d for n, _r, d in vec}


adc_in = [2048, 1000, 500, 300, 200, 100, 80, 60, 40, 20]

nav_vec = _vectors(0x01)
mot_vec = _vectors(0x02)
nav_d = _as_dict(nav_vec)
mot_d = _as_dict(mot_vec)

# 走公开 API（顺带验证 build_nav_frame / build_motor_frame 参数语义一致）
# ★ 按**配置的字段顺序**传（不写死字段名）：协议加字段/改名后这里不用改，
#   而且字段增减会被往返断言立刻覆盖到 —— 写死名字曾在 ACCEL 拆成 ACCEL_LAT/LON 时直接崩。
f_adc = build_adc_frame(7, 1000, adc_in)
nav_names = [f[0] for f in PROTOCOL[0x01]["fields"]]
f_nav = build_nav_frame(3, 1000, *[nav_d[n] for n in nav_names])
f_mot = build_motor_frame(4, 1000, mot_d["MOTOR_TEMP"], mot_d["MOTOR_RPM"],
                          mot_d["TORQUE"], mot_d["THROTTLE"], mot_d["VOLTAGE"],
                          mot_d["CURRENT"], mot_d["SPEED"])

check("ADC 帧长 = %d 字节（配置声明 %d）" % (len(f_adc), PROTOCOL[0x00]["total"]),
      len(f_adc) == PROTOCOL[0x00]["total"])
check("NAV 帧长 = %d 字节（配置声明 %d）" % (len(f_nav), PROTOCOL[0x01]["total"]),
      len(f_nav) == PROTOCOL[0x01]["total"])
check("MOTOR 帧长 = %d 字节（配置声明 %d）" % (len(f_mot), PROTOCOL[0x02]["total"]),
      len(f_mot) == PROTOCOL[0x02]["total"])
check("配置里 adc_n 与打包区字节数自洽",
      (CFG.ADC_COUNT * 12 + 7) // 8 == PROTOCOL[0x00]["packed_len"])

print("\n── 2) 组帧 → 解析 往返一致 ─────────────────────────")
packed = pack_12bit(adc_in, CFG.ADC_COUNT)
check("pack_12bit 产出 %d 字节" % PROTOCOL[0x00]["packed_len"],
      len(packed) == PROTOCOL[0x00]["packed_len"])
check("unpack_12bit 还原全部通道", unpack_12bit(packed, CFG.ADC_COUNT) == adc_in)
check("边界值：全 0 往返", unpack_12bit(pack_12bit([0] * CFG.ADC_COUNT, CFG.ADC_COUNT), CFG.ADC_COUNT)
      == [0] * CFG.ADC_COUNT)
check("边界值：全 4095 往返", unpack_12bit(pack_12bit([4095] * CFG.ADC_COUNT, CFG.ADC_COUNT), CFG.ADC_COUNT)
      == [4095] * CFG.ADC_COUNT)

# ---- 12bit 解包顺序：4 种都必须自洽，且覆盖/还原不能留残值 ----------------
# 背景：用户实测「CH2 跟着 CH1 漂、动 CH2 传感器 CH2 却不变」= 组内两路装反，
#   lsb/msb 都只是整串反转，救不了；只有 *_swap 能。这四个值必须一直可用，
#   否则现场排查时又得重新打包一次。
_ord0 = CFG.ADC_12BIT_ORDER
try:
    _vals = [i * 400 % 4096 for i in range(CFG.ADC_COUNT)]
    _bad = []
    for _o in ("lsb", "msb", "lsb_swap", "msb_swap"):
        CFG.ADC_12BIT_ORDER = _o
        if unpack_12bit(pack_12bit(_vals, CFG.ADC_COUNT), CFG.ADC_COUNT) != _vals:
            _bad.append(_o)
    check("4 种解包顺序 pack/unpack 都互逆", not _bad)

    # swap 的语义：组内两路互换，不是整串反转
    CFG.ADC_12BIT_ORDER = "lsb"
    _pk = pack_12bit([1000, 3000] + [0] * (CFG.ADC_COUNT - 2), CFG.ADC_COUNT)
    CFG.ADC_12BIT_ORDER = "lsb_swap"
    _got = unpack_12bit(_pk, CFG.ADC_COUNT)
    check("*_swap 是组内互换（不是整串反转）",
          _got[0] == 3000 and _got[1] == 1000 and _got[2:] == [0] * (CFG.ADC_COUNT - 2))

    # ★ 运行期改配置必须立刻影响解码（protocol 动态读，不是 import 时绑定）
    CFG.ADC_12BIT_ORDER = "msb"
    _msb_got = unpack_12bit(_pk, CFG.ADC_COUNT)
    CFG.ADC_12BIT_ORDER = "lsb"
    _lsb_got = unpack_12bit(_pk, CFG.ADC_COUNT)
    check("改 ADC_12BIT_ORDER 后解码立刻跟着变", _msb_got != _lsb_got)
finally:
    CFG.ADC_12BIT_ORDER = _ord0

# ---- ★ 字节级真值锁（用户 2026-10-06 抓的真实 P0 帧）----------------------
# 之前两次都是靠"看曲线抖不抖 / 通道跟不跟着漂"来判 lsb 还是 msb，判错过两次。
# 这帧是硬证据：数据区 15 字节去掉空格连成 hex digit，每 3 个一组就是 CH1..CH10，
# 用户当场确认 CH1=0x008 / CH2=0xFF9，固件作者的 unpack12 也输出同一串。
_REAL_FRAME = bytes.fromhex("55AA00744ABE0200008FF9000FF758E590583011BEE0034B8CAA55")
_REAL_ADC = [0x008, 0xFF9, 0x000, 0xFF7, 0x58E, 0x590, 0x583, 0x011, 0xBEE, 0x003]
_ord1 = CFG.ADC_12BIT_ORDER
try:
    _p0 = CFG.PROTOCOL[0x00]
    check("真实 ADC 帧长 == P0 total", len(_REAL_FRAME) == _p0["total"])
    check("ADC 数据从第 9 字节起（packed_off=8）", _p0["packed_off"] == 8)
    _body = _REAL_FRAME[_p0["packed_off"]: _p0["packed_off"] + _p0["packed_len"]]
    _digits = _body.hex().upper()
    _manual = [int(_digits[i * 3:(i + 1) * 3], 16) for i in range(CFG.ADC_COUNT)]
    check("真实帧 hex-digit 每 3 位一组的读数 == 用户确认值", _manual == _REAL_ADC)

    CFG.ADC_12BIT_ORDER = "msb"
    check("★ msb 解码 == 手工 hex-digit 读法（字节级终判）",
          unpack_12bit(_body, CFG.ADC_COUNT) == _manual)
    check("★ 用 msb 回组的字节 == 固件原始字节",
          pack_12bit(_REAL_ADC, CFG.ADC_COUNT) == _body)

    # 其余三种必须与真值不同 —— 否则说明选项退化成同一个东西，现场排查就没意义了
    _differ = []
    for _o in ("lsb", "lsb_swap", "msb_swap"):
        CFG.ADC_12BIT_ORDER = _o
        if unpack_12bit(_body, CFG.ADC_COUNT) != _manual:
            _differ.append(_o)
    check("另外 3 种顺序都与真值不同（选项有效）", len(_differ) == 3)
    check("12bit 默认顺序 == msb", CFG._BUILTIN.get("adc12bitOrder") == "msb")
finally:
    CFG.ADC_12BIT_ORDER = _ord1

# 注入噪声 + 三类包混排
buf = bytearray()
buf += f_adc
buf += b"\x00\xff"        # 噪声：滑窗必须跳过它而不是卡死
buf += f_nav
buf += f_mot
buf += f_adc
frames = parse_stream(buf)
check("三类包 + 噪声 → 解析出 4 帧", len(frames) == 4)
check("噪声字节已被消费干净", len(buf) == 0)

if len(frames) == 4:
    a, n, m = frames[0], frames[1], frames[2]
    check("ADC 名称 / seq / ts", a.name == "ADC" and a.seq == 7 and a.ts == 1000)
    # ★ ADC 的期望值必须按 ADC_DECODE_MODE 推导，绝不能写死。
    #   以前这里写死 2048.0 / 20.0（那是 raw 模式下的码值），一旦把模式切成
    #   eng（显示值 = 码/4095×rng，2048 会变成 180.04），这项就会误报失败 ——
    #   协议层一切正常，是自检自己在说谎。
    #   现在改成逐通道按当前模式推导，raw / eng 都能通过，切换模式不再假报错。
    _eng_lsb = [CFG.ADC_FULLSCALE[i] / 4095.0 for i in range(CFG.ADC_COUNT)]

    def _adc_want(i, raw12):
        if CFG.ADC_DECODE_MODE == "eng":
            return raw12 * _eng_lsb[i], _eng_lsb[i] * 0.51 + 1e-9
        return float(raw12), 0.0

    _bad = []
    for _i in range(CFG.ADC_COUNT):
        _want, _tol = _adc_want(_i, adc_in[_i])
        _got = a.values[CFG.ADC_CHANNELS[_i]["short"]]
        if abs(_got - _want) > _tol:
            _bad.append("CH%d 期望 %.4f 实得 %.4f" % (_i + 1, _want, _got))
    check("ADC 全通道取值正确（%s 模式）" % CFG.ADC_DECODE_MODE, not _bad)
    for _b in _bad:
        print("     ✗ " + _b)
    # 逐字段核对往返结果（字段增删后无需改自检，会自动覆盖到）
    def _close(got, want, scale):
        """容差 = 半个 scale（定点量化误差的上限）。"""
        return abs(got - want) <= abs(scale) * 0.51 + 1e-9

    for name, raw, disp in nav_vec:
        sc = CFG.field_scale(PROTOCOL[0x01]["fields"], name)
        check("NAV %-10s 往返：原始码 %7d → %g（scale=%g）" % (name, raw, disp, sc),
              _close(n.values[name], disp, sc))
    for name, raw, disp in mot_vec:
        sc = CFG.field_scale(PROTOCOL[0x02]["fields"], name)
        check("MOTOR %-10s 往返：原始码 %7d → %g（scale=%g）" % (name, raw, disp, sc),
              _close(m.values[name], disp, sc))

    # ---- ★ 偏移回归锁：组帧写字节的位置必须等于配置声明的偏移 --------------
    # 背景（2026-10-10）：把 MOTOR_RPM 挪到偏移 10、TORQUE 挪到 9 之后自检满江红。
    #   根因不在配置（偏移没重叠也没越界，配置体检是绿的），而是
    #   _build_typed_frame 曾按 fields 列表顺序 append、把偏移当摆设 ——
    #   列表顺序和偏移顺序一旦不一致，组帧写进去的位置就和解析读的位置整体错位。
    # ★ 判据：只把某一个字段设成特征值、其余全 0，看哪几个字节跟着变；
    #   变的那段必须正好是 [off, off+size)。
    #   这条不含任何写死的字段名/数值，改偏移、改宽度、加字段都自动跟着变，
    #   而且一红就直接告诉你「哪个字段写到了别的字节上」。
    def _offset_probe(type_id: int) -> List[str]:
        spec = PROTOCOL[type_id]
        crc_at = int(spec["total"]) - 4
        names = [f[0] for f in spec["fields"]]
        zero = _build_typed_frame(type_id, 1, 1000, {n: 0.0 for n in names})
        bad = []
        for name, off, fmt, scale in spec["fields"]:
            size = struct.calcsize(fmt)
            mark = 0x5A if size == 1 else 0x5A3C      # 特征值：每个字节都非 0
            vals = {n: 0.0 for n in names}
            vals[name] = mark * float(scale)          # 传显示值：display = raw × scale
            got = _build_typed_frame(type_id, 1, 1000, vals)
            # 只比对数据区：CRC 字节本来就会跟着变，不能算进来
            diff = [i for i in range(8, crc_at) if got[i] != zero[i]]
            if not diff:
                bad.append("%s 特征值没落到任何字节上" % name)
                continue
            lo, hi = min(diff), max(diff) + 1
            if lo != off or hi != off + size:
                bad.append("%s 声明占 %d~%d，实际写在 %d~%d"
                           % (name, off, off + size - 1, lo, hi - 1))
        return bad

    for _tid in sorted(k for k, v in PROTOCOL.items() if "fields" in v):
        _bad = _offset_probe(_tid)
        check("★ 0x%02X(%s) 组帧字节落点 == 配置偏移（%d 字段）"
              % (_tid, PROTOCOL[_tid]["name"], len(PROTOCOL[_tid]["fields"])), not _bad)
        for _b in _bad:
            print("     ✗ " + _b)

    # ---- MOTOR 多字节字段字节序（转速乱跳到几万时的排查点）----
    # 固件若用大端手工拼 u16，而这里按小端解，显示值 = (R&0xFF)<<8 | (R>>8)：
    # 真值每变 1 → 显示跳 256，表现为「数值几万 + 跳动极快」。
    # ★ 测试值与期望值全部从配置 fields 推导（字段名/宽度/scale 都现读），
    #   改偏移、改 scale、加字段都不会让这里莫名其妙变红。
    _e0 = CFG.MOTOR_ENDIAN
    _mfmt = {f[0]: (f[2], float(f[3])) for f in PROTOCOL[0x02]["fields"]}
    _mraw = {n: _SAMPLE_RAW.get(fmt, 1) for n, (fmt, _s) in _mfmt.items()}
    _mdisp = {n: r * _mfmt[n][1] for n, r in _mraw.items()}
    try:
        for _e in ("<", ">"):
            CFG.MOTOR_ENDIAN = _e
            _p = parse_stream(bytearray(_build_typed_frame(0x02, 1, 1000, _mdisp)))
            _bad = ["%s 期望 %g 实得 %r" % (n, d, _p[0].values.get(n) if _p else None)
                    for n, d in _mdisp.items()
                    if not _p or _p[0].values.get(n) != float(d)]
            check("MOTOR 字节序 %s：全部字段往返正确"
                  % ("小端" if _e == "<" else "大端"), not _bad)
            for _b in _bad:
                print("     ✗ " + _b)
        # 同一串字节在两种字节序下必须解出不同值（否则说明切换没生效）
        CFG.MOTOR_ENDIAN = ">"
        _fb = _build_typed_frame(0x02, 1, 1000, _mdisp)
        CFG.MOTOR_ENDIAN = "<"
        _pl = parse_stream(bytearray(_fb))[0].values
        CFG.MOTOR_ENDIAN = ">"
        _pb = parse_stream(bytearray(_fb))[0].values
        _u16 = [n for n, (fmt, _s) in _mfmt.items() if struct.calcsize(fmt) >= 2]
        _same = [n for n in _u16 if _pl.get(n) == _pb.get(n)]
        check("切字节序后多字节字段立刻变（%s）"
              % "、".join("%s %g↔%g" % (n, _pl[n], _pb[n]) for n in _u16[:2]),
              bool(_u16) and not _same)
        # 「跳到几万」的通用判据：字节序反了值会整个跳到另一个量级，
        # 不写死「>10000」——那个数只在 rpm=3000 时成立，换个值就假红。
        _flat = ["%s %g↔%g" % (n, _pl[n], _pb[n]) for n in _u16
                 if abs(_pl[n] - _pb[n]) < max(1.0, abs(_pb[n]))]
        check("字节序搞反时值跳到另一个量级（复现「转速几万」）", not _flat)
        for _b in _flat:
            print("     ✗ " + _b)
        # 1 字节字段不受字节序影响，且恒等于喂进去的值
        _u8 = [n for n, (fmt, _s) in _mfmt.items() if struct.calcsize(fmt) == 1]
        _b1 = ["%s 期望 %g 实得 %g/%g" % (n, _mdisp[n], _pl.get(n), _pb.get(n))
               for n in _u8
               if _pl.get(n) != _mdisp[n] or _pb.get(n) != _mdisp[n]]
        check("1 字节字段不受字节序影响（%s）" % "、".join(_u8), bool(_u8) and not _b1)
        for _b in _b1:
            print("     ✗ " + _b)
    finally:
        CFG.MOTOR_ENDIAN = _e0

print("\n── 3) 容错：坏帧必须被丢弃 ─────────────────────────")
bad = bytearray(f_adc)
bad[-3] ^= 0xFF            # 破坏 CRC
check("CRC 损坏帧被丢弃", len(parse_stream(bytearray(bad))) == 0)

bad_tail = bytearray(f_adc)
bad_tail[-1] ^= 0xFF       # 破坏包尾
check("包尾不符被丢弃", len(parse_stream(bytearray(bad_tail))) == 0)

junk = bytearray(SYNC + bytes([0x09, 0, 0, 0, 0]) + b"\x00" * 30) + f_adc
check("未知包类型被跳过，后续合法帧照常解析",
      len(parse_stream(junk)) == 1)

truncated = bytearray(f_adc[:20])     # 半帧：应原样保留等后续数据
check("半帧不产出错误帧（等待补齐）", len(parse_stream(truncated)) == 0 and len(truncated) == 20)

print("\n── 4) CSV 导出自洽 ────────────────────────────────")
cols: List[str] = CFG.csv_columns()
row = CFG.build_csv_row("12:00:00.000", [1] * CFG.ADC_COUNT,
                        [0.0] * len(CFG.NAV_SIGNALS),
                        [0.0] * len(CFG.MOTOR_SIGNALS)).rstrip("\n").split(",")
check("CSV 列数（%d）= 表头列数（%d）" % (len(row), len(cols)), len(row) == len(cols))
check("CSV 首列为时间标签", cols[0] == "time")
check("CSV 表头含全部 ADC 通道",
      all(any(c.startswith(m[0]) for c in cols) for m in CFG.csv_channels_meta()))

# ★ 这两条专治「eng 模式下界面/CSV 静默失效」，都是踩过的坑：
#   ① CSV 曾经写成 str(int(v))，悬架 rng=28 时 13.648 落成 "13"，
#      12bit 分辨率（0.0068 mm/码）被抹成 1 mm/格，录的文件没法做赛后分析。
#   ② 界面量程曾经恒为 0~4095，eng 下条形只占 0.68%，数字在跳图形不动。
_mode = CFG.ADC_DECODE_MODE
_code_of = lambda i, n: n if _mode != "eng" else n * CFG.ADC_FULLSCALE[i] / 4095.0
_lost, _rng_bad = [], []
for _i in range(CFG.ADC_COUNT):
    def _cell(v):
        return CFG.build_csv_row(
            "t", [0.0] * _i + [v] + [0.0] * (CFG.ADC_COUNT - 1 - _i)
        ).rstrip("\n").split(",")[1 + _i]
    _t1, _t2 = _cell(_code_of(_i, 1)), _cell(_code_of(_i, 2))
    if _t1 == _t2:
        _lost.append("CH%d（%s rng=%g）1码=%s 2码=%s 落盘后分不开"
                     % (_i + 1, CFG.ADC_CHANNELS[_i]["name"],
                        CFG.ADC_FULLSCALE[_i], _t1, _t2))
    _d = CFG.ui_adc_config()[_i]
    # ★ 期望量程按【物理公式】手算，不能写死 0~rng：
    #   零点在中间的通道（第 3 区填了 zero）值域是 -z*step ~ (4095-z)*step，
    #   下界是负的。写成 0~rng 会在用户一填 zero 就误报（同族坑：自检写死）。
    _z = CFG.adc_zero_code(_i)
    if _mode == "eng":
        _step = float(CFG.ADC_CHANNELS[_i]["rng"]) / 4095.0
        _wlo, _whi = -_z * _step, (4095.0 - _z) * _step
    else:
        _wlo = float(CFG.ADC_CHANNELS[_i].get("min", 0)) - _z
        _whi = float(CFG.ADC_CHANNELS[_i].get("max", 4095)) - _z
    if abs(float(_d["max"]) - _whi) > 1e-9 or abs(float(_d["min"]) - _wlo) > 1e-9:
        _rng_bad.append("CH%d 期望 %g~%g 实得 %g~%g"
                        % (_i + 1, _wlo, _whi, _d["min"], _d["max"]))
# ★ 位数只由配置决定（第 3 区 ADC_DECIMALS / 第 5 区 DEFAULT_DECIMALS），
#   不按量程、也不按 scale 反推 —— 反推会让"改一次量程，CSV 列宽就变"。
_want_dec = CFG.adc_decimals(0)
_bad_dec = ["CH%d 期望 %d 位实得 %r" % (i + 1, _want_dec, CFG.adc_csv_fmt(i))
            for i in range(CFG.ADC_COUNT)
            if CFG.adc_csv_fmt(i) != "%%.%df" % _want_dec]
check("ADC CSV 小数位 = 配置位数（%d 位）" % _want_dec, not _bad_dec)
_bad_ui = ["CH%d 界面 dec=%r" % (i + 1, CFG.ui_adc_config()[i].get("dec"))
           for i in range(CFG.ADC_COUNT)
           if CFG.ui_adc_config()[i].get("dec") != _want_dec]
check("界面 ADC 小数位同口径（%d 位）" % _want_dec, not _bad_ui)

# 「相邻码值能不能分开」取决于位数够不够：位数不足是**用户有意为之**
# （就是要好读），只提示、不判失败 —— 以前这里刷一屏 ✗ 看着像自检没过。
if CFG.ADC_COUNT:
    _step0 = float(CFG.ADC_FULLSCALE[0]) / 4095.0      # 1 个码值 = 多少工程量
    _res = 10.0 ** (-_want_dec)                        # 当前位数 = 多少工程量
    _coarse = _res / _step0 if _step0 > 0 else 0
    if _coarse > 1.5:
        print("  ⚠ %d 位小数 = %.4g %s/格，而 1 个码值只有 %.4g %s —— 显示精度比"
              " 12bit 原始分辨率粗约 %.0f 倍（要赛后精细分析就把 ADC_DECIMALS 调大；"
              "这不是错误，只是提醒：%d 个通道落盘后被合并成同一格）"
              % (_want_dec, _res, CFG.ADC_CHANNELS[0]["unit"] or "",
                 _step0, CFG.ADC_CHANNELS[0]["unit"] or "", _coarse, len(_lost)))
check("界面 ADC 量程跟随解码模式 + 零点（%s）" % _mode, not _rng_bad)
for _b in _rng_bad:
    print("     ✗ " + _b)

# ---------------------------------------------------------------------------
# ★★ 回归锁：零点 / 偏置（2026-10-10 用户要求「有些传感器零点在中间」）
# ---------------------------------------------------------------------------
# 零点在中间的传感器（悬架中位、转向中位、双向力）必须满足三件事：
#   ① raw == zero 时显示 0（零点真的是零点）
#   ② 显示范围跨 0（min<0<max），否则负值全被压在底边，看着像"卡住了"
#   ③ 组帧与解析互为逆运算：写进去的码是 v/scale + zero
# 这里不写死具体零点值 —— 期望全部从第 3 区 / 第 5 区当前配置推导。
_z_bad = []
_f0 = build_adc_frame(1, 1000,
                      [int(round(CFG.adc_zero_code(k)))
                       for k in range(CFG.ADC_COUNT)])
_p0v = parse_stream(bytearray(_f0))[0].values
for _i in range(CFG.ADC_COUNT):
    _z = CFG.adc_zero_code(_i)
    _v0 = _p0v[CFG.ADC_CHANNELS[_i]["short"]]
    if abs(_v0) > 1e-6:
        _z_bad.append("CH%d zero=%g 时显示 %g（应为 0）" % (_i + 1, _z, _v0))
    if _z > 0:
        _lo, _hi = CFG.adc_display_range(_i)
        if not (_lo < 0 < _hi):
            _z_bad.append("CH%d zero=%g 但量程 %g~%g 没有跨 0" % (_i + 1, _z, _lo, _hi))
check("ADC 零点：原始码 == zero 时显示 0（%d 路同帧）" % CFG.ADC_COUNT, not _z_bad)
for _b in _z_bad:
    print("     ✗ " + _b)

# 字段零点（NAV / MOTOR）往返：临时把 TORQUE 的零点设成一个非 0 值，
# 验证「组帧写 v/scale+zero → 解析 (raw-zero)×scale = v」，跑完立刻还原。
_z0 = CFG.FIELD_ZERO.get("TORQUE")
try:
    CFG.FIELD_ZERO["TORQUE"] = 128.0
    # 显示值取 100：100 + zero(128) = 228 < 255，不会撞上 u8 上溢被夹住
    _fm = build_motor_frame(1, 1000, 60, 3000, 100, 50, 500, 100, 20)
    _spec2 = CFG.PROTOCOL[0x02]
    _tq = [f for f in _spec2["fields"] if f[0] == "TORQUE"][0]
    _raww = struct.unpack("<B", _fm[_tq[1]:_tq[1] + struct.calcsize(_tq[2])])[0]
    _got = parse_stream(bytearray(_fm))[0].values["TORQUE"]
    check("字段零点：显示 100 → 原始码 = 100/scale + zero = %d" % _raww,
          _raww == int(round(100 / float(_tq[3])) + 128))
    check("字段零点往返：解析回显示值（%.1f）" % _got, abs(_got - 100.0) < 1e-6)
finally:
    if _z0 is None:
        CFG.FIELD_ZERO.pop("TORQUE", None)
    else:
        CFG.FIELD_ZERO["TORQUE"] = _z0

# ---------------------------------------------------------------------------
# ★★ 回归锁：改 scale 不改小数位（2026-10-09 用户亲历：改 SPEED 的 scale 后自检报红）
# ---------------------------------------------------------------------------
# 以前 CSV 位数 = ceil(-log10(scale))、界面又各写各的 toFixed(1)，
# 于是：改一次系数 -> 位数跟着跳 -> 依赖位数的期望值全飘 -> 一改 scale 就报红。
# 现在位数只认配置（FIELD_DECIMALS / DEFAULT_DECIMALS），这里把 scale 变异四档，
# 断言 CSV 与界面的位数纹丝不动。
def _dec_of(cell: str) -> int:
    return len(cell.split(".")[1]) if "." in cell else 0


def _speed_cell() -> str:
    return CFG.build_csv_row(
        "t", [0.0] * CFG.ADC_COUNT,
        [0.0] * len(CFG.NAV_SIGNALS),
        [0.0] * len(CFG.MOTOR_SIGNALS)).rstrip("\n").split(",")[-1]


_spec2 = PROTOCOL[0x02]
_f0 = [list(f) for f in _spec2["fields"]]
_idx = [i for i, f in enumerate(_f0) if f[0] == "SPEED"][0]
_want_spd = CFG.field_decimals("SPEED", _spec2["fields"][_idx])
_seen, _bad_vary = [], []
try:
    for _sc in (1.0, 0.01, 0.001, 0.0001):
        _newf = [list(f) for f in _f0]
        _newf[_idx][3] = _sc
        _spec2["fields"] = [tuple(f) for f in _newf]
        CFG.refresh()
        _d_csv = _dec_of(_speed_cell())
        _d_ui = [f for f in CFG.ui_config()["motor"] if f["name"] == "SPEED"][0]["dec"]
        _seen.append((_sc, _d_csv, _d_ui))
        if _d_csv != _want_spd or _d_ui != _want_spd:
            _bad_vary.append("scale=%g -> CSV %d 位 / 界面 %d 位（应为 %d 位）"
                             % (_sc, _d_csv, _d_ui, _want_spd))
finally:
    _spec2["fields"] = [tuple(f) for f in _f0]
    CFG.refresh()
check("★ 改 SPEED 的 scale（1 / 0.01 / 0.001 / 0.0001）不改变小数位（恒 %d 位）"
      % _want_spd, not _bad_vary)
for _b in _bad_vary:
    print("     ✗ " + _b)

# 反向锁：位数确实能改 —— 而且只能靠配置改（改 FIELD_DECIMALS / 字段第 5 项 dec）
_old_fd = CFG.FIELD_DECIMALS.get("SPEED")
try:
    CFG.FIELD_DECIMALS["SPEED"] = _want_spd + 2
    CFG.refresh()
    _d3 = _dec_of(_speed_cell())
    _ui3 = [f for f in CFG.ui_config()["motor"] if f["name"] == "SPEED"][0]["dec"]
    check("改第 5 区 FIELD_DECIMALS.SPEED -> CSV/界面位数立刻变（%d -> %d）"
          % (_want_spd, _d3), _d3 == _want_spd + 2 and _ui3 == _want_spd + 2)
finally:
    if _old_fd is None:
        CFG.FIELD_DECIMALS.pop("SPEED", None)
    else:
        CFG.FIELD_DECIMALS["SPEED"] = _old_fd
    CFG.refresh()

print("\n── 5) 可扩展性：改配置后不用改代码 ─────────────────")
# 这一节模拟"你在 telemetry_config.py 里加字段 / 加包类型"，验证解析层和
# CSV 派生都能自动跟上 —— 也就是所谓「自定义协议」到底自不自由。
# 测完立刻还原，不影响后续检查。
from protocol import _build_typed_frame

_base_cols = len(CFG.csv_columns())
_saved_motor = list(PROTOCOL[0x02]["fields"])
_saved_total = PROTOCOL[0x02]["total"]
try:
    # ① MOTOR 包末尾加一个字段（total 同步 +1）
    PROTOCOL[0x02]["fields"] = _saved_motor + [("CTRL_TEMP", _saved_total - 4,
                                                "<B", 1.0)]
    PROTOCOL[0x02]["total"] = _saved_total + 1
    CFG.refresh()                       # 正常改文件重启即可，这里是运行时改才要调

    mv2 = _as_dict(_vectors(0x02))
    mv2["CTRL_TEMP"] = 42.0
    got = parse_stream(bytearray(_build_typed_frame(0x02, 1, 0, mv2)))[0]
    check("MOTOR 新增字段 CTRL_TEMP 被解出 = 42", got.values.get("CTRL_TEMP") == 42.0)
    check("新增字段后 CSV 自动多一列（%d → %d）"
          % (_base_cols, len(CFG.csv_columns())),
          len(CFG.csv_columns()) == _base_cols + 1)
    row = CFG.build_csv_row("t", [0] * CFG.ADC_COUNT, [0.0] * 4,
                            [mv2[n] for n, _r, _d in _vectors(0x02)] + [42.0])
    # ★ 期望值按「配置里的位数」推导：新字段没配 dec，就走 DEFAULT_DECIMALS，
    #   写成死 "42" 会在 DEFAULT_DECIMALS 一改就误报（同族坑：别写死期望值）。
    _exp = "42" if CFG.field_decimals("CTRL_TEMP") == 0 \
        else "%.*f" % (CFG.field_decimals("CTRL_TEMP"), 42.0)
    check("新字段的值进了 CSV 行（末列=%s）" % row.strip().split(",")[-1],
          row.strip().split(",")[-1] == _exp)

    # ② 新增一个 0x03 BMS 包类型
    PROTOCOL[0x03] = {
        "name": "BMS", "total": 18, "seq": (3, "<B"), "ts": (4, "<I"),
        "fields": [("PACK_V", 8, "<H", 0.1), ("PACK_A", 10, "<h", 0.1),
                   ("SOC", 12, "<B", 1.0), ("FAULT", 13, "<B", 1.0)],
    }
    CFG.refresh()
    check("新增 0x03 包通过配置体检", not CFG.validate())
    fb = _build_typed_frame(0x03, 9, 555,
                            {"PACK_V": 480.5, "PACK_A": -12.3, "SOC": 77, "FAULT": 3})
    fb_out = parse_stream(bytearray(b"\x00\xff" + fb))   # 前面塞噪声
    check("新增 0x03 包能被解析",
          len(fb_out) == 1 and fb_out[0].name == "BMS"
          and abs(fb_out[0].values["PACK_V"] - 480.5) < 0.05
          and fb_out[0].values["SOC"] == 77.0)
    check("新增包类型的字段也进了 CSV（%d → %d 列）"
          % (_base_cols + 1, len(CFG.csv_columns())),
          len(CFG.csv_columns()) == _base_cols + 1 + 4)
finally:
    PROTOCOL[0x02]["fields"] = _saved_motor
    PROTOCOL[0x02]["total"] = _saved_total
    PROTOCOL.pop(0x03, None)
    CFG.refresh()

print("\n结果:", ("全部通过 " + MARK_OK) if OK else ("存在失败 " + MARK_FAIL))
raise SystemExit(0 if OK else 1)
