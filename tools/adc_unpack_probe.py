# -*- coding: utf-8 -*-
"""adc_unpack_probe.py -- 12bit 打包区「解包变体穷举器」（只读诊断，不改任何业务代码）

用途：拿到一帧真实 P0(ADC) 的 HEX，同时用 4 种常见的 12bit 打包方式解出
      10 个通道，直接和你的物理量对照，判断固件到底用的哪一种塞法。

用法：
  python tools/adc_unpack_probe.py --hex "55 AA 00 ... AA 55"
  python tools/adc_unpack_probe.py --hex "帧1" --hex "帧2"      # 对比两帧（静止 / 拉满）
  python tools/adc_unpack_probe.py --file sample_capture.bin    # 整包统计

输出：
  1) 帧体检：长度 / 包头 / 类型 / 序号 / 时间戳 / CRC / 包尾
  2) 打包区 15 字节，及两帧之间的字节差异（★判断传感器到底有没有动）
  3) 4 种解包变体 × 10 通道的 raw 码值 + 按当前配置换算后的工程量
"""
import sys, os, argparse, statistics as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ensure_utf8():
    try:
        if (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


_ensure_utf8()

import protocol as P                      # noqa: E402
import telemetry_config as CFG            # noqa: E402

N = CFG.ADC_COUNT
RNG = CFG.ADC_FULLSCALE
NAMES = [c["name"] for c in CFG.ADC_CHANNELS]


def unpack_variants(b: bytes):
    """返回 {变体名: [10 个 12bit 码值]}。b 必须是 15 字节打包区。"""
    le = int.from_bytes(b, "little")
    be = int.from_bytes(b, "big")
    bits = N * 12
    out = {}

    # V0 小端紧凑（当前配置 ADC_12BIT_ORDER="lsb"）：第 i 通道占最低位起的第 i 段
    out["V0 小端紧凑(当前)"] = [(le >> (12 * i)) & 0xFFF for i in range(N)]
    # V1 大端紧凑：与 V0 数值集合相同，仅通道顺序整体反转
    out["V1 大端紧凑"] = [(be >> (bits - 12 * (i + 1))) & 0xFFF for i in range(N)]

    # V2 组内大端 + 通道反转（每 3 字节装 2 通道，组内先放高 8 位，且组序反转）
    v2 = []
    for j in range(N // 2):
        k = j * 3
        v2.append(((b[k] << 4) | (b[k + 1] >> 4)) & 0xFFF)
        v2.append((((b[k + 1] & 0x0F) << 8) | b[k + 2]) & 0xFFF)
    out["V2 组内大端反转"] = list(reversed(v2))
    return out


def frame_report(fr):
    raw = fr.raw
    print("  长度 %d  包头 %s  类型 %d  序号 %d  时间戳 %d"
          % (len(raw), raw[0:2].hex().upper(), raw[2], raw[3],
             int.from_bytes(raw[4:8], "little")))
    crc_ok = P.crc16(raw[:-4]) == int.from_bytes(raw[-4:-2], "little")
    print("  CRC 域 %s  实算 %04X  ->  %s   包尾 %s"
          % (raw[-4:-2].hex().upper(), P.crc16(raw[:-4]),
             "通过" if crc_ok else "不通过", raw[-2:].hex().upper()))
    print("  打包区(15B) = " + " ".join("%02X" % x for x in raw[8:23]))
    return raw[8:23]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hex", action="append", default=[], help="一帧 27 字节 HEX，可重复传多帧")
    ap.add_argument("--file", help="原始二进制捕获文件")
    args = ap.parse_args()

    print("当前配置: ADC_DECODE_MODE=%s  ADC_12BIT_ORDER=%s  通道数=%d"
          % (CFG.ADC_DECODE_MODE, CFG.ADC_12BIT_ORDER, N))
    print("量程 rng = %s\n" % RNG)

    packeds = []
    for h in args.hex:
        bs = bytes.fromhex(h.replace(" ", "").replace(",", ""))
        frames = P.parse_stream(bytearray(bs))
        adc = [f for f in frames if f.type_id == 0x00]
        if not adc:
            print("[帧体检] 未解出 P0 帧（长度 %d，头 %s）" % (len(bs), bs[:2].hex().upper()))
            print("         可能是 CRC 不通过或长度不足 27 字节，帧会被解析器丢弃")
            continue
        print("[帧体检]")
        packeds.append(frame_report(adc[0]))
        print()

    if args.file:
        frames = P.parse_stream(bytearray(open(args.file, "rb").read()))
        adc = [f for f in frames if f.type_id == 0x00]
        print("[文件] %s  共 %d 帧 P0" % (args.file, len(adc)))
        if adc:
            packeds.append(adc[0].raw[8:23])
            # 统计模式：每种变体各通道 min/max/std
            print("\n  %-18s %-8s %6s %6s %8s %8s" % ("变体", "通道", "min", "max", "mean", "std"))
            for vname in unpack_variants(adc[0].raw[8:23]).keys():
                for i in range(N):
                    vals = [unpack_variants(f.raw[8:23])[vname][i] for f in adc]
                    print("  %-18s CH%-2d      %6d %6d %8.1f %8.1f"
                          % (vname, i + 1, min(vals), max(vals), st.mean(vals), st.pstdev(vals)))
        print()

    if not packeds:
        print("没有可分析的帧。请用 --hex 粘一帧 27 字节 HEX。")
        return

    # ---- 字节差异：定位"哪个字节真的在动" ----
    if len(packeds) >= 2:
        a, b = packeds[0], packeds[1]
        diff = [i for i in range(15) if a[i] != b[i]]
        print("[字节差异] 第 1 帧 vs 第 2 帧，变化的打包区字节下标(0-14): %s" % diff)
        print("           第1帧: " + " ".join("%02X" % x for x in a))
        print("           第2帧: " + " ".join("%02X" % x for x in b))
        print("           （无变化字节 = 该通道根本没采集到变化，先查硬件/接线）\n")

    # ---- 四种变体解包 ----
    for idx, pk in enumerate(packeds):
        print("===== 第 %d 帧 · 四种解包变体 =====" % (idx + 1))
        vs = unpack_variants(pk)
        hdr = "  %-18s" % "变体" + "".join("   CH%-3d" % (i + 1) for i in range(N))
        print(hdr)
        for vname, vals in vs.items():
            print("  %-18s" % vname + "".join("%7d" % v for v in vals))
        print("\n  -- 按当前配置换算后（工程量 = 码/4095×rng）--")
        print("  %-18s" % "变体" + "".join("   CH%-3d" % (i + 1) for i in range(N)))
        for vname, vals in vs.items():
            eng = [v / 4095.0 * RNG[i] for i, v in enumerate(vals)]
            print("  %-18s" % vname + "".join("%7.1f" % v for v in eng))
        print()

    print("通道对照表:")
    for i in range(N):
        print("  CH%-2d  %-12s rng=%-6g unit=%s" % (i + 1, NAMES[i], RNG[i], CFG.ADC_CHANNELS[i]["unit"]))


if __name__ == "__main__":
    main()
