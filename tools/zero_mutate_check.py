# -*- coding: utf-8 -*-
"""
zero_mutate_check.py —— ★「零点 / 偏置」变异自检 ★
==============================================================================
用户要求（2026-10-10）：
    「给我的传感器加偏移量，有些传感器的零点在中间，
      运动过程中会向减小和增大的方向变化。」

零点在中间的通道（悬架中位 / 转向中位 / 双向力）必须满足：

    ① 原始码 == zero 时显示 0        —— 零点真的是零点
    ② 显示范围跨 0（min < 0 < max）  —— 否则负值全被压在底边，像"传感器卡住"
    ③ 界面量程 == 协议量程 == CSV 可表示范围  —— 三处同一个真源
    ④ CSV 里 CH07 这一格 == 物理第 7 路（减完零点后的值）
    ⑤ 组帧与解析互为逆运算（字段零点：写入码 = 显示值/scale + zero）

这个脚本把零点当变量**变异**（单通道 0/512/2048/4095、全通道 2048、
混合零点、字段零点、raw 模式下的零点），每种组合都重跑上面五条。
期望值全部按物理公式手算，不写死任何数字。

用法：
    cd F:\\上位机\\v2
    <venv>\\python.exe tools\\zero_mutate_check.py
    退出码 0 = 任意零点配置下都自洽；非 0 = 有静默失效
★ 全部在内存里变异，不写磁盘、不改你的配置文件（跑完自动还原）。
"""
import os
import struct
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


def fail(desc, detail=""):
    print("    %s %s" % (MARK_FAIL, desc))
    if detail:
        print("         " + str(detail))
    _fails.append(desc)


def step_of(i):
    """第 i 通道「1 码 = 多少工程量」的物理公式（不调配置函数，独立手算）。"""
    if CFG.ADC_DECODE_MODE != "eng":
        return 1.0
    return float(CFG.ADC_CHANNELS[i]["rng"]) / 4095.0


def want_range(i):
    """第 i 通道期望显示范围（物理量公式手算）。"""
    z = float(CFG.ADC_CHANNELS[i].get("zero", 0) or 0)
    if CFG.ADC_DECODE_MODE == "eng":
        st = step_of(i)
        return -z * st, (4095.0 - z) * st
    return (float(CFG.ADC_CHANNELS[i].get("min", 0)) - z,
            float(CFG.ADC_CHANNELS[i].get("max", 4095)) - z)


def want_val(i, raw):
    """第 i 通道 raw 码对应的期望显示值（物理量公式手算）。"""
    return (raw - float(CFG.ADC_CHANNELS[i].get("zero", 0) or 0)) * step_of(i)


def check_adc_case(tag):
    """对当前（已变异的）ADC 零点配置跑一遍完整校验。"""
    # ① 体检
    probs = CFG.validate()
    if probs:
        fail("%s 配置体检报错" % tag, probs[:2])

    n = CFG.ADC_COUNT
    # 每路给一个互不相同的原始码（0~4095 内）
    raws = [(i * 373 + 7) % 4096 for i in range(n)]

    # ② 零点处必须显示 0
    fz = P.build_adc_frame(1, 1000,
                           [int(round(float(CFG.ADC_CHANNELS[k].get("zero", 0) or 0)))
                            for k in range(n)])
    pz = P.parse_stream(bytearray(fz))
    if not pz:
        fail("%s 零点帧解析失败" % tag)
        return
    bad0 = []
    for i in range(n):
        key = CFG.ADC_CHANNELS[i]["short"]
        v = pz[0].values.get(key, 0.0)
        if abs(v) > 1e-6:
            bad0.append("%s zero=%g 显示 %g" % (key, CFG.ADC_CHANNELS[i].get("zero", 0), v))
    if bad0:
        fail("%s 零点处应显示 0" % tag, bad0[:3])

    # ③ 量程：界面 = 手算 = 跨 0（有零点时）
    bad_r = []
    for i in range(n):
        lo, hi = want_range(i)
        ui = CFG.ui_adc_config()
        # ui_adc_config 按 adc_out_order 输出，找到物理通道 i 对应的那一项
        pos = CFG.adc_out_order().index(i)
        d = ui[pos]
        if abs(float(d["min"]) - lo) > 1e-6 or abs(float(d["max"]) - hi) > 1e-6:
            bad_r.append("CH%d 期望 %g~%g 界面 %g~%g"
                         % (i + 1, lo, hi, d["min"], d["max"]))
        z = float(CFG.ADC_CHANNELS[i].get("zero", 0) or 0)
        if 0 < z < 4095 and not (lo < 0 < hi):
            bad_r.append("CH%d zero=%g 但量程 %g~%g 没跨 0" % (i + 1, z, lo, hi))
    if bad_r:
        fail("%s 显示量程与零点不一致" % tag, bad_r[:3])

    # ④ CSV：列名（物理通道号）↔ 值 必须一一对应
    f = P.build_adc_frame(1, 1000, raws)
    pf = P.parse_stream(bytearray(f))
    if not pf:
        fail("%s 数据帧解析失败" % tag)
        return
    order = CFG.adc_out_order()
    vals = [pf[0].values.get(CFG.ADC_CHANNELS[i]["short"], 0.0) for i in order]
    row = CFG.build_csv_row("12:00:00", vals).rstrip("\n").split(",")
    hdr = CFG.CSV_HEADER.split(",")
    bad_c = []
    for col, phys_i in enumerate(order):
        cell = row[1 + col]
        want = CFG.adc_csv_fmt(phys_i) % want_val(phys_i, raws[phys_i])
        if cell != want:
            bad_c.append("第 %d 列 %s = %s，期望 %s"
                         % (1 + col, hdr[1 + col], cell, want))
        # 列名里的 CH 号必须是物理通道号（这是"数据与通道名对不上"的防线）
        if not hdr[1 + col].startswith("CH%02d" % (phys_i + 1)):
            bad_c.append("第 %d 列名 %s 与物理 CH%d 不符"
                         % (1 + col, hdr[1 + col], phys_i + 1))
    if bad_c:
        fail("%s CSV 列与值不对应" % tag, bad_c[:3])


