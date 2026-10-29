#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""decode_frame.py —— 粘一串 HEX 帧，立刻看到它到底是什么。

用法：
    python tools/decode_frame.py "55 AA 00 74 4A BE 02 00 00 8F F9 ... AA 55"
    echo "55AA0074..." | python tools/decode_frame.py            # 管道也行
    python tools/decode_frame.py "...." --expect 008 FF9         # 带上你手算的期望值

输出：
  ① 帧长 / 帧头帧尾 / 类型 / 序号 / 时间戳 是否自洽
  ② CRC 校验结果（并对不过的情况给出"可能是抄错了几个字节"的提示）
  ③ ADC 包：数据区 hex digit 每 3 个一组的"手工读法" + 4 种解包顺序对照
  ④ NAV / MOTOR 包：按第 4 区字段表逐字段列出 原始值 → 显示值（×scale）
"""
import sys, os, re, struct

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)


def _ensure_utf8():
    try:
        if (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def parse_hex(s):
    s = re.sub(r"[^0-9A-Fa-f]", "", s)
    if len(s) % 2:
        raise ValueError("hex 字符数为奇数(%d)，多半抄漏/抄多了一位" % len(s))
    return bytes.fromhex(s)


def main():
    _ensure_utf8()
    # ⚠️ 必须把 "--expect" 后面的值也剔掉，否则 "008" 会被当成 HEX 拼进帧里
    argv = sys.argv[1:]
    if "--expect" in argv:
        i = argv.index("--expect")
        tail = argv[i + 1:]
        expect = []
        for x in tail:
            if re.fullmatch(r"[0-9A-Fa-f]{1,4}", x):
                expect.append(int(x, 16))
            else:
                break                      # 遇到非 hex 就认为期望值列表结束
        argv = argv[:i] + tail[len(expect):]
    else:
        expect = []

    args = [a for a in argv if not a.startswith("--")]
    if args:
        txt = " ".join(args)
    else:
        txt = sys.stdin.read()
    try:
        raw = parse_hex(txt)
    except ValueError as e:
        print("× 输入不是合法 HEX：%s" % e)
        return 1

    import telemetry_config as CFG
    import protocol as P

    print("帧长 = %d 字节" % len(raw))
    print("原始 = %s" % raw.hex(" ").upper())
    print()

    # ---- ① 信封自洽性 ----
    print("── ① 帧信封 ──────────────────────────")
    tid = raw[2] if len(raw) > 2 else -1
    spec = CFG.PROTOCOL.get(tid)
    if spec is None:
        print("  ⚠ 类型字节 0x%02X 不在协议表里（%s）" %
              (tid & 0xFF, [hex(k) for k in CFG.PROTOCOL]))
        print("    → 这多半不是帧头位置，或固件类型号变了")
    else:
        print("  类型 0x%02X = %s 包" % (tid, spec["name"]))
        print("  帧长 %d  %s 配置声明 %d" %
              (len(raw), "==" if len(raw) == spec["total"] else "!=", spec["total"]))
        if len(raw) != spec["total"]:
            print("    ⚠★ 长度不符！这帧 %d 字节而配置写 %d —— 差 %+d 字节。\n"
                  "      若固件真发 %dB，必须改第 4 区 PROTOCOL[%s]['total']，\n"
                  "      否则这类帧永远解不出来（CRC 与包尾都落在错误的位置上）。"
                  % (len(raw), spec["total"], len(raw) - spec["total"], len(raw), hex(tid)))
    print("  帧头 %s   帧尾 %s" % (raw[:2].hex(" ").upper(), raw[-2:].hex(" ").upper()))
    if len(raw) >= 4:
        print("  序号 %d" % raw[3])
    if len(raw) >= 8:
        ts = int.from_bytes(raw[4:8], "little")
        print("  时间戳 %d ms = %.1f s" % (ts, ts / 1000.0))
    print()

    # ---- ② CRC ----
    print("── ② CRC ─────────────────────────────")
    if len(raw) >= 4:
        crc_calc = P.crc16(raw[:len(raw) - 4])
        crc_rx_le = int.from_bytes(raw[len(raw) - 4:len(raw) - 2], "little")
        crc_rx_be = int.from_bytes(raw[len(raw) - 4:len(raw) - 2], "big")
        ok = (crc_calc == crc_rx_le)
        print("  覆盖 [0:%d]，帧里 CRC 字节 = %s" %
              (len(raw) - 4, raw[len(raw) - 4:len(raw) - 2].hex(" ").upper()))
        print("  计算值 = 0x%04X   （小端读=0x%04X / 大端读=0x%04X）" %
              (crc_calc, crc_rx_le, crc_rx_be))
        print("  结果   = %s" % ("OK PASS" if ok else "X  FAIL"))
        if not ok:
            print("  ⚠ CRC 不过 → 要么抄录有误差，要么帧长/覆盖范围不对。")
    print()

    if spec is None:
        return 0

    # ---- ③ ADC：12bit 解包 ----
    if "packed_off" in spec:
        n = spec["adc_n"]
        body = raw[spec["packed_off"]: spec["packed_off"] + spec["packed_len"]]
        print("── ③ 数据区（第 %d 字节起，%d 字节）────" % (spec["packed_off"] + 1, len(body)))
        print("  %s" % body.hex(" ").upper())
        digits = body.hex().upper()
        manual = [int(digits[i * 3:(i + 1) * 3], 16) for i in range(n)]
        print("  ★ 手工读法（把 hex 连起来，每 3 个字符 = 一路）:")
        print("     " + " | ".join(digits[i * 3:(i + 1) * 3] for i in range(n)))
        for i, v in enumerate(manual):
            rng = CFG.ADC_CHANNELS[i]["rng"]
            print("       CH%-2d = 0x%03X (%4d)  工程量=%.3f %s"
                  % (i + 1, v, v, v / 4095.0 * rng, CFG.ADC_CHANNELS[i]["unit"]))
        print()
        print("── ④ 四种解包顺序对照 ────────────────")
        print("  %-9s %s" % ("order", " ".join("CH%-2d" % (i + 1) for i in range(n))))
        for o in ("msb", "msb_swap", "lsb", "lsb_swap"):
            CFG.ADC_12BIT_ORDER = o
            v = P.unpack_12bit(body, n)
            mark = "  <== 与手工读法一致" if v == manual else ""
            flag = "  <== 你给的期望值" if expect and v[:len(expect)] == expect else ""
            print("  %-9s %s%s%s" % (o, "  ".join("0x%03X" % x for x in v), flag, mark))
        CFG.ADC_12BIT_ORDER = CFG._BUILTIN.get("adc12bitOrder", "msb")
        print()
        print("  -> 与手工读法一致的那一行就是正确答案。")
        return 0

    # ---- ③ NAV / MOTOR：按字段表逐个解 ----
    fields = spec.get("fields", [])
    print("── ③ 字段解码（第 4 区字段表）─────────")
    print("  %-12s %-5s %-6s %-10s %-12s %s" %
          ("信号", "偏移", "格式", "原始字节", "原始值", "显示值 = 原始 x scale"))
    for f in fields:
        name, off, fmt, scale = f[0], f[1], f[2], float(f[3])
        width = struct.calcsize(fmt)
        unit = CFG.UNITS.get(name, "")
        if off + width > len(raw):
            print("  %-12s %-5d %-6s  X 帧不够长（需要到 %d 字节）" % (name, off, fmt, off + width))
            continue
        chunk = raw[off:off + width]
        rawv = struct.unpack(fmt, chunk)[0]
        dec = f[4] if len(f) > 4 else None
        if dec is not None:
            disp = ("%%.%df" % dec) % (rawv * scale)
        else:
            disp = "%g" % (rawv * scale)
        warn = ""
        if fmt == "<H" and rawv > 32767:
            warn = ("   (!) 无符号解出 %d > 32767；若固件发的是有符号 i16，"
                    "真实值应为 %d —— 把格式改成 '<h' 试试" % (rawv, rawv - 65536))
        print("  %-12s %-5d %-6s %-10s %-12s %s %s%s" %
              (name, off, fmt, chunk.hex().upper(), rawv, disp, unit, warn))
    print()
    print("  ⚠ 上面「原始值」就是固件发来的整数。请核对：")
    print("     · 电机转速：量级对不对？（是 rpm 本身，还是 rpm×10 / ×0.1？）")
    print("     · 扭矩    ：是不是 0~150 N·m 量级？（1 字节最大只能到 255）")
    print("     哪个不对，就带着这串字节改第 4 区 PROTOCOL[0x02] 的 偏移/格式/scale。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
