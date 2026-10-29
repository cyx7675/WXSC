# -*- coding: utf-8 -*-
"""
column_order_check.py —— ★「CSV 列顺序改了，数据和列名会不会错位」端到端自检 ★
==============================================================================
用户要求（2026-10-10）：
    「让我在 ADC 区能改通道顺序 / 量程，MOTOR 区和姿态区也一样；
      CSV 的列要跟着改，**不能出现数据与通道名称对不上**。」

现在三个地方都走同一个真源：
    界面卡片顺序 / CSV 列名顺序 / CSV 取值顺序  ← 全部来自 CFG.adc_out_order()
    MOTOR / 姿态的列顺序                        ← 第 4 区 fields 的书写顺序
    （字节布局只认偏移 off，与书写顺序无关，所以随便调顺序都不会动协议）

这个脚本用**端到端特征值法**把「列↔值」钉死，不走任何内部变量：
    1. 组一串真实帧（每路 ADC / 每个字段给一个彼此不同的值）
    2. 走 protocol.parse_stream 解析
    3. 完全按 launcher_web.py 的取值方式（adc_out_order() / fields 顺序）取列表
    4. 走 CFG.build_csv_row 拼成一行
    5. 断言：列名 "CH07_xxx" 那一格里的数 == 物理第 7 路的值
              列名 "torque"   那一格里的数 == TORQUE 字段的值
    只要有任何一处顺序不跟着配置走，第 5 步立刻对不上。

再拿它做**变异**：打乱 ADC_ORDER、打乱 MOTOR / NAV 的 fields 书写顺序，
每种组合都必须仍然对得上（列顺序应当跟着变，但对应关系永不错位）。

用法：
    cd F:\\上位机\\v2
    <venv>\\python.exe tools\\column_order_check.py
    退出码 0 = 任意顺序下 CSV 列名与数据都严格对应；非 0 = 有错位
★ 全部在内存里变异，不写磁盘、不改你的配置文件（跑完自动还原）。
"""
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import telemetry_config as CFG          # noqa: E402
import protocol as P                    # noqa: E402

MARK_OK = "[OK]"
MARK_FAIL = "[FAIL]"


def _ensure_utf8():
    try:
        if "utf" not in (sys.stdout.encoding or "").lower():
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _adc_frame_values():
    """组一帧 ADC 并解析，返回 {物理通道索引: 显示值}。"""
    raws = [(i + 1) * 200 for i in range(CFG.ADC_COUNT)]     # 200,400,…,2000 互不相同
    frame = P.build_adc_frame(1, 1000, raws)
    got = P.parse_stream(bytearray(frame))
    if not got:
        raise RuntimeError("ADC 帧没解析出来")
    v = got[0].values
    return {i: float(v[CFG.ADC_CHANNELS[i]["short"]]) for i in range(CFG.ADC_COUNT)}


def _typed_values():
    """{信号名: 显示值}：给每个字段一个互不相同的值，组帧→解析后返回实际值。

    用解析回来的值当期望值，是为了让 clamp / scale 的影响天然一致
    （比如 SPEED 是 int16，喂 106 会被夹到 32767 → 显示 32.767）。
    """
    out = {}
    for tid, names in CFG.csv_groups():
        disp = {nm: float(100 + k) for k, nm in enumerate(names)}
        got = P.parse_stream(bytearray(P._build_typed_frame(tid, 1, 1000, disp)))
        if not got:
            raise RuntimeError("0x%02X 包没解析出来" % tid)
        for nm in names:
            out[nm] = float(got[0].values[nm])
    return out


def _check_once(label):
    """跑一遍完整的「组帧 → 解析 → 取值 → 拼行 → 比对列名」。返回问题列表。"""
    CFG.refresh()                       # 顺序变了要重算 CSV_HEADER / 格式表
    adc_val = _adc_frame_values()
    typed_val = _typed_values()

    # ★ 完全照抄 launcher_web.py 的取值方式
    adc_list = [adc_val[i] for i in CFG.adc_out_order()]
    groups = []
    for _tid, names in CFG.csv_groups():
        groups.append([typed_val[nm] for nm in names])

    row = CFG.build_csv_row("12:00:00.000", adc_list, *groups).strip()
    header = CFG.CSV_HEADER.split(",")
    cells = row.split(",")

    bad = []
    if len(header) != len(cells):
        bad.append("表头 %d 列但数据行 %d 格（列数对不上）" % (len(header), len(cells)))
        return bad

    # ---- ADC：列名里的 CH 号是【物理通道号】，值必须等于该物理通道 ----
    phys_of_col = {}
    for idx, col in enumerate(header):
        if col.startswith("CH") and "_" in col:
            try:
                phys_of_col[idx] = int(col[2:col.index("_")]) - 1
            except ValueError:
                pass
    for idx, phys in phys_of_col.items():
        want = CFG.adc_csv_fmt(phys) % adc_val[phys]
        if cells[idx] != want:
            bad.append("列 %s 的值是 %s，但物理第 %d 路的值是 %s"
                       % (header[idx], cells[idx], phys + 1, want))

    # ---- NAV / MOTOR：列名就是信号名小写 ----
    for idx, col in enumerate(header):
        nm = col.upper()
        if nm in typed_val:
            spec = None
            for _tid, names in CFG.csv_groups():
                if nm in names:
                    spec = CFG.PROTOCOL[_tid]
                    break
            fld = next(f for f in spec["fields"] if f[0] == nm)
            want = CFG._fmt_for(fld) % typed_val[nm]
            if cells[idx] != want:
                bad.append("列 %s 的值是 %s，但 %s 的值应该是 %s"
                           % (col, cells[idx], nm, want))

    # ---- 三处顺序必须同源：界面卡片 / CSV 列名 / CSV 取值 ----
    ui_short = [c["short"] for c in CFG.ui_adc_config()]
    csv_short = ["CH%d" % (phys + 1) for _i, phys in sorted(phys_of_col.items())]
    if ui_short != csv_short:
        bad.append("界面卡片顺序 %s 与 CSV 列顺序 %s 不一致（必须都走 adc_out_order()）"
                   % (ui_short, csv_short))
    return bad


