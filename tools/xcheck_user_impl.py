#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交叉验证：用户提供的参考实现 decode_adc_log.py  vs  上位机 protocol.py/telemetry_config.py

跑法：python tools/xcheck_user_impl.py
只读操作，不改任何业务代码。
"""
import os
import sys
import random
import importlib.util

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import telemetry_config as CFG      # noqa: E402
import protocol as P                # noqa: E402

USR_PATH = os.path.join(HERE, "_user_decode_adc_log.py")
_spec = importlib.util.spec_from_file_location("usr_impl", USR_PATH)
usr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(usr)

random.seed(20261005)
ok = fail = 0


def check(name, cond, note=""):
    global ok, fail
    if cond:
        ok += 1
        print("  ✅ " + name)
    else:
        fail += 1
        print("  ❌ " + name + ("  " + note if note else ""))


print("=" * 70)
print("  A) CRC-16/MODBUS 算法本身（查表 vs 上位机实现）")
print("=" * 70)
bad = []
for t in range(3000):
    n = random.randint(0, 40)
    b = bytes(random.randrange(256) for _ in range(n))
    if usr.crc16(b) != P.crc16(b):
        bad.append(b.hex())
check("3000 组随机数据 CRC 完全一致", not bad, "首个不一致: " + (bad[0] if bad else ""))
# 查重71922 那种 canonical 测试向量
check("标准向量 '123456789' -> 0x4B37",
      usr.crc16(b"123456789") == 0x4B37 == P.crc16(b"123456789"),
      "usr=0x%04X me=0x%04X" % (usr.crc16(b"123456789"), P.crc16(b"123456789")))

print()
print("=" * 70)
print("  B) 帧结构常量（字节数 / 偏移 / 覆盖范围 / 帧尾）")
print("=" * 70)
p0 = CFG.PROTOCOL[0x00]
check("ADC 帧长 27B 一致", usr.LEN[0] == 27 == p0["total"],
      "usr=%s me=%s" % (usr.LEN[0], p0["total"]))
check("NAV 帧长 20B 一致", usr.LEN[1] == 20 == CFG.PROTOCOL[0x01]["total"])
check("MOTOR 帧长 23B 一致", usr.LEN[2] == 23 == CFG.PROTOCOL[0x02]["total"])
check("ADC 打包区偏移=8 长度=15 一致",
      p0["packed_off"] == 8 and p0["packed_len"] == 15,
      "off=%s len=%s" % (p0.get("packed_off"), p0.get("packed_len")))
check("CRC 覆盖 [0:帧长-4]（= header..payload）一致",
      CFG.CRC_RANGE == "header_to_payload")
check("帧头线上字节 55 AA 一致", CFG.HEADER == b"\x55\xaa",
      CFG.HEADER.hex(" "))
check("帧尾线上字节 AA 55 一致", CFG.TAIL_CUSTOM == (0xAA, 0x55)
      and CFG.TAIL_MODE == "reversed")

print()
print("=" * 70)
print("  C) 12bit 解包：你的 unpack12() 到底等于 msb 还是 lsb？")
print("=" * 70)
samples = [bytes(random.randrange(256) for _ in range(15)) for _ in range(5000)]
m_msb = m_lsb = 0
P.ADC_12BIT_ORDER = "msb"
for b in samples:
    if usr.unpack12(b) == P.unpack_12bit(b, CFG.ADC_COUNT):
        m_msb += 1
P.ADC_12BIT_ORDER = "lsb"
for b in samples:
    if usr.unpack12(b) == P.unpack_12bit(b, CFG.ADC_COUNT):
        m_lsb += 1
P.ADC_12BIT_ORDER = CFG.ADC_12BIT_ORDER          # 还原，别污染
check("你的 unpack12 == 上位机 msb（5000 组随机）", m_msb == 5000,
      "命中 %d/5000" % m_msb)
check("你的 unpack12 != 上位机 lsb", m_lsb == 0, "误命中 %d/5000" % m_lsb)

# 逐 bit 手写一组字节，把结论摊开给人看
demo = bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0,
              0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77])
u = usr.unpack12(demo)
P.ADC_12BIT_ORDER = "msb"
msb_v = P.unpack_12bit(demo, CFG.ADC_COUNT)
P.ADC_12BIT_ORDER = "lsb"
lsb_v = P.unpack_12bit(demo, CFG.ADC_COUNT)
P.ADC_12BIT_ORDER = CFG.ADC_12BIT_ORDER          # 还原
print("\n  用 15 字节 %s 举例：" % demo.hex(" ").upper())
print("    %-6s %-10s %-10s %-10s" % ("通道", "你的脚本", "上位机msb", "上位机lsb"))
for i in range(10):
    tag = "" if u[i] == msb_v[i] else "   <-- 不一致"
    print("    CH%-4d %-10d %-10d %-10d%s"
          % (i + 1, u[i], msb_v[i], lsb_v[i], tag))
print("    → 你的第 1 通道取的是 buf[0] 高 8 位 + buf[1] 高 4 位，")
print("      也就是「15 字节当大端整数的最高 12bit」= 上位机的 msb。")

print()
print("=" * 70)
print("  D) 端到端：上位机组帧 -> 喂给你的 parse() -> 值能不能对上")
print("=" * 70)
raw12 = [0, 1, 1234, 2047, 2048, 3000, 3999, 4095, 888, 12]
frame = bytearray(P.build_adc_frame(7, 123456, raw12))
check("上位机组出的帧长 = 27", len(frame) == 27, "实为 %d" % len(frame))
frames = usr.parse(frame)
check("你的 parse() 能通过 CRC 与帧尾校验", len(frames) == 1,
      "解析出 %d 帧" % len(frames))
if frames:
    _i, _t, fr = frames[0]
    got = usr.unpack12(fr[8:23])
    check("你的脚本解出的 10 路 == 组帧时喂进去的原始码", got == raw12,
          "喂入 %s\n        解出 %s" % (raw12, got))
    me = P.parse_stream(bytearray(frame))
    if me:
        _bad = []
        for _i in range(CFG.ADC_COUNT):
            _want = (raw12[_i] if CFG.ADC_DECODE_MODE != "eng"
                     else raw12[_i] * CFG.ADC_FULLSCALE[_i] / 4095.0)
            _got = me[0].values[CFG.ADC_CHANNELS[_i]["short"]]
            if abs(_got - _want) > 1e-6:
                _bad.append("CH%d 期望 %.4f 实得 %.4f" % (_i + 1, _want, _got))
        check("上位机自己解析回来的值也对（%s 模式）" % CFG.ADC_DECODE_MODE, not _bad)
        for _b in _bad:
            print("     ✗ " + _b)

print()
print("=" * 70)
print("  结果：%d 项通过，%d 项失败" % (ok, fail))
print("=" * 70)
sys.exit(1 if fail else 0)