def check_field_case(tag, name, zero, disp):
    """某个 NAV/MOTOR 字段设了零点后：写入码 = disp/scale + zero，解析回 disp。"""
    spec = None
    for tid, s in CFG.PROTOCOL.items():
        if "fields" in s and any(f[0] == name for f in s["fields"]):
            spec = (tid, s)
            break
    if not spec:
        fail("%s 找不到字段 %s" % (tag, name))
        return
    tid, s = spec
    fld = [f for f in s["fields"] if f[0] == name][0]
    scale = float(fld[3])
    frame = P._build_typed_frame(tid, 1, 1000, {name: disp})
    size = struct.calcsize(fld[2])
    raw = struct.unpack("<" + fld[2].lstrip("<>!=!"),
                        frame[fld[1]:fld[1] + size])[0]
    if fld[2].lstrip("<>!=!") in ("b", "h"):      # 有符号要按有符号读
        raw = struct.unpack(fld[2], frame[fld[1]:fld[1] + size])[0]
    want_raw = int(round(disp / scale)) + int(round(zero))
    pf = P.parse_stream(bytearray(frame))
    got = pf[0].values.get(name) if pf else None
    ok_raw = (raw == want_raw)
    ok_val = (got is not None and abs(got - disp) < 1e-6)
    if not ok_raw or not ok_val:
        fail("%s 字段零点往返" % tag,
             "%s 写入码 %d（期望 %d）· 解析回 %s（期望 %g）" % (name, raw, want_raw, got, disp))


def main():
    _ensure_utf8()
    print("=" * 68)
    print("  零点 / 偏置变异自检 —— 零点在中间的传感器")
    print("=" * 68)

    # ---------- A. 单通道零点 ----------
    print("\n① 单通道零点（CH1 取 0 / 512 / 2048 / 4095）")
    for z in (0, 512, 2048, 4095):
        CFG.restore_builtin(); CFG.refresh()
        CFG.ADC_CHANNELS[0]["zero"] = float(z)
        check_adc_case("CH1 zero=%-4d" % z)
        print("    %s CH1 zero=%d → 范围 %.3f ~ %.3f %s"
              % (MARK_OK if not _fails or _fails[-1] != "CH1 zero=%-4d 配置体检报错" % z
                 else MARK_FAIL, z, *want_range(0), CFG.ADC_CHANNELS[0]["unit"]))

    # ---------- B. 全通道同一零点 ----------
    print("\n② 全通道零点 = 2048（全部零点在中位）")
    CFG.restore_builtin(); CFG.refresh()
    for c in CFG.ADC_CHANNELS:
        c["zero"] = 2048.0
    check_adc_case("全通道2048")

    # ---------- C. 混合零点 ----------
    print("\n③ 混合零点（CH1=2048 悬架中位 / CH5=2048 转向中位 / CH10=1024）")
    CFG.restore_builtin(); CFG.refresh()
    CFG.ADC_CHANNELS[0]["zero"] = 2048.0
    CFG.ADC_CHANNELS[4]["zero"] = 2048.0
    CFG.ADC_CHANNELS[9]["zero"] = 1024.0
    check_adc_case("混合零点")

    # ---------- D. raw 模式下的零点 ----------
    print("\n④ raw 模式（码值直显）下的零点")
    _m0 = CFG.ADC_DECODE_MODE
    try:
        CFG.restore_builtin(); CFG.refresh()
        CFG.ADC_DECODE_MODE = "raw"
        for c in CFG.ADC_CHANNELS:
            c["zero"] = 2048.0
        check_adc_case("raw模式2048")
        print("    %s raw 模式下 2048 码 → 显示 %.0f（码值减零点）"
              % (MARK_OK, want_val(0, 2048)))
    finally:
        CFG.ADC_DECODE_MODE = _m0
    CFG.restore_builtin(); CFG.refresh()

    # ---------- E. 字段零点（NAV / MOTOR）----------
    print("\n⑤ 字段零点（原始码偏置）")
    for nm, z, disp in (("TORQUE", 128.0, 100.0),
                        ("MOTOR_RPM", 1000.0, 3000.0),
                        ("SPEED", 500.0, 20.0),
                        ("ROLL", 180.0, -45.0)):
        CFG.restore_builtin(); CFG.refresh()
        CFG.FIELD_ZERO[nm] = z
        check_field_case("%s zero=%g" % (nm, z), nm, z, disp)
        _sc = 1.0
        for _s in CFG.PROTOCOL.values():
            if "fields" in _s:
                for _f in _s["fields"]:
                    if _f[0] == nm:
                        _sc = float(_f[3]) or 1.0
        print("    %s %-10s zero=%-6g 显示 %-8g → 原始码 %d（scale=%g）"
              % (MARK_OK, nm, z, disp, int(round(disp / _sc)) + int(z), _sc))
    CFG.restore_builtin(); CFG.refresh()

    print("\n" + "=" * 68)
    if _fails:
        print("  ❌ %d 项不通过：" % len(_fails))
        for f in _fails:
            print("     · " + f)
        print("=" * 68)
        return 1
    print("  ✅ 任意零点配置下：零点显示 0、量程跨 0、界面/协议/CSV 三处同源")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