def _variants():
    """[(说明, 应用函数, 还原函数)] —— 各种顺序组合。"""
    rng = random.Random(20261010)
    n = CFG.ADC_COUNT
    out = []

    mot_orig = [tuple(f) for f in CFG.PROTOCOL[0x02]["fields"]]
    nav_orig = [tuple(f) for f in CFG.PROTOCOL[0x01]["fields"]]
    ord_orig = CFG.ADC_ORDER

    def _no_op():
        pass

    def _restore_all():
        CFG.ADC_ORDER = ord_orig
        CFG.PROTOCOL[0x02]["fields"] = [list(f) for f in mot_orig]
        CFG.PROTOCOL[0x01]["fields"] = [list(f) for f in nav_orig]
        CFG.refresh()

    out.append(("当前配置（扭矩在前）", _no_op, _restore_all))

    # ---- 只打乱 ADC 显示顺序 ----
    for tag in ("逆序", "随机1", "随机2"):
        seq = list(range(1, n + 1))
        seq = seq[::-1] if tag == "逆序" else rng.sample(seq, n)

        def _apply(seq=seq):
            CFG.ADC_ORDER = list(seq)
        out.append(("ADC_ORDER %s" % tag, _apply, _restore_all))

    # ---- 只打乱 MOTOR 的书写顺序（字节布局不动，只动列顺序）----
    for tag in ("逆序", "随机"):
        perm = mot_orig[::-1] if tag == "逆序" else rng.sample(mot_orig, len(mot_orig))

        def _apply(perm=perm):
            CFG.PROTOCOL[0x02]["fields"] = [list(f) for f in perm]
        out.append(("MOTOR 列顺序 %s" % tag, _apply, _restore_all))

    # ---- 只打乱 NAV 的书写顺序 ----
    perm = nav_orig[::-1]

    def _apply(perm=perm):
        CFG.PROTOCOL[0x01]["fields"] = [list(f) for f in perm]
    out.append(("NAV 列顺序 逆序", _apply, _restore_all))

    # ---- 一起乱（最狠的一种）----
    def _apply_all():
        CFG.ADC_ORDER = rng.sample(list(range(1, n + 1)), n)
        CFG.PROTOCOL[0x02]["fields"] = [list(f) for f in rng.sample(mot_orig, len(mot_orig))]
        CFG.PROTOCOL[0x01]["fields"] = [list(f) for f in rng.sample(nav_orig, len(nav_orig))]
    out.append(("ADC+MOTOR+NAV 全乱", _apply_all, _restore_all))

    return out


def main():
    _ensure_utf8()
    print("=" * 72)
    print("  CSV 列顺序改了，数据与通道名称会不会错位？（端到端）")
    print("=" * 72)

    total_bad = []
    for label, apply_fn, restore_fn in _variants():
        apply_fn()
        try:
            bad = _check_once(label)
        except Exception as e:                      # 组帧/解析炸了也算失败
            bad = ["异常：%r" % (e,)]
        ok = not bad
        head = CFG.CSV_HEADER.split(",")
        print("  %s %-22s 列数 %d  首列 %s"
              % (MARK_OK if ok else MARK_FAIL, label, len(head),
                 head[1] if len(head) > 1 else "?"))
        for b in bad[:4]:
            print("         ✗ " + b)
        if not ok:
            total_bad.append(label)
        restore_fn()

    # 顺带确认：改 ADC_ORDER 确实会让 CSV 列顺序变（开关不是摆设）
    _check_once("x")
    before = CFG.CSV_HEADER.split(",")[1:1 + CFG.ADC_COUNT]
    CFG.ADC_ORDER = list(range(CFG.ADC_COUNT, 0, -1))
    CFG.refresh()
    after = CFG.CSV_HEADER.split(",")[1:1 + CFG.ADC_COUNT]
    CFG.ADC_ORDER = None
    CFG.refresh()
    switched = before != after
    print("\n  %s 改 ADC_ORDER 后 CSV 列顺序确实变了（%s → %s）"
          % (MARK_OK if switched else MARK_FAIL, before[0], after[0]))
    if not switched:
        total_bad.append("ADC_ORDER 开关无效")

    print("\n" + "=" * 72)
    if total_bad:
        print("  %s 以下顺序组合出现「列名 ↔ 数据」错位：" % MARK_FAIL)
        for b in total_bad:
            print("    · " + b)
        print("=" * 72)
        return 1
    print("  %s 所有顺序组合下：CSV 列名与数据严格一一对应，界面/CSV/取值三者同源。"
          % MARK_OK)
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
