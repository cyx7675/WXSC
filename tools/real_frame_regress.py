# -*- coding: utf-8 -*-
"""
real_frame_regress.py —— ★「用户给过的真实报文」回归锁 ★
==============================================================================
背景：协议是靠抓到的真帧反推出来的（12bit 顺序、字节序、偏移、scale）。
      这些结论一旦被后来的改动悄悄推翻，界面会「看着正常但全是错值」，
      而且靠合成帧自检根本抓不到 —— 合成帧是自己组自己解，永远自洽。

所以把**用户实际抓到的报文**当物理事实固化在这里，每次打包前重解一遍：

  ┌── MOTOR 真帧（2026-10-10 用户提供，CRC PASS）
  │   55 AA 02 2F 48 95 01 00 00 00 00 00 00 B6 01 00 00 00 00 3D E4 AA 55
  │   事实：type=2；13-14 字节 = B6 01
  │        小端解 = 0x01B6 = 438 ← 用户确认「电池原始码 438」，量级合理
  │        大端解 = 0xB601 = 46593 ← 46593 V 的电池包不存在，据此定死小端
  │
  └── ADC 真帧（2026-10 用户提供；⚠ 手抄帧 CRC 算不过，只校验数据区）
      55 AA 00 74 4A BE 02 00 00 8F F9 00 0F F7 58 E5 90 58 30 11 BE E0 03 …
      数据区 15 字节去空格后按「每 3 个字符一路」手工分组：
          008 FF9 000 FF7 58E 590 583 011 BEE 003
      → 这就是 CH1..CH10，据此定死 ADC_12BIT_ORDER = "msb"

★ 期望值全部**从真帧本身手工推出**（写在下面的 REAL_ 常量里，附推导注释），
  不是从上位机当前配置反推 —— 那样等于自己验自己，改错了也是绿的。
  判据：当前配置解出来的结果，必须等于真帧的手工读数；不等就 FAIL，
  并把「当前配置是什么 / 期望是什么」一起打出来，直接指向该改哪一行。

用法：
    cd F:\\上位机\\v2
    <venv>\\python.exe tools\\real_frame_regress.py
    退出码 0 = 历史真帧全部仍能被正确解读；非 0 = 配置漂移，真帧已被解错
★ 不写磁盘、不改你的配置文件。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import telemetry_config as CFG          # noqa: E402
import protocol as P                    # noqa: E402

MARK_OK = "[OK]"
MARK_FAIL = "[FAIL]"

_fails = []


def _ensure_utf8():
    try:
        if "utf" not in (sys.stdout.encoding or "").lower():
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def check(desc, ok, detail=""):
    print("  %s %s" % (MARK_OK if ok else MARK_FAIL, desc))
    if detail:
        for ln in str(detail).splitlines():
            print("       " + ln)
    if not ok:
        _fails.append(desc)
    return ok


def hex2b(s):
    return bytes.fromhex(s.replace(" ", ""))


# ============================ 真帧 · 物理事实 ============================
# MOTOR：用户 2026-10-10 提供，全帧 CRC 校验通过（可放心当完整帧用）
REAL_MOTOR_HEX = ("55 AA 02 2F 48 95 01 00 00 00 00 00 00 "
                  "B6 01 00 00 00 00 3D E4 AA 55")

# MOTOR 真帧里每个字段的「原始码」，直接按字节人工读出。
#   偏移 8  = 00            -> TEMP     0
#   偏移 9  = 00            -> TORQUE   0
#   偏移 10-11 = 00 00      -> RPM      0
#   偏移 12 = 00            -> THROTTLE 0
#   偏移 13-14 = B6 01      -> VOLTAGE  0x01B6 = 438   ★ 关键证据
#   偏移 15-16 = 00 00      -> CURRENT  0
#   偏移 17-18 = 00 00      -> SPEED    0
REAL_MOTOR_RAW = {
    "MOTOR_TEMP": 0, "TORQUE": 0, "MOTOR_RPM": 0, "THROTTLE": 0,
    "VOLTAGE": 438, "CURRENT": 0, "SPEED": 0,
}

# ADC：用户手抄帧，CRC 字节对不上（抄写误差），所以只取数据区 15 字节。
REAL_ADC_DATA_HEX = "008FF9000FF758E590583011BEE003"
# 手工分组（每 3 个 hex 字符一路）—— 用户亲口确认悬架1=008、悬架2=FF9
REAL_ADC_DIGITS = ["008", "FF9", "000", "FF7", "58E",
                   "590", "583", "011", "BEE", "003"]

# ── NAV 历史真帧（旧布局：20 字节 / 单 ACCEL 在偏移 14）★ 2026-10-14 新增 ──
# 背景：用户 2026-10-14 把 NAV 从 20B（四字段）改成 22B（五字段，ACCEL 拆成
#       ACCEL_LAT@14 / ACCEL_LON@16）。telemetry/ 里 6 份旧 .bin 全是 20B 帧。
# 为什么必须钉在这里：换成新布局后，旧帧会被解码器**明确拒绝**（实测 6 份旧录播里
#       NAV 帧解析数从 4222~31892 全部变成 0）——这是协议变更的必然结果，不是 bug。
#       如果哪天有人"顺手放宽校验"让它能解出来，那才是真出事（会解出错位数据）。
# 所以这一节的做法是：把**历史样本 + 当时的布局**一起固化，
#   ① 在【历史布局】下断言这帧能被完整解析、读数与手工读数一致；
#   ② 在【当前布局】下断言它不会产生**错值**（要么明确拒绝，要么真做了兼容后读数仍正确）。
REAL_NAV_HEX = "55 AA 01 FD 1A CB 03 00 00 00 00 00 00 00 AD FD 75 43 AA 55"
# 人工读数（按旧布局：偏移 8/10/12 三个 int16 angle，偏移 14 一个 int16 ACCEL）
#   偏移 8  = 00 00        -> ROLL   0
#   偏移 10 = 00 00        -> PITCH  0
#   偏移 12 = 00 00        -> YAW    0
#   偏移 14 = AD FD (小端) -> ACCEL  -595
#   ★ 历史布局**自身**的定标是 0.001（-595 × 0.001 = -0.595），用于 ② 的历史布局校验；
#     而 ③ 走现行兼容垫片时，同一路物理量要按**当前配置的 ACCEL_LON scale** 重算
#     （用户改定标 = 改标定，历史原始码不变、显示值跟着新定标走），勿写死 0.001。
REAL_NAV_RAW = {"ROLL": 0, "PITCH": 0, "YAW": 0, "ACCEL": -595}
HIST_NAV_FIELDS = [("ROLL", 8, "<h", 0.01), ("PITCH", 10, "<h", 0.01),
                   ("YAW", 12, "<h", 0.01), ("ACCEL", 14, "<h", 0.001)]
HIST_NAV_TOTAL = 20


# ============================== 检查主体 ==============================
def check_motor():
    print("\n① MOTOR 真帧（%d 字节，CRC 已在帧内）" % len(hex2b(REAL_MOTOR_HEX)))
    raw = hex2b(REAL_MOTOR_HEX)
    spec = CFG.PROTOCOL[0x02]

    check("帧长 == 配置 total（%d）" % spec["total"], len(raw) == spec["total"],
          "实际 %d 字节" % len(raw))

    frames = P.parse_stream(bytearray(raw))
    if not check("整帧能被解析（包头/包尾/CRC 全过）", bool(frames)):
        return
    fr = frames[0]

    # 关键证据：电压字节必须落在配置声明的偏移上，且小端读出 438
    vspec = [f for f in spec["fields"] if f[0] == "VOLTAGE"]
    if not vspec:
        check("配置里有 VOLTAGE 字段", False)
        return
    _n, voff, _fmt, vscale = (list(vspec[0]) + [None] * 4)[:4]
    vbytes = raw[voff:voff + 2]
    check("VOLTAGE 字节落在偏移 %d~%d = %s（真帧里就是 B6 01）"
          % (voff, voff + 1, vbytes.hex(" ").upper()),
          vbytes == b"\xb6\x01", "实际读到 %s" % vbytes.hex(" ").upper())

    import struct
    le = struct.unpack("<H", vbytes)[0]
    be = struct.unpack(">H", vbytes)[0]
    check("小端解 = 438（用户确认的电池原始码）", le == 438, "小端=%d 大端=%d" % (le, be))
    print("       → 当前 MOTOR_ENDIAN=%r，显示值 = %.1f %s"
          % (CFG.MOTOR_ENDIAN, fr.values.get("VOLTAGE", 0),
             CFG.UNITS.get("VOLTAGE", "")))
    if abs(float(vscale) - 1.0) > 1e-12:
        check("VOLTAGE scale == 1.0（1 码 = 1 V，用户已确认 438 码 = 438 V）", False,
              "当前 scale = %r，真帧 438 码会显示成 %.4g；"
              "若你确实改了量纲请更新本文件的 REAL_MOTOR_RAW 说明" % (vscale, 438 * float(vscale)))

    # 其余字段：原始码必须与人工读数一致（scale 反推，与系数解耦）
    bad = []
    for name, want in REAL_MOTOR_RAW.items():
        f = [x for x in spec["fields"] if x[0] == name]
        if not f:
            bad.append("%s 在配置里不存在" % name)
            continue
        sc = float(f[0][3]) or 1.0
        got = fr.values.get(name, 0.0) / sc
        if abs(got - want) > 1e-6:
            bad.append("%s 原始码期望 %d 实得 %.6g（×scale=%.6g 后=%s）"
                       % (name, want, got, sc, fr.values.get(name)))
    check("全部字段原始码 == 真帧人工读数（%s）"
          % ", ".join("%s=%d" % (k, v) for k, v in REAL_MOTOR_RAW.items()),
          not bad)
    for b in bad:
        print("       ✗ " + b)


def check_adc():
    print("\n② ADC 真帧数据区（手抄帧，CRC 跳过，只验解包顺序）")
    data = hex2b(REAL_ADC_DATA_HEX)
    check("数据区 15 字节", len(data) == 15, "实际 %d" % len(data))

    # 手工分组：去空格后每 3 个 hex 字符一路（用户亲口确认悬架1=008）
    h = REAL_ADC_DATA_HEX.replace(" ", "")
    manual = [int(h[i * 3:i * 3 + 3], 16) for i in range(len(h) // 3)]
    check("手工分组 == %s" % REAL_ADC_DIGITS,
          ["%03X" % m for m in manual] == REAL_ADC_DIGITS,
          "实际 %s" % ["%03X" % m for m in manual])

    got = list(P.unpack_12bit(bytes(data), len(manual)))
    ok = (len(got) >= len(manual) and got[:len(manual)] == manual)
    check("当前 ADC_12BIT_ORDER=%r 解出来 == 手工分组" % CFG.ADC_12BIT_ORDER, ok)
    if not ok:
        print("       期望 %s" % ["%03X" % m for m in manual])
        print("       实得 %s" % ["%03X" % g for g in got[:len(manual)]])
        print("       → 真帧已证明顺序是 msb；改这里请改 telemetry_user.json 的 "
              "adc12bitOrder，或第 3 区 ADC_12BIT_ORDER")


def check_power_sanity():
    """物理量级自洽校核（不写死：全部从当前配置推导）。"""
    print("\n③ 功率自洽校核（60 kW 硬约束 × 当前量程）")
    vspec = [f for f in CFG.PROTOCOL[0x02]["fields"] if f[0] == "VOLTAGE"]
    cspec = [f for f in CFG.PROTOCOL[0x02]["fields"] if f[0] == "CURRENT"]
    if not vspec or not cspec:
        print("  %s 配置里缺 VOLTAGE / CURRENT，跳过" % MARK_OK)
        return
    volt = REAL_MOTOR_RAW["VOLTAGE"] * float(vspec[0][3])
    crng = CFG.GAUGE_RANGES.get("CURRENT", 200)
    cmax = crng[1] if isinstance(crng, (list, tuple)) else float(crng)
    vrng = CFG.GAUGE_RANGES.get("VOLTAGE", 600)
    vmax = vrng[1] if isinstance(vrng, (list, tuple)) else float(vrng)
    amps = 60000.0 / volt if volt > 1e-9 else float("inf")
    print("       电池 %.1f V  →  60 kW 需要 %.0f A" % (volt, amps))
    check("60 kW 对应电流 %.0f A 落在 CURRENT 量程 %.0f A 内" % (amps, cmax),
          amps <= cmax,
          "若超出说明 VOLTAGE 的 scale 或量程不对（438 码应解读成 %s）"
          % ("438 V" if abs(float(vspec[0][3]) - 1.0) < 1e-12 else "其它量纲"))
    check("电池 %.1f V 落在 VOLTAGE 量程 %.0f V 内" % (volt, vmax), volt <= vmax)


def check_nav_historical():
    """NAV 历史真帧：把 20B 旧布局与样本一起固化（协议 2026-10-14 已改成 22B）。"""
    print("\n④ NAV 历史真帧（旧布局 %dB；当前配置是 %dB / %s）"
          % (HIST_NAV_TOTAL, CFG.PROTOCOL[0x01]["total"],
             [f[0] for f in CFG.PROTOCOL[0x01]["fields"]]))
    raw = hex2b(REAL_NAV_HEX)
    check("历史样本帧长 == 旧布局 total（%d）" % HIST_NAV_TOTAL,
          len(raw) == HIST_NAV_TOTAL, "实际 %d 字节" % len(raw))

    # ① 手工读数（不依赖任何配置）—— 这是"物理事实"
    manual = {}
    for name, off, _fmt, _sc in HIST_NAV_FIELDS:
        manual[name] = int.from_bytes(raw[off:off + 2], "little", signed=True)
    check("按旧布局手工读出的原始码 == %s" % REAL_NAV_RAW, manual == REAL_NAV_RAW,
          "实得 %s" % manual)

    # ② 历史布局下必须能完整解析，且读数 == 手工读数 × scale
    old_spec = CFG.PROTOCOL[0x01]
    merged = dict(old_spec)
    merged["fields"] = HIST_NAV_FIELDS
    merged["total"] = HIST_NAV_TOTAL
    try:
        CFG.PROTOCOL[0x01] = merged
        P.PROTOCOL[0x01] = merged          # 同一个 dict 对象，写一处即可；防御性再写一次
        frs = P.parse_stream(bytearray(raw))
        ok = check("在【历史布局】下这帧能被完整解析（包头 / CRC / 包尾全过）", len(frs) == 1,
                   "实得 %d 帧" % len(frs))
        if ok:
            got = frs[0].values
            want = {n: REAL_NAV_RAW[n] * sc for n, _o, _f, sc in HIST_NAV_FIELDS}
            bad = {k: (got.get(k), v) for k, v in want.items()
                   if abs(float(got.get(k, 0.0)) - v) > 1e-9}
            check("历史布局解出的读数 == 手工读数 × scale（%s）" % want, not bad,
                  "不一致：%s" % bad)
    finally:
        CFG.PROTOCOL[0x01] = old_spec
        P.PROTOCOL[0x01] = old_spec

    # ③ 当前布局下：允许"明确拒绝"或"真做了兼容且读数正确"，**绝不允许错位解出错值**
    #    ★ 2026-10-15：protocol.parse_stream 已加 20B 旧窗口兜底（为救回 6 份旧录播的姿态数据），
    #      所以这里现在应走**兼容**分支 —— 判据随之明确成：
    #      旧单路 ACCEL → ACCEL_LON、ACCEL_LAT 记 0、ROLL/PITCH/YAW 读数不变。
    #      断言强度不减（逐字段精确比对，错位解一定被抓）。
    #    ★ 2026-10-16 修：旧单路 ACCEL 映射到 ACCEL_LON 后，**用的是当前配置的 scale**
    #      （兼容垫片的语义就是"同一路物理量按现行定标重算"），所以期望值必须由
    #      当前配置的 ACCEL_LON scale 推导，不能沿用历史布局里的 0.001。
    #      以前这里用 hist（历史 0.001）算，用户把定标改成 0.0098 后就成了**假红**
    #      （界面解出 -5.831 才是对的）。
    cur_total = CFG.PROTOCOL[0x01]["total"]
    cur_names = [f[0] for f in CFG.PROTOCOL[0x01]["fields"]]
    frs_new = P.parse_stream(bytearray(raw))
    if not frs_new:
        check("当前布局（%dB / %s）**明确拒绝**这帧、不产生错值 —— "
              "旧录播的 NAV 不兼容，已如实记录（不是靠放宽校验装绿）"
              % (cur_total, list(cur_names)), True)
    else:
        into = getattr(P, "LEGACY_NAV_ACCEL_INTO", "ACCEL_LON")
        # 各历史字段在当前布局下应得的显示值：历史布局里出现过的字段（ROLL/PITCH/YAW）
        # 用历史 scale；**旧单路 ACCEL 映射到 into 时用当前配置里该字段的 scale**。
        cur_scale = {f[0]: float(f[3]) for f in CFG.PROTOCOL[0x01]["fields"]}
        hist = {n: REAL_NAV_RAW[n] * sc for n, _o, _f, sc in HIST_NAV_FIELDS if n != "ACCEL"}
        hist[into] = REAL_NAV_RAW["ACCEL"] * cur_scale.get(into, 0.001)
        want = dict(hist)
        for k in cur_names:
            want.setdefault(k, 0.0)          # ACCEL_LAT 等当前独有字段必须为 0
        got = frs_new[0].values
        bad = {k: (got.get(k), v) for k, v in want.items()
               if abs(float(got.get(k, 0.0)) - v) > 1e-9}
        check("当前布局下走**兼容垫片**解出，读数 == 历史读数映射（ACCEL→%s@现行scale=%g、其余补 0）：%s"
              % (into, cur_scale.get(into, 1.0), want), not bad,
              "不一致：%s（实得 %s）" % (bad, got))


def main():
    _ensure_utf8()
    print("=" * 68)
    print("  真实报文回归锁 —— 用你抓到的真帧反验当前配置")
    print("=" * 68)
    CFG.refresh()
    check_motor()
    check_adc()
    check_power_sanity()
    check_nav_historical()
    print("\n" + "=" * 68)
    if _fails:
        print("  ❌ %d 项不通过（真帧已被当前配置解错）：" % len(_fails))
        for f in _fails:
            print("     · " + f)
        print("=" * 68)
        return 1
    print("  ✅ 历史真帧全部仍能被正确解读（字节序 / 12bit 顺序 / 偏移 / scale 未漂移）")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
